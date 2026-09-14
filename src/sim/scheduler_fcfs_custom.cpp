/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#include "sim/scheduler_fcfs_custom.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace dr_evt {

CustomFCFSScheduler::CustomFCFSScheduler(
    num_nodes_t total_nodes, size_t initial_job_count, BackfillPolicy bf_policy,
    size_t num_max_candidates, job_cost_function_t cost_function,
    backfill_selector_t selector, size_t initial_capacity,
    CircularOverflowPolicy overflow_policy)
    : SchedulerBase(total_nodes, bf_policy),
      m_wait_queue(initial_capacity != 0 ? initial_capacity
                                         : initial_job_count),
      m_overflow_policy(overflow_policy), m_eligible_end_idx(0),
      m_current_tracked_time(0.0), m_removed_count(0),
      m_num_max_candidates(num_max_candidates),
      m_job_cost_function(std::move(cost_function)),
      m_backfill_selector(std::move(selector)) {
  if (m_num_max_candidates == 0) {
    throw std::invalid_argument(
        "CustomFCFSScheduler requires num_max_candidates > 0");
  }
  if (!m_job_cost_function) {
    throw std::invalid_argument(
        "CustomFCFSScheduler requires a job cost function");
  }
  if (!m_backfill_selector) {
    throw std::invalid_argument(
        "CustomFCFSScheduler requires a backfill selector");
  }
  if (m_backfill_policy == BackfillPolicy::CONSERVATIVE) {
    throw std::invalid_argument(
        "CustomFCFSScheduler supports EASY or NONE backfilling");
  }
}

void CustomFCFSScheduler::insert_job(job_no_t job_id,
                                           sim_time_t submit_time,
                                           tdiff_t run_time_estimate,
                                           num_nodes_t nodes_requested) {
  if (m_wait_queue.full()) {
    if (m_overflow_policy == CircularOverflowPolicy::ABORT) {
      throw std::runtime_error(
          "CustomFCFSScheduler: wait queue capacity (" +
          std::to_string(m_wait_queue.capacity()) + ") exceeded");
    }
    m_wait_queue.set_capacity(std::max<size_t>(m_wait_queue.capacity() * 2, 1));
  }

  const job_cost_t cost = m_job_cost_function(
      job_id, submit_time, run_time_estimate, nodes_requested);
  m_wait_queue.push_back(
      JobEntry(job_id, submit_time, run_time_estimate, nodes_requested, cost));
  if (submit_time <= m_current_tracked_time) {
    m_eligible_end_idx = m_wait_queue.size();
  }
}

void CustomFCFSScheduler::sync_to(sim_time_t current_time) {
  if (current_time <= m_current_tracked_time) {
    return;
  }
  while (m_eligible_end_idx < m_wait_queue.size() &&
         m_wait_queue[m_eligible_end_idx].submit_time <= current_time) {
    ++m_eligible_end_idx;
  }
  m_current_tracked_time = current_time;
}

sim_time_t CustomFCFSScheduler::get_next_arrival_time() {
  for (size_t i = m_eligible_end_idx; i < m_wait_queue.size(); ++i) {
    if (!m_wait_queue[i].removed) {
      return m_wait_queue[i].submit_time;
    }
  }
  return std::numeric_limits<sim_time_t>::max();
}

void CustomFCFSScheduler::compact_if_needed() {
  if (m_removed_count == 0 || m_removed_count * 2 <= m_wait_queue.size()) {
    return;
  }

  size_t eligible_removed = 0;
  for (size_t i = 0; i < m_eligible_end_idx; ++i) {
    eligible_removed += m_wait_queue[i].removed ? 1 : 0;
  }
  const auto new_end =
      std::remove_if(m_wait_queue.begin(), m_wait_queue.end(),
                     [](const JobEntry &entry) { return entry.removed; });
  m_wait_queue.erase(new_end, m_wait_queue.end());
  m_eligible_end_idx -= eligible_removed;
  m_removed_count = 0;
}

backfill_candidates_t CustomFCFSScheduler::find_backfill_candidates(
    num_nodes_t available_nodes, sim_time_t current_time,
    sim_time_t reservation_time) const {
  backfill_candidates_t candidates;
  candidates.reserve(std::min(m_num_max_candidates, m_eligible_end_idx > 0
                                                        ? m_eligible_end_idx - 1
                                                        : size_t{0}));

  if (m_num_max_candidates == 0 || m_eligible_end_idx <= 1) {
    return candidates;
  }

  for (size_t i = 1; i < m_eligible_end_idx; ++i) {
    const auto &job = m_wait_queue[i];
    if (job.removed || job.nodes_requested > available_nodes) {
      continue;
    }
    if (current_time + job.run_time_estimate >= reservation_time) {
      continue;
    }
    candidates.emplace_back(job.job_id, job.m_cost);
    if (candidates.size() == m_num_max_candidates) {
      break;
    }
  }
  return candidates;
}

std::vector<job_no_t>
CustomFCFSScheduler::schedule(num_nodes_t free_nodes,
                                    const running_jobs_t &running_jobs,
                                    sim_time_t current_time) {
  sync_to(current_time);
  compact_if_needed();

  if (m_eligible_end_idx == 0) {
    return {};
  }

  std::vector<job_no_t> jobs_to_run;
  num_nodes_t available_nodes = free_nodes;
  running_jobs_t effective_running_jobs = running_jobs;

  while (m_eligible_end_idx > 0 && !m_wait_queue.empty() &&
         (m_wait_queue.front().removed ||
          m_wait_queue.front().nodes_requested <= available_nodes)) {
    if (!m_wait_queue.front().removed) {
      const auto &job = m_wait_queue.front();
      jobs_to_run.push_back(job.job_id);
      available_nodes -= job.nodes_requested;
      effective_running_jobs[job.job_id] = {current_time, job.run_time_estimate,
                                            job.nodes_requested};
    } else {
      --m_removed_count;
    }
    m_wait_queue.pop_front();
    --m_eligible_end_idx;
  }

  if (active_job_count() == 0 || m_backfill_policy == BackfillPolicy::NONE) {
    return jobs_to_run;
  }

  m_fcfs_reservation_time = calculate_fcfs_reservation(
      m_wait_queue.front().nodes_requested, available_nodes,
      effective_running_jobs, current_time);

  const auto candidates = find_backfill_candidates(
      available_nodes, current_time, m_fcfs_reservation_time);
  if (candidates.empty()) {
    return jobs_to_run;
  }

  const std::optional<job_no_t> selected = m_backfill_selector(candidates);
  if (!selected) {
    return jobs_to_run;
  }

  const auto candidate =
      std::find_if(candidates.begin(), candidates.end(),
                   [&](const auto &item) { return item.first == *selected; });
  if (candidate == candidates.end()) {
    throw std::invalid_argument(
        "backfill selector returned a job that was not a candidate");
  }

  for (size_t i = 1; i < m_eligible_end_idx; ++i) {
    if (!m_wait_queue[i].removed && m_wait_queue[i].job_id == *selected) {
      m_wait_queue[i].removed = true;
      ++m_removed_count;
      jobs_to_run.push_back(*selected);
      break;
    }
  }
  return jobs_to_run;
}

} // namespace dr_evt
