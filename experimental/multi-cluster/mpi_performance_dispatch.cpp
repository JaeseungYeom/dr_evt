/******************************************************************************
 * Copyright 2023 Lawrence Livermore National Security, LLC
 * SPDX-License-Identifier: MIT
 ******************************************************************************/

/**
 * Native MPI version of grpc_performance_dispatch.py.
 *
 * Rank zero reads arrivals and selects a system.  Every other rank owns one
 * independent DR_EVT Simulation.  Only small command/result objects cross
 * MPI; Ser20 never serializes Simulation itself.
 */

#include "dr_evt_config.hpp"
#include "sim/sim.hpp"
#include "utils/state_io_ser20.hpp"

#define OMPI_SKIP_MPICXX 1
#define MPICH_SKIP_MPICXX 1
#include <mpi.h>

#include <ser20/types/string.hpp>
#include <ser20/types/vector.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <span>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

#if DR_EVT_LEGACY_QUEUE_INPUT
constexpr const char *kDefaultQueue = "pbatch";
constexpr const char *kQueueField = "queue";
#else
constexpr const char *kDefaultQueue = "1";
constexpr const char *kQueueField = "q_id";
#endif

constexpr int kSizeTag = 100;
constexpr int kPayloadTag = 101;

enum class Operation : std::uint8_t { Snapshot, Append, Finish };

struct JobMessage {
  double submit_time = 0.0;
  std::uint32_t num_nodes = 0;
  std::string queue;
  double limit_time = 0.0;

  template <class Archive> void serialize(Archive &archive) {
    archive(submit_time, num_nodes, queue, limit_time);
  }
};

struct Request {
  Operation operation = Operation::Snapshot;
  double target_time = 0.0;
  double prediction_utilization = 1.0;
  JobMessage job;

  template <class Archive> void serialize(Archive &archive) {
    archive(operation, target_time, prediction_utilization, job);
  }
};

struct ReleaseMessage {
  double time = 0.0;
  std::uint32_t nodes_released = 0;

  template <class Archive> void serialize(Archive &archive) {
    archive(time, nodes_released);
  }
};

struct WindowMessage {
  double current_time = 0.0;
  std::uint32_t available_nodes = 0;
  double shadow_time = -1.0;
  std::vector<ReleaseMessage> releases;

  template <class Archive> void serialize(Archive &archive) {
    archive(current_time, available_nodes, shadow_time, releases);
  }
};

struct StatisticsMessage {
  std::uint64_t jobs_submitted = 0;
  std::uint64_t jobs_completed = 0;
  double makespan = 0.0;

  template <class Archive> void serialize(Archive &archive) {
    archive(jobs_submitted, jobs_completed, makespan);
  }
};

struct Response {
  bool ok = true;
  std::string error;
  WindowMessage window;
  double prediction_horizon = 0.0;
  std::uint64_t job_idx = 0;
  StatisticsMessage statistics;

  template <class Archive> void serialize(Archive &archive) {
    archive(ok, error, window, prediction_horizon, job_idx, statistics);
  }
};

template <typename T> void send_serialized(const T &value, int rank) {
  std::vector<char> bytes;
  const auto serialized_size = dr_evt::serialize_binary(value, bytes);
  if (serialized_size != bytes.size())
    throw std::runtime_error("Ser20 reported an inconsistent message size");
  const auto size = static_cast<std::uint64_t>(bytes.size());
  if (size > static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
    throw std::length_error("serialized MPI message exceeds INT_MAX bytes");
  }
  MPI_Send(&size, 1, MPI_UINT64_T, rank, kSizeTag, MPI_COMM_WORLD);
  MPI_Send(bytes.data(), static_cast<int>(bytes.size()), MPI_BYTE, rank,
           kPayloadTag, MPI_COMM_WORLD);
}

