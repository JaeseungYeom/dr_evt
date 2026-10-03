/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file test_checkpoint_restart.cpp
 * @brief Exact Ser20 checkpoint/restart coverage for streaming simulations.
 */

#define DR_EVT_HAS_CONFIG 1
#include "sim/sim.hpp"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <optional>
#include <sstream>
#include <string>

using namespace dr_evt;

namespace {

#if DR_EVT_LEGACY_QUEUE_INPUT
constexpr const char *test_queue = "pbatch";
#else
constexpr const char *test_queue = "1";
#endif

constexpr const char *empty_trace = "/tmp/dr_evt_checkpoint_empty.csv";
constexpr const char *loaded_trace = "/tmp/dr_evt_checkpoint_loaded.csv";
constexpr size_t scheduler_workload_size = 256;
constexpr sim_time_t scheduler_checkpoint_time = 60.0;
constexpr sim_time_t restarted_workload_start = 160.0;

/** @brief Create the header-only simulation trace required by Trace. */
void write_empty_trace() {
  std::ofstream output(empty_trace);
  output << "job_submit_time,num_nodes,time_limit\n";
  assert(output.good());
}

/** @brief Construct the common deterministic checkpoint test configuration. */
Sim_Params make_params() {
  Sim_Params params;
  params.m_infile = empty_trace;
  params.m_total_nodes = 100;
  params.m_trace_format = "simple";
  params.m_timestamp_format = "epoch";
  params.m_run_time_mode = RunTimeMode::LIMIT;
  return params;
}

/** @brief Compare two floating-point statistics with simulation tolerance. */
bool close(double left, double right) { return std::fabs(left - right) < 1e-9; }

/** @brief Read a complete binary file for byte-for-byte comparison. */
std::string read_file(const std::string &filename) {
  std::ifstream input(filename, std::ios::binary);
  assert(input.good());
  std::ostringstream contents;
  contents << input.rdbuf();
  assert(input.good() || input.eof());
  return contents.str();
}

/** @brief Populate a large queue with every job lifecycle state represented. */
void populate_checkpoint_workload(Simulation &simulation) {
  simulation.get_trace().load_data(0);
  for (size_t i = 0; i < scheduler_workload_size; ++i) {
    const sim_time_t submit_time = static_cast<sim_time_t>(i / 8) * 5.0;
    const num_nodes_t nodes = static_cast<num_nodes_t>(8 + (i * 7) % 17);
    const tdiff_t run_time = static_cast<tdiff_t>(20 + (i * 11) % 61);
    simulation.append_job(submit_time, nodes, test_queue, run_time);
  }
  simulation.advance_to(scheduler_checkpoint_time);
}

/** @brief Require a checkpoint boundary with a populated mixed-state queue. */
void check_populated_checkpoint_state(const Simulation &simulation) {
  const auto stats = simulation.get_statistics();
  assert(simulation.get_trace().data().size() == scheduler_workload_size);
  assert(stats.jobs_completed > 0);
  assert(stats.jobs_running > 0);
  assert(stats.jobs_waiting > 0);
  const size_t unscheduled = static_cast<size_t>(
      std::count_if(simulation.get_trace().data().begin(),
                    simulation.get_trace().data().end(),
                    [](const Job_Record &job) { return !job.is_scheduled(); }));
  assert(unscheduled > scheduler_workload_size / 2);
  assert(simulation.get_trace().data().back().get_submit_time().first >
         scheduler_checkpoint_time);
}

/** @brief Append the second 256-job workload after the checkpoint boundary. */
void append_restarted_workload(Simulation &simulation) {
  for (size_t i = 0; i < scheduler_workload_size; ++i) {
    const sim_time_t submit_time =
        restarted_workload_start + static_cast<sim_time_t>(i / 8) * 5.0;
    const num_nodes_t nodes = static_cast<num_nodes_t>(8 + ((i + 3) * 5) % 17);
    const tdiff_t run_time = static_cast<tdiff_t>(20 + ((i + 5) * 13) % 61);
    const job_no_t job_id =
        simulation.append_job(submit_time, nodes, test_queue, run_time);
    assert(job_id == static_cast<job_no_t>(scheduler_workload_size + i));
  }
}

/** @brief Verify restart matches uninterrupted execution for one scheduler. */
void test_exact_continuation_case(QueueImplementation queue_impl,
                                  PriorityPolicy priority_policy) {
  auto params = make_params();
  params.m_queue_impl = queue_impl;
  params.m_priority_policy = priority_policy;
  params.m_block_size = 4;

  Simulation uninterrupted(params);
  populate_checkpoint_workload(uninterrupted);
  check_populated_checkpoint_state(uninterrupted);
  const auto checkpoint_stats = uninterrupted.get_statistics();
  const std::vector<job_no_t> status_ids = {0, scheduler_workload_size - 1};
  const auto checkpoint_job_statuses =
      uninterrupted.get_job_statuses(status_ids);

  std::stringstream checkpoint(std::ios::in | std::ios::out | std::ios::binary);
  uninterrupted.save_checkpoint(checkpoint);
  append_restarted_workload(uninterrupted);
  uninterrupted.advance_to(std::numeric_limits<sim_time_t>::max());
  const auto expected_stats = uninterrupted.get_statistics();
  assert(expected_stats.jobs_submitted == 2 * scheduler_workload_size);

  checkpoint.seekg(0);
  Simulation restarted(params);
  restarted.load_checkpoint(checkpoint);
  const auto restored_stats = restarted.get_statistics();
  assert(restored_stats.current_time == checkpoint_stats.current_time);
  assert(restored_stats.jobs_completed == checkpoint_stats.jobs_completed);
  assert(restored_stats.jobs_running == checkpoint_stats.jobs_running);
  assert(restored_stats.jobs_waiting == checkpoint_stats.jobs_waiting);
  assert(restored_stats.nodes_in_use == checkpoint_stats.nodes_in_use);
  const auto restored_job_statuses = restarted.get_job_statuses(status_ids);
  assert(restored_job_statuses.size() == checkpoint_job_statuses.size());
  for (size_t i = 0; i < restored_job_statuses.size(); ++i) {
    assert(restored_job_statuses[i].job_idx ==
           checkpoint_job_statuses[i].job_idx);
    assert(restored_job_statuses[i].state == checkpoint_job_statuses[i].state);
    assert(restored_job_statuses[i].start_time ==
           checkpoint_job_statuses[i].start_time);
    assert(restored_job_statuses[i].end_time ==
           checkpoint_job_statuses[i].end_time);
    assert(restored_job_statuses[i].expected_start_time ==
           checkpoint_job_statuses[i].expected_start_time);
  }

  append_restarted_workload(restarted);
  restarted.advance_to(std::numeric_limits<sim_time_t>::max());
  const auto actual_stats = restarted.get_statistics();
  assert(actual_stats.jobs_submitted == expected_stats.jobs_submitted);
  assert(actual_stats.jobs_completed == expected_stats.jobs_completed);
  assert(actual_stats.current_time == expected_stats.current_time);
  assert(actual_stats.nodes_in_use == expected_stats.nodes_in_use);
  assert(close(actual_stats.resource_area, expected_stats.resource_area));
  assert(close(actual_stats.utilization, expected_stats.utilization));
  assert(close(actual_stats.avg_wait_time, expected_stats.avg_wait_time));
  assert(close(actual_stats.avg_turnaround_time,
               expected_stats.avg_turnaround_time));
  assert(actual_stats.makespan == expected_stats.makespan);

  assert(restarted.get_trace().data().size() ==
         uninterrupted.get_trace().data().size());
  for (size_t i = 0; i < restarted.get_trace().data().size(); ++i) {
    const auto &actual = restarted.get_trace().data()[i];
    const auto &expected = uninterrupted.get_trace().data()[i];
    assert(actual.get_begin_time() == expected.get_begin_time());
    assert(actual.get_end_time() == expected.get_end_time());
  }
}

/** @brief Verify exact continuation for every standard scheduler backend. */
void test_exact_continuation() {
  for (const auto queue_impl :
       {QueueImplementation::CIRCULAR, QueueImplementation::DEQUE,
        QueueImplementation::MULTIMAP, QueueImplementation::BLOCK}) {
    test_exact_continuation_case(queue_impl, PriorityPolicy::FCFS);
  }
  for (const auto priority_policy :
       {PriorityPolicy::SJF, PriorityPolicy::LJF}) {
    test_exact_continuation_case(QueueImplementation::CIRCULAR,
                                 priority_policy);
  }
}

/** @brief Verify loaded trace records are not implicitly submitted on load. */
void test_loaded_jobs_remain_unsubmitted() {
  {
    std::ofstream output(loaded_trace);
    output << "job_submit_time,num_nodes,time_limit\n";
    output << "25,10,5\n";
    assert(output.good());
  }
  auto params = make_params();
  params.m_infile = loaded_trace;
  Simulation source(params);
  assert(source.initialize_trace() == 1);
  std::stringstream checkpoint(std::ios::in | std::ios::out | std::ios::binary);
  source.save_checkpoint(checkpoint);

  checkpoint.seekg(0);
  Simulation restored(params);
  restored.load_checkpoint(checkpoint);
  const auto before = restored.get_statistics();
  assert(before.jobs_submitted == 0);
  assert(before.jobs_waiting == 0);

  restored.advance_to(std::numeric_limits<sim_time_t>::max());
  const auto after = restored.get_statistics();
  assert(after.jobs_submitted == 0);
  assert(after.jobs_completed == 0);
  assert(restored.get_trace().data().size() == 1);
  assert(!restored.get_trace().data().front().is_scheduled());
}

/** @brief Verify callback-based Custom FCFS resumes with supplied callbacks. */
void test_custom_scheduler_continuation() {
  auto params = make_params();
  params.m_backfill_policy = BackfillPolicy::EASY;
  params.m_num_max_candidates = 4;
  size_t cost_calls = 0;
  const job_cost_function_t cost_function =
      [&cost_calls](job_no_t job, sim_time_t, tdiff_t, num_nodes_t) {
        ++cost_calls;
        return static_cast<job_cost_t>(job);
      };
  const backfill_selector_t selector =
      [](const backfill_candidates_t &candidates) -> std::optional<job_no_t> {
    return candidates.empty() ? std::nullopt
                              : std::optional{candidates.back().first};
  };

  Simulation uninterrupted(params, cost_function, selector);
  populate_checkpoint_workload(uninterrupted);
  check_populated_checkpoint_state(uninterrupted);
  const auto checkpoint_stats = uninterrupted.get_statistics();
  std::stringstream checkpoint(std::ios::in | std::ios::out | std::ios::binary);
  uninterrupted.save_checkpoint(checkpoint);
  append_restarted_workload(uninterrupted);
  uninterrupted.advance_to(std::numeric_limits<sim_time_t>::max());
  const auto expected_stats = uninterrupted.get_statistics();
  assert(expected_stats.jobs_submitted == 2 * scheduler_workload_size);

  checkpoint.seekg(0);
  Simulation wrong_scheduler_kind(params);
  bool scheduler_kind_rejected = false;
  try {
    wrong_scheduler_kind.load_checkpoint(checkpoint);
  } catch (const std::runtime_error &) {
    scheduler_kind_rejected = true;
  }
  assert(scheduler_kind_rejected);

  checkpoint.clear();
  checkpoint.seekg(0);
  Simulation restarted(params, cost_function, selector);
  const size_t calls_before_load = cost_calls;
  restarted.load_checkpoint(checkpoint);
  assert(cost_calls == calls_before_load);
  const auto restored_stats = restarted.get_statistics();
  assert(restored_stats.current_time == checkpoint_stats.current_time);
  assert(restored_stats.jobs_completed == checkpoint_stats.jobs_completed);
  assert(restored_stats.jobs_running == checkpoint_stats.jobs_running);
  assert(restored_stats.jobs_waiting == checkpoint_stats.jobs_waiting);
  assert(restored_stats.nodes_in_use == checkpoint_stats.nodes_in_use);
  append_restarted_workload(restarted);
  assert(cost_calls == calls_before_load + scheduler_workload_size);
  restarted.advance_to(std::numeric_limits<sim_time_t>::max());

  const auto &expected = uninterrupted.get_trace().data();
  const auto &actual = restarted.get_trace().data();
  assert(actual.size() == expected.size());
  for (size_t i = 0; i < actual.size(); ++i) {
    assert(actual[i].get_begin_time() == expected[i].get_begin_time());
    assert(actual[i].get_end_time() == expected[i].get_end_time());
  }
  const auto actual_stats = restarted.get_statistics();
  assert(actual_stats.jobs_submitted == expected_stats.jobs_submitted);
  assert(actual_stats.jobs_completed == expected_stats.jobs_completed);
  assert(actual_stats.current_time == expected_stats.current_time);
  assert(actual_stats.nodes_in_use == expected_stats.nodes_in_use);
  assert(close(actual_stats.resource_area, expected_stats.resource_area));
  assert(close(actual_stats.utilization, expected_stats.utilization));
  assert(close(actual_stats.avg_wait_time, expected_stats.avg_wait_time));
  assert(close(actual_stats.avg_turnaround_time,
               expected_stats.avg_turnaround_time));
  assert(actual_stats.makespan == expected_stats.makespan);
}

/** @brief Verify reclaimed jobs and incremental outputs continue exactly once.
 */
void test_output_continuation() {
  const std::string checkpoint_path = "/tmp/dr_evt_checkpoint.bin";
  const std::string baseline_job_output =
      "/tmp/dr_evt_checkpoint_baseline_jobs.csv";
  const std::string baseline_resource_output =
      "/tmp/dr_evt_checkpoint_baseline_resources.csv";
  const std::string restarted_job_output =
      "/tmp/dr_evt_checkpoint_restarted_jobs.csv";
  const std::string restarted_resource_output =
      "/tmp/dr_evt_checkpoint_restarted_resources.csv";
  std::remove(checkpoint_path.c_str());
  std::remove(baseline_job_output.c_str());
  std::remove(baseline_resource_output.c_str());
  std::remove(restarted_job_output.c_str());
  std::remove(restarted_resource_output.c_str());

  auto baseline_params = make_params();
  baseline_params.set_outfile(baseline_job_output);
  baseline_params.set_resource_trace(baseline_resource_output);
  {
    Simulation baseline(baseline_params);
    baseline.get_trace().load_data(0);
    baseline.append_job(0.0, 50, test_queue, 10.0);
    baseline.append_job(0.0, 50, test_queue, 30.0);
    baseline.advance_to(15.0);
    baseline.flush_completed_jobs();
    baseline.advance_to(std::numeric_limits<sim_time_t>::max());
    baseline.write_simulated_trace();
    baseline.write_resource_trace(baseline_resource_output);
    assert(baseline.get_statistics().jobs_completed == 2);
  }

  auto restarted_params = make_params();
  restarted_params.set_outfile(restarted_job_output);
  restarted_params.set_resource_trace(restarted_resource_output);
  {
    Simulation source(restarted_params);
    source.get_trace().load_data(0);
    source.append_job(0.0, 50, test_queue, 10.0);
    source.append_job(0.0, 50, test_queue, 30.0);
    source.advance_to(15.0);
    source.flush_completed_jobs();
    assert(source.get_trace().num_reclaimed() == 1);
    source.save_checkpoint(checkpoint_path);
  }
  {
    Simulation resumed(restarted_params);
    resumed.load_checkpoint(checkpoint_path);
    resumed.advance_to(std::numeric_limits<sim_time_t>::max());
    resumed.write_simulated_trace();
    resumed.write_resource_trace(restarted_resource_output);
    assert(resumed.get_statistics().jobs_completed == 2);
  }

  assert(read_file(restarted_job_output) == read_file(baseline_job_output));
  assert(read_file(restarted_resource_output) ==
         read_file(baseline_resource_output));

  std::ifstream jobs(restarted_job_output);
  std::string line;
  size_t lines = 0;
  while (std::getline(jobs, line)) {
    ++lines;
  }
  assert(lines == 3); // one header and two non-duplicated job rows
}

/** @brief Verify loading with incompatible configuration fails. */
void test_rejected_checkpoints() {
  auto params = make_params();
  Simulation source(params);
  populate_checkpoint_workload(source);
  std::stringstream checkpoint(std::ios::in | std::ios::out | std::ios::binary);
  source.save_checkpoint(checkpoint);

  auto incompatible_params = make_params();
  incompatible_params.m_total_nodes = 99;
  Simulation incompatible(incompatible_params);
  checkpoint.seekg(0);
  bool mismatch_rejected = false;
  try {
    incompatible.load_checkpoint(checkpoint);
  } catch (const std::runtime_error &) {
    mismatch_rejected = true;
  }
  assert(mismatch_rejected);
}

} // namespace

/** @brief Run all exact checkpoint/restart tests. */
int main() {
  write_empty_trace();
  test_exact_continuation();
  test_loaded_jobs_remain_unsubmitted();
  test_custom_scheduler_continuation();
  test_output_continuation();
  test_rejected_checkpoints();
  std::cout << "Checkpoint/restart tests passed\n";
  return EXIT_SUCCESS;
}
