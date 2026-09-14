/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#define DR_EVT_HAS_CONFIG 1
#include "sim/scheduler_fcfs_custom.hpp"
#include "sim/sim.hpp"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace dr_evt;

#if DR_EVT_LEGACY_QUEUE_INPUT
constexpr const char *kTestQueue = "pbatch";
#else
constexpr const char *kTestQueue = "1";
#endif

job_cost_t cost_from_job_order(job_no_t job_id, sim_time_t, tdiff_t,
                               num_nodes_t) {
  return static_cast<job_cost_t>(job_id);
}

std::optional<job_no_t>
select_lowest_cost(const backfill_candidates_t &candidates) {
  if (candidates.empty()) {
    return std::nullopt;
  }
  return std::min_element(candidates.begin(), candidates.end(),
                          [](const auto &lhs, const auto &rhs) {
                            return lhs.second < rhs.second;
                          })
      ->first;
}

void test_external_backfill_selection() {
  backfill_candidates_t observed;
  auto observe_and_select_lowest =
      [&](const backfill_candidates_t &candidates) -> std::optional<job_no_t> {
    observed = candidates;
    return select_lowest_cost(candidates);
  };

  CustomFCFSScheduler scheduler(100, 0, BackfillPolicy::EASY, 2,
                                cost_from_job_order,
                                observe_and_select_lowest);
  scheduler.insert_job(0, 0.0, 100.0, 70);
  scheduler.insert_job(1, 0.0, 200.0, 50);
  scheduler.insert_job(2, 0.0, 50.0, 20);
  scheduler.insert_job(3, 0.0, 20.0, 10);
  scheduler.insert_job(4, 0.0, 30.0, 10);

  const auto selected = scheduler.schedule(100, {}, 0.0);
  assert((selected == std::vector<job_no_t>{0, 2}));
  assert((observed == backfill_candidates_t{{2, 2}, {3, 3}}));

  // The comparator considers only cost, so std::min_element keeps the first
  // candidate when costs tie.
  assert(select_lowest_cost({{7, 4}, {8, 2}, {9, 2}}) == 8);
}

void test_selector_must_return_a_candidate() {
  CustomFCFSScheduler scheduler(
      100, 0, BackfillPolicy::EASY, 1,
      [](job_no_t, sim_time_t, tdiff_t, num_nodes_t) { return 0; },
      [](const backfill_candidates_t &) {
        return std::optional<job_no_t>{99};
      });
  scheduler.insert_job(0, 0.0, 100.0, 70);
  scheduler.insert_job(1, 0.0, 200.0, 50);
  scheduler.insert_job(2, 0.0, 20.0, 10);

  bool threw = false;
  try {
    (void)scheduler.schedule(100, {}, 0.0);
  } catch (const std::invalid_argument &) {
    threw = true;
  }
  assert(threw);
}

void test_current_utilization_api() {
  constexpr const char *trace_path =
      "/tmp/dr_evt_custom_scheduler_empty.csv";
  {
    std::ofstream trace(trace_path);
    trace << "job_submit_time,num_nodes,time_limit\n";
  }

  Sim_Params params;
  params.m_infile = trace_path;
  params.m_total_nodes = 100;
  params.m_trace_format = "simple";
  params.m_timestamp_format = "epoch";
  params.m_run_time_mode = RunTimeMode::LIMIT;
  params.m_num_max_candidates = 4;

  Simulation simulation(params, cost_from_job_order, select_lowest_cost);
  simulation.get_trace().load_data(0);
  simulation.append_job(0.0, 25, kTestQueue, 100.0);
  simulation.advance_to(0.0);
  assert(std::abs(simulation.get_current_utilization() - 0.25) < 1e-12);
  simulation.advance_to(100.0);
  assert(simulation.get_current_utilization() == 0.0);
}

void test_matches_default_circular_easy() {
  constexpr const char *trace_path =
      "/tmp/dr_evt_custom_scheduler_equivalence.csv";
  {
    std::ofstream trace(trace_path);
    trace << "job_submit_time,num_nodes,time_limit\n";
  }

  Sim_Params params;
  params.m_infile = trace_path;
  params.m_total_nodes = 100;
  params.m_trace_format = "simple";
  params.m_timestamp_format = "epoch";
  params.m_run_time_mode = RunTimeMode::LIMIT;
  params.m_backfill_policy = BackfillPolicy::EASY;
  params.m_queue_impl = QueueImplementation::CIRCULAR;
  params.m_num_max_candidates = 3;

  Simulation standard(params);
  Simulation custom(params, cost_from_job_order, select_lowest_cost);
  standard.get_trace().load_data(0);
  custom.get_trace().load_data(0);

  struct InputJob {
    sim_time_t submit_time;
    num_nodes_t nodes;
    tdiff_t run_time;
  };
  const std::vector<InputJob> jobs = {
      {0.0, 70, 100.0}, {0.0, 50, 200.0}, {0.0, 20, 50.0},
      {0.0, 10, 20.0},  {0.0, 10, 30.0},
  };

  for (const auto &job : jobs) {
    standard.append_job(job.submit_time, job.nodes, kTestQueue, job.run_time);
    custom.append_job(job.submit_time, job.nodes, kTestQueue, job.run_time);
  }
  standard.advance_to(300.0);
  custom.advance_to(300.0);

  for (job_no_t id = 0; id < jobs.size(); ++id) {
    const auto &expected = standard.get_trace().job_at(id);
    const auto &actual = custom.get_trace().job_at(id);
    assert(expected.is_scheduled() == actual.is_scheduled());
    assert(expected.get_begin_time() == actual.get_begin_time());
    assert(expected.get_end_time() == actual.get_end_time());
  }
  assert(standard.get_statistics().jobs_completed ==
         custom.get_statistics().jobs_completed);
}

int write_custom_schedule(const char *input_path, const char *output_path,
                          num_nodes_t total_nodes) {
  Sim_Params params;
  params.m_infile = input_path;
  params.set_outfile(output_path);
  params.m_total_nodes = total_nodes;
  params.m_trace_format = "simple";
  params.m_timestamp_format = "epoch";
  params.m_run_time_mode = RunTimeMode::LIMIT;
  params.m_backfill_policy = BackfillPolicy::EASY;
  params.m_num_max_candidates = 3;

  Simulation simulation(params, cost_from_job_order, select_lowest_cost);
  simulation.run();
  simulation.write_simulated_trace();
  return 0;
}

int main(int argc, char **argv) {
  if ((argc == 4 || argc == 5) &&
      std::string(argv[1]) == "--write-schedule") {
    const auto total_nodes = argc == 5
                                 ? static_cast<num_nodes_t>(std::stoul(argv[4]))
                                 : num_nodes_t{100};
    return write_custom_schedule(argv[2], argv[3], total_nodes);
  }
  if (argc != 1) {
    std::cerr << "Usage: " << argv[0]
              << " [--write-schedule INPUT_CSV OUTPUT_CSV [TOTAL_NODES]]\n";
    return 2;
  }

  test_external_backfill_selection();
  test_selector_must_return_a_candidate();
  test_current_utilization_api();
  test_matches_default_circular_easy();
  std::cout << "Custom scheduler tests passed\n";
  return 0;
}