template <typename T> T receive_serialized(int rank) {
  std::uint64_t size = 0;
  MPI_Recv(&size, 1, MPI_UINT64_T, rank, kSizeTag, MPI_COMM_WORLD,
           MPI_STATUS_IGNORE);
  if (size > static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
    throw std::length_error("serialized MPI message exceeds INT_MAX bytes");
  }
  std::vector<char> bytes(static_cast<std::size_t>(size));
  MPI_Recv(bytes.data(), static_cast<int>(bytes.size()), MPI_BYTE, rank,
           kPayloadTag, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  T value;
  dr_evt::deserialize_binary(value, bytes);
  return value;
}

struct Options {
  std::string jobs;
  std::string performance_table;
  std::optional<std::string> output;
  std::vector<std::string> system_ids;
  std::vector<std::uint32_t> system_nodes;
  std::uint32_t total_nodes = 100;
  double prediction_utilization = 1.0;
};

[[noreturn]] void usage(const char *program, const std::string &error = {}) {
  if (!error.empty())
    std::cerr << "error: " << error << "\n\n";
  std::cerr
      << "Usage: mpirun -np <systems+1> " << program << " OPTIONS\n"
      << "  --jobs PATH\n"
      << "  --performance-table PATH\n"
      << "  --system-id NAME          repeat once per worker rank\n"
      << "  --system-nodes COUNT      optional; repeat once per worker rank\n"
      << "  --total-nodes COUNT       default capacity (default: 100)\n"
      << "  --prediction-utilization U  value in [0,1] (default: 1)\n"
      << "  --output PATH             decision CSV (default: stdout)\n";
  throw std::invalid_argument(error.empty() ? "help requested" : error);
}

std::string option_value(int &index, int argc, char **argv,
                         const std::string &name) {
  if (++index >= argc)
    usage(argv[0], name + " requires a value");
  return argv[index];
}

Options parse_options(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--jobs")
      options.jobs = option_value(i, argc, argv, arg);
    else if (arg == "--performance-table")
      options.performance_table = option_value(i, argc, argv, arg);
    else if (arg == "--system-id")
      options.system_ids.push_back(option_value(i, argc, argv, arg));
    else if (arg == "--system-nodes")
      options.system_nodes.push_back(static_cast<std::uint32_t>(
          std::stoul(option_value(i, argc, argv, arg))));
    else if (arg == "--total-nodes")
      options.total_nodes = static_cast<std::uint32_t>(
          std::stoul(option_value(i, argc, argv, arg)));
    else if (arg == "--prediction-utilization")
      options.prediction_utilization =
          std::stod(option_value(i, argc, argv, arg));
    else if (arg == "--output")
      options.output = option_value(i, argc, argv, arg);
    else if (arg == "--help" || arg == "-h")
      usage(argv[0]);
    else
      usage(argv[0], "unknown option: " + arg);
  }
  if (options.jobs.empty() || options.performance_table.empty())
    usage(argv[0], "--jobs and --performance-table are required");
  if (options.total_nodes == 0)
    usage(argv[0], "--total-nodes must be positive");
  if (!std::isfinite(options.prediction_utilization) ||
      options.prediction_utilization < 0.0 ||
      options.prediction_utilization > 1.0)
    usage(argv[0], "--prediction-utilization must be in [0,1]");
  return options;
}

std::vector<std::string> split_csv(const std::string &line) {
  std::vector<std::string> fields;
  std::string field;
  bool quoted = false;
  for (std::size_t i = 0; i < line.size(); ++i) {
    const char c = line[i];
    if (c == '"') {
      if (quoted && i + 1 < line.size() && line[i + 1] == '"') {
        field.push_back('"');
        ++i;
      } else {
        quoted = !quoted;
      }
    } else if (c == ',' && !quoted) {
      fields.push_back(field);
      field.clear();
    } else {
      field.push_back(c);
    }
  }
  if (quoted)
    throw std::runtime_error("unterminated quoted CSV field");
  fields.push_back(field);
  return fields;
}

using Header = std::map<std::string, std::size_t>;

Header make_header(const std::vector<std::string> &fields) {
  Header header;
  for (std::size_t i = 0; i < fields.size(); ++i)
    header.emplace(fields[i], i);
  return header;
}

const std::string &field(const std::vector<std::string> &row,
                         const Header &header, const std::string &name) {
  const auto found = header.find(name);
  if (found == header.end() || found->second >= row.size())
    throw std::runtime_error("missing CSV field: " + name);
  return row[found->second];
}

std::string optional_field(const std::vector<std::string> &row,
                           const Header &header, const std::string &name,
                           std::string fallback) {
  const auto found = header.find(name);
  if (found == header.end() || found->second >= row.size() ||
      row[found->second].empty())
    return fallback;
  return row[found->second];
}

