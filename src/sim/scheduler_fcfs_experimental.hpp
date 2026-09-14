/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_SIM_SCHEDULER_FCFS_EXPERIMENTAL_HPP
#define DR_EVT_SIM_SCHEDULER_FCFS_EXPERIMENTAL_HPP

#include "sim/scheduler_base.hpp"
#include <boost/circular_buffer.hpp>
#include <functional>
#include <optional>
#include <utility>

namespace dr_evt {

using backfill_candidate_t = std::pair<job_no_t, job_cost_t>;
using backfill_candidates_t = std::vector<backfill_candidate_t>;
using job_cost_function_t =
    std::function<job_cost_t(job_no_t, sim_time_t, tdiff_t, num_nodes_t)>;
using backfill_selector_t =
    std::function<std::optional<job_no_t>(const backfill_candidates_t &)>;

/**
 * @brief Experimental FCFS scheduler with externally selected backfilling.
 *
 * FCFS-head handling, circular-buffer growth, and EASY feasibility checks
 * match CircularBufferFCFSScheduler. When a job is inserted, a caller-provided
 * cost function computes the m_cost stored in its wait-queue entry. When the
 * head is blocked, up to num_max_candidates feasible (job_id, cost) pairs are
 * reported to a caller-provided selector. Only the candidate returned by that
 * selector is backfilled during the call.
 */
class ExperimentalFCFSScheduler : public SchedulerBase {
private:
  struct JobEntry {
    job_no_t job_id;
    sim_time_t submit_time;
    tdiff_t run_time_estimate;
    num_nodes_t nodes_requested;
    job_cost_t m_cost;
    bool removed;

    JobEntry(job_no_t id, sim_time_t submit, tdiff_t run_time,
             num_nodes_t nodes, job_cost_t cost)
        : job_id(id), submit_time(submit), run_time_estimate(run_time),
          nodes_requested(nodes), m_cost(cost), removed(false) {}
  };

  boost::circular_buffer<JobEntry> m_wait_queue;
  CircularOverflowPolicy m_overflow_policy;
  size_t m_eligible_end_idx;
  sim_time_t m_current_tracked_time;
  size_t m_removed_count;
  size_t m_num_max_candidates;
  job_cost_function_t m_job_cost_function;
  backfill_selector_t m_backfill_selector;

public:
  /**
   * @param[in] total_nodes Cluster capacity available for allocations.
   * @param[in] initial_job_count Number of initially known jobs, used when
   * initial_capacity is zero.
   * @param[in] bf_policy Backfill policy. External selection is used for EASY.
   * @param[in] num_max_candidates Maximum feasible jobs shown per decision.
   * @param[in] cost_function Function called at insertion to compute m_cost
   * from job ID, submit time, estimated runtime, and requested nodes.
   * @param[in] selector Function returning a candidate job ID, or nullopt to
   * make no backfill selection. The selected ID must occur in its input.
   * @param[in] initial_capacity Initial circular-buffer capacity; zero derives
   * one from initial_job_count.
   * @param[in] overflow_policy Action when the circular buffer is full.
   */
  ExperimentalFCFSScheduler(
      num_nodes_t total_nodes, size_t initial_job_count,
      BackfillPolicy bf_policy, size_t num_max_candidates,
      job_cost_function_t cost_function, backfill_selector_t selector,
      size_t initial_capacity = 0,
      CircularOverflowPolicy overflow_policy = CircularOverflowPolicy::GROW);

  /**
   * @brief Insert a job and compute its cost with m_job_cost_function.
   */
  void insert_job(job_no_t job_id, sim_time_t submit_time,
                  tdiff_t run_time_estimate,
                  num_nodes_t nodes_requested) override;

  std::vector<job_no_t> schedule(num_nodes_t free_nodes,
                                 const running_jobs_t &running_jobs,
                                 sim_time_t current_time) override;
  void sync_to(sim_time_t current_time) override;
  size_t active_job_count() override {
    return m_eligible_end_idx - m_removed_count;
  }
  sim_time_t get_next_arrival_time() override;
  bool has_eligible_jobs() override { return active_job_count() > 0; }

  /**
   * @brief Identify feasible backfill jobs without changing queue state.
   * @details The queue must already be synchronized and have a blocked FCFS
   * head at index zero. Candidates retain FCFS order and are capped by the
   * constructor's num_max_candidates value.
   */
  backfill_candidates_t
  find_backfill_candidates(num_nodes_t available_nodes, sim_time_t current_time,
                           sim_time_t reservation_time) const;

protected:
  size_t wait_queue_size() const override { return m_wait_queue.size(); }

private:
  void compact_if_needed();
};

} // namespace dr_evt

#endif // DR_EVT_SIM_SCHEDULER_FCFS_EXPERIMENTAL_HPP
