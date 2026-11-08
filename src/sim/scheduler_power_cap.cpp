/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#include "sim/scheduler_power_cap.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>

namespace dr_evt {

PowerCappedFCFSScheduler::PowerCappedFCFSScheduler(
    num_nodes_t total_nodes, size_t initial_job_count,
    size_t num_max_candidates, double maximum_power,
    job_power_function_t power_function, bool cap_backfill_power,
    bool cap_fcfs_power, size_t initial_capacity,
    CircularOverflowPolicy overflow_policy)
    : FCFSEasyCustomScheduler(total_nodes, initial_job_count,
                              BackfillPolicy::EASY, num_max_candidates,
                              initial_capacity, overflow_policy),
      m_maximum_power(maximum_power),
      m_power_function(std::move(power_function)),
      m_cap_backfill_power(cap_backfill_power),
      m_cap_fcfs_power(cap_fcfs_power),
      m_uncapped_submit_time(-std::numeric_limits<sim_time_t>::infinity()) {
  if (!std::isfinite(m_maximum_power) || m_maximum_power < 0.0) {
    throw std::invalid_argument(
        "power-capped scheduler maximum power must be finite and "
        "nonnegative");
  }
}

void PowerCappedFCFSScheduler::insert_job(job_no_t job_id,
                                          sim_time_t submit_time,
                                          tdiff_t run_time_estimate,
                                          num_nodes_t nodes_requested) {
  if (!m_power_function) {
    throw std::invalid_argument(
        "power-capped scheduler requires inline power metadata or a power "
        "function");
  }
  const double predicted_power =
      m_power_function(job_id, submit_time, run_time_estimate, nodes_requested);
  if (!std::isfinite(predicted_power) || predicted_power < 0.0) {
    throw std::invalid_argument(
        "power-capped scheduler job power must be finite and nonnegative");
  }
  insert_job_with_cost(job_id, submit_time, run_time_estimate, nodes_requested,
                       predicted_power);
}

std::optional<double> PowerCappedFCFSScheduler::power_from_metadata(
    const SchedulerJobMetadata &metadata) const {
  return metadata.predicted_power;
}

void PowerCappedFCFSScheduler::insert_job_with_metadata(
    job_no_t job_id, sim_time_t submit_time, tdiff_t run_time_estimate,
    num_nodes_t nodes_requested, const SchedulerJobMetadata &metadata) {
  const auto power = power_from_metadata(metadata);
  if (!power) {
    insert_job(job_id, submit_time, run_time_estimate, nodes_requested);
    return;
  }
  if (!std::isfinite(*power) || *power < 0.0) {
    throw std::invalid_argument(
        "power-capped scheduler job power must be finite and nonnegative");
  }
  insert_job_with_cost(job_id, submit_time, run_time_estimate, nodes_requested,
                       *power);
}

double PowerCappedFCFSScheduler::current_power(
    const running_jobs_t &running_jobs) const {
  double power = 0.0;
  for (const auto &[job_id, running] : running_jobs) {
    (void)job_id;
    if (!running.predicted_power) {
      throw std::logic_error(
          "power-capped scheduler has no power estimate for a running job");
    }
    power += *running.predicted_power;
  }
  return power;
}

std::optional<double>
PowerCappedFCFSScheduler::running_job_power(const JobEntry &job) const {
  return job.m_cost;
}

bool PowerCappedFCFSScheduler::can_start_fcfs_job(
    const JobEntry &job, num_nodes_t available_nodes,
    const running_jobs_t &effective_running_jobs,
    sim_time_t current_time) const {
  (void)available_nodes;
  (void)current_time;
  return !m_cap_fcfs_power || is_uncapped_job(job) ||
         current_power(effective_running_jobs) + job.m_cost <= m_maximum_power;
}

sim_time_t PowerCappedFCFSScheduler::fcfs_head_reservation_time(
    const JobEntry &job, num_nodes_t available_nodes,
    const running_jobs_t &effective_running_jobs, sim_time_t current_time) {
  if (!m_cap_fcfs_power || is_uncapped_job(job)) {
    return FCFSEasyCustomScheduler::fcfs_head_reservation_time(
        job, available_nodes, effective_running_jobs, current_time);
  }

  num_nodes_t projected_available = available_nodes;
  double projected_power = 0.0;
  struct Release {
    sim_time_t time = 0.0;
    job_no_t job_id = 0;
    num_nodes_t nodes = 0;
    double power = 0.0;
  };
  std::vector<Release> releases;
  releases.reserve(effective_running_jobs.size());
  for (const auto &[running_id, running] : effective_running_jobs) {
    if (!running.predicted_power) {
      throw std::logic_error(
          "power-capped scheduler has no power estimate for a running job");
    }
    projected_power += *running.predicted_power;
    const sim_time_t end_time = running.start_time + running.run_time;
    if (end_time > current_time) {
      releases.push_back(
          {end_time, running_id, running.nodes, *running.predicted_power});
    }
  }
  if (job.nodes_requested <= projected_available &&
      projected_power + job.m_cost <= m_maximum_power) {
    return current_time;
  }
  std::sort(releases.begin(), releases.end(),
            [](const Release &left, const Release &right) {
              return left.time != right.time ? left.time < right.time
                                             : left.job_id < right.job_id;
            });
  // Keep the release-only path below for fixed, non-overcommitted capacity.
  // Otherwise merge the already ordered capacity outlook with this same
  // release vector; no second event container is needed.
  if (m_outlook_changes_begin != m_outlook_changes_end ||
      (available_nodes == 0 && m_outlook_occupied > m_outlook_capacity)) {
    std::int64_t balance = static_cast<std::int64_t>(available_nodes);
    if (available_nodes == 0 && m_outlook_occupied > m_outlook_capacity) {
      balance = static_cast<std::int64_t>(m_outlook_capacity) -
                static_cast<std::int64_t>(m_outlook_occupied);
    }
    const Capacity_Change *change = m_outlook_changes_begin;
    num_nodes_t reference_capacity = m_outlook_capacity;
    while (change != m_outlook_changes_end && change->time <= current_time) {
      reference_capacity = change->total_nodes;
      ++change;
    }
    size_t begin = 0;
    while (begin < releases.size() || change != m_outlook_changes_end) {
      sim_time_t event_time = begin < releases.size()
                                  ? releases[begin].time
                                  : change->time;
      if (change != m_outlook_changes_end) {
        event_time = std::min(event_time, change->time);
      }
      double released_power = 0.0;
      while (begin < releases.size() && releases[begin].time == event_time) {
        balance += static_cast<std::int64_t>(releases[begin].nodes);
        released_power += releases[begin].power;
        ++begin;
      }
      while (change != m_outlook_changes_end && change->time == event_time) {
        balance += static_cast<std::int64_t>(change->total_nodes) -
                   static_cast<std::int64_t>(reference_capacity);
        reference_capacity = change->total_nodes;
        ++change;
      }
      projected_power = std::max(0.0, projected_power - released_power);
      // Net releases and capacity changes before checking either constraint.
      if (static_cast<std::int64_t>(job.nodes_requested) <= balance &&
          projected_power + job.m_cost <= m_maximum_power) {
        return event_time;
      }
    }
    return std::numeric_limits<sim_time_t>::infinity();
  }
  for (size_t begin = 0; begin < releases.size();) {
    const sim_time_t end_time = releases[begin].time;
    num_nodes_t released_nodes = 0;
    double released_power = 0.0;
    do {
      released_nodes += releases[begin].nodes;
      released_power += releases[begin].power;
      ++begin;
    } while (begin < releases.size() && releases[begin].time == end_time);
    projected_available += released_nodes;
    projected_power = std::max(0.0, projected_power - released_power);
    if (job.nodes_requested <= projected_available &&
        projected_power + job.m_cost <= m_maximum_power) {
      return end_time;
    }
  }
  return std::numeric_limits<sim_time_t>::infinity();
}

} // namespace dr_evt