struct Job {
  std::string id;
  double submit_time;
  std::uint32_t num_nodes;
  std::string queue;
  double limit_time;
};

std::vector<Job> read_jobs(const std::string &path) {
  std::ifstream stream(path);
  if (!stream)
    throw std::runtime_error("cannot open jobs file: " + path);
  std::string line;
  if (!std::getline(stream, line))
    throw std::runtime_error("jobs file is empty: " + path);
  const Header header = make_header(split_csv(line));
  for (const auto *name : {"job_submit_time", "num_nodes", "time_limit"})
    if (!header.contains(name))
      throw std::runtime_error(path + " must contain " + name);

  std::vector<Job> jobs;
  while (std::getline(stream, line)) {
    if (line.empty())
      continue;
    const auto row = split_csv(line);
    Job job{
        optional_field(row, header, "job_id", std::to_string(jobs.size())),
        std::stod(field(row, header, "job_submit_time")),
        static_cast<std::uint32_t>(std::stoul(field(row, header, "num_nodes"))),
        optional_field(row, header, kQueueField, kDefaultQueue),
        std::stod(field(row, header, "time_limit"))};
    if (job.num_nodes == 0 || job.limit_time <= 0.0)
      throw std::runtime_error(path + ": job " + job.id +
                               " has non-positive size");
    if (!jobs.empty() && jobs.back().submit_time > job.submit_time)
      throw std::runtime_error(path +
                               ": jobs must be sorted by job_submit_time");
    jobs.push_back(std::move(job));
  }
  return jobs;
}

struct Profile {
  std::string id;
  double num_nodes;
  double time_limit;
  std::vector<double> performance;
};

std::vector<Profile> read_profiles(const std::string &path,
                                   const std::vector<std::string> &systems) {
  std::ifstream stream(path);
  if (!stream)
    throw std::runtime_error("cannot open performance table: " + path);
  std::string line;
  if (!std::getline(stream, line))
    throw std::runtime_error("performance table is empty: " + path);
  const Header header = make_header(split_csv(line));
  for (const auto &name : systems)
    if (!header.contains(name))
      throw std::runtime_error(path + " must contain system column " + name);

  std::vector<Profile> profiles;
  while (std::getline(stream, line)) {
    if (line.empty())
      continue;
    const auto row = split_csv(line);
    Profile profile{field(row, header, "profile_id"),
                    std::stod(field(row, header, "num_nodes")),
                    std::stod(field(row, header, "time_limit")),
                    {}};
    for (const auto &system : systems) {
      const double value = std::stod(field(row, header, system));
      if (!std::isfinite(value) || value <= 0.0)
        throw std::runtime_error(path + ": profile " + profile.id +
                                 " has invalid performance");
      profile.performance.push_back(value);
    }
    if (profile.id.empty() || profile.num_nodes <= 0.0 ||
        profile.time_limit <= 0.0)
      throw std::runtime_error(path + " has an invalid profile");
    profiles.push_back(std::move(profile));
  }
  if (profiles.empty())
    throw std::runtime_error("performance table is empty: " + path);
  return profiles;
}

const Profile &nearest_profile(const Job &job,
                               const std::vector<Profile> &profiles) {
  const auto node_bounds = std::minmax_element(
      profiles.begin(), profiles.end(),
      [](const auto &a, const auto &b) { return a.num_nodes < b.num_nodes; });
  const auto time_bounds = std::minmax_element(
      profiles.begin(), profiles.end(),
      [](const auto &a, const auto &b) { return a.time_limit < b.time_limit; });
  const double node_range =
      node_bounds.second->num_nodes - node_bounds.first->num_nodes;
  const double time_range =
      time_bounds.second->time_limit - time_bounds.first->time_limit;
  const double node_scale = node_range == 0.0 ? 1.0 : node_range;
  const double time_scale = time_range == 0.0 ? 1.0 : time_range;
  return *std::min_element(
      profiles.begin(), profiles.end(), [&](const auto &a, const auto &b) {
        const auto distance = [&](const Profile &profile) {
          const double dn = (job.num_nodes - profile.num_nodes) / node_scale;
          const double dt = (job.limit_time - profile.time_limit) / time_scale;
          return dn * dn + dt * dt;
        };
        return distance(a) < distance(b);
      });
}

double estimate_release_wait(const WindowMessage &window,
                             std::uint32_t required_nodes) {
  auto available = window.available_nodes;
  if (available >= required_nodes)
    return 0.0;
  for (const auto &release : window.releases) {
    available += release.nodes_released;
    if (available >= required_nodes)
      return std::max(0.0, release.time - window.current_time);
  }
  return std::numeric_limits<double>::infinity();
}

double estimate_wait(const WindowMessage &window, std::uint32_t required_nodes,
                     double runtime, double prediction_horizon) {
  const bool has_waiting_head = window.shadow_time > window.current_time;
  const bool can_start_now = window.available_nodes >= required_nodes;
  const bool can_backfill_now =
      can_start_now &&
      (!has_waiting_head || window.current_time + runtime < window.shadow_time);
  if (can_backfill_now)
    return 0.0;
  if (!has_waiting_head)
    return estimate_release_wait(window, required_nodes);
  return window.shadow_time - window.current_time + prediction_horizon;
}

struct Choice {
  std::size_t index;
  double relative_performance;
  double estimated_wait;
  double estimated_runtime;
  double predicted_turnaround;
};

Choice choose_system(const Job &job, const Profile &profile,
                     const std::vector<std::uint32_t> &capacities,
                     const std::vector<Response> &snapshots) {
  std::optional<Choice> best;
  for (std::size_t i = 0; i < capacities.size(); ++i) {
    if (job.num_nodes > capacities[i])
      continue;
    const double runtime = job.limit_time / profile.performance[i];
    const double wait = estimate_wait(snapshots[i].window, job.num_nodes,
                                      runtime, snapshots[i].prediction_horizon);
    if (!std::isfinite(wait))
      continue;
    Choice choice{i, profile.performance[i], wait, runtime, wait + runtime};
    if (!best ||
        std::tie(choice.predicted_turnaround, choice.estimated_wait,
                 choice.index) < std::tie(best->predicted_turnaround,
                                          best->estimated_wait, best->index))
      best = choice;
  }
  if (!best)
    throw std::runtime_error("job " + job.id + " cannot fit any system");
  return *best;
}

Response handle_request(dr_evt::Simulation &simulation,
                        const Request &request) {
  Response response;
  try {
    if (request.operation == Operation::Snapshot) {
      simulation.advance_to(request.target_time);
      const auto window = simulation.get_backfill_window();
      response.window.current_time = window.current_time;
      response.window.available_nodes = window.available_nodes;
      response.window.shadow_time = window.shadow_time;
      for (const auto &release : window.releases)
        response.window.releases.push_back(
            {release.time, release.nodes_released});
      response.prediction_horizon =
          simulation.get_prediction_horizon(request.prediction_utilization);
    } else if (request.operation == Operation::Append) {
      response.job_idx =
          simulation.append_job(request.job.submit_time, request.job.num_nodes,
                                request.job.queue, request.job.limit_time);
      simulation.advance_to(request.job.submit_time);
    } else {
      simulation.advance_to(std::numeric_limits<dr_evt::sim_time_t>::max());
      const auto stats = simulation.get_statistics();
      response.statistics = {static_cast<std::uint64_t>(stats.jobs_submitted),
                             static_cast<std::uint64_t>(stats.jobs_completed),
                             stats.makespan};
    }
  } catch (const std::exception &error) {
    response.ok = false;
    response.error = error.what();
  }
  return response;
}

void worker(const Options &options, int rank) {
  const std::size_t index = static_cast<std::size_t>(rank - 1);
  dr_evt::Sim_Params params;
  params.m_infile = options.jobs;
  params.m_total_nodes = options.system_nodes[index];
  params.m_trace_format = "simple";
  params.m_timestamp_format = "epoch";
  params.m_run_time_mode = dr_evt::RunTimeMode::LIMIT;
  params.m_backfill_policy = dr_evt::BackfillPolicy::EASY;
  params.m_priority_policy = dr_evt::PriorityPolicy::FCFS;
  params.m_queue_impl = dr_evt::QueueImplementation::CIRCULAR;
  params.m_verbose = false;
  dr_evt::Simulation simulation(params);

  while (true) {
    const Request request = receive_serialized<Request>(0);
    const bool finish = request.operation == Operation::Finish;
    send_serialized(handle_request(simulation, request), 0);
    if (finish)
      break;
  }
}

std::vector<Response> call_all(const Request &request, int worker_count) {
  for (int rank = 1; rank <= worker_count; ++rank)
    send_serialized(request, rank);
  std::vector<Response> responses;
  responses.reserve(static_cast<std::size_t>(worker_count));
  for (int rank = 1; rank <= worker_count; ++rank) {
    auto response = receive_serialized<Response>(rank);
    if (!response.ok)
      throw std::runtime_error("worker " + std::to_string(rank) + ": " +
                               response.error);
    responses.push_back(std::move(response));
  }
  return responses;
}

void controller(const Options &options, int worker_count) {
  const auto jobs = read_jobs(options.jobs);
  const auto profiles =
      read_profiles(options.performance_table, options.system_ids);
  std::ofstream output_file;
  std::ostream *output = &std::cout;
  if (options.output) {
    output_file.open(*options.output);
    if (!output_file)
      throw std::runtime_error("cannot open output: " + *options.output);
    output = &output_file;
  }
  *output << "job_id,submit_time,profile_id,system_id,relative_performance,"
             "estimated_wait,estimated_runtime,predicted_turnaround,job_idx\n";
  *output << std::setprecision(17);

  for (const auto &job : jobs) {
    Request snapshot;
    snapshot.operation = Operation::Snapshot;
    snapshot.target_time = job.submit_time;
    snapshot.prediction_utilization = options.prediction_utilization;
    const auto snapshots = call_all(snapshot, worker_count);
    const auto &profile = nearest_profile(job, profiles);
    const Choice choice =
        choose_system(job, profile, options.system_nodes, snapshots);

    Request append;
    append.operation = Operation::Append;
    append.job = {job.submit_time, job.num_nodes, job.queue,
                  choice.estimated_runtime};
    send_serialized(append, static_cast<int>(choice.index) + 1);
    const Response response =
        receive_serialized<Response>(static_cast<int>(choice.index) + 1);
    if (!response.ok)
      throw std::runtime_error("worker " + std::to_string(choice.index + 1) +
                               ": " + response.error);
    *output << job.id << ',' << job.submit_time << ',' << profile.id << ','
            << options.system_ids[choice.index] << ','
            << choice.relative_performance << ',' << choice.estimated_wait
            << ',' << choice.estimated_runtime << ','
            << choice.predicted_turnaround << ',' << response.job_idx << '\n';
  }

  Request finish;
  finish.operation = Operation::Finish;
  const auto responses = call_all(finish, worker_count);
  for (std::size_t i = 0; i < responses.size(); ++i) {
    const auto &stats = responses[i].statistics;
    std::cerr << options.system_ids[i] << ": submitted=" << stats.jobs_submitted
              << " completed=" << stats.jobs_completed
              << " makespan=" << stats.makespan << '\n';
  }
}

} // namespace

int main(int argc, char **argv) {
  MPI_Init(&argc, &argv);
  int rank = 0;
  int size = 0;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  MPI_Comm_size(MPI_COMM_WORLD, &size);
  try {
    Options options = parse_options(argc, argv);
    if (size < 2)
      throw std::invalid_argument("at least two MPI ranks are required");
    const auto worker_count = static_cast<std::size_t>(size - 1);
    if (options.system_ids.empty()) {
      for (std::size_t i = 0; i < worker_count; ++i)
        options.system_ids.push_back("system-" + std::to_string(i + 1));
    }
    if (options.system_ids.size() != worker_count)
      throw std::invalid_argument(
          "--system-id must be repeated once per worker rank");
    auto unique_ids = options.system_ids;
    std::sort(unique_ids.begin(), unique_ids.end());
    if (std::adjacent_find(unique_ids.begin(), unique_ids.end()) !=
        unique_ids.end())
      throw std::invalid_argument("--system-id values must be unique");
    if (options.system_nodes.empty())
      options.system_nodes.assign(worker_count, options.total_nodes);
    if (options.system_nodes.size() != worker_count ||
        std::ranges::any_of(options.system_nodes,
                            [](auto nodes) { return nodes == 0; }))
      throw std::invalid_argument(
          "--system-nodes must be positive and repeated once per worker rank");

    if (rank == 0)
      controller(options, size - 1);
    else
      worker(options, rank);
    MPI_Finalize();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "rank " << rank << " error: " << error.what() << '\n';
    MPI_Abort(MPI_COMM_WORLD, 1);
    return 1;
  }
}
