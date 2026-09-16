/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file sim/sim.cpp
 * @brief Discrete-event simulation orchestration implementation.
 * @details Public Simulation contracts are documented in sim.hpp; this file
 * contains event-loop sequencing, runtime sampling, and output mechanics.
 */

#include "sim/sim.hpp"
#include "sim/block_wait_queue.hpp"
#include "trace/epoch.hpp"
#include "trace/job_io.hpp"
#include "trace/parse_utils.hpp"
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <queue>
#include <sstream>

namespace dr_evt {

template <typename TraceType>
BasicSimulation<TraceType>::BasicSimulation(const Sim_Params &params)
    : m_params(params), m_trace(params.m_infile, params.m_trace_format,
                                params.m_timestamp_format, params.m_timezone),
      m_scheduler(create_scheduler(
          params.m_total_nodes, m_trace.data().size(), params.m_backfill_policy,
          params.m_priority_policy, params.m_queue_impl, params.m_block_size,
          params.m_wait_queue_capacity, params.m_wait_queue_overflow)),
      m_custom_scheduler(nullptr), m_current_time(0.0),
      m_job_rejection_capacity(params.m_total_nodes),
      m_capacity_changes(load_capacity_schedule(params.m_capacity_schedule,
                                                params.m_total_nodes)),
      m_next_capacity_change(0), m_current_capacity(params.m_total_nodes),
      m_capacity_area(0.0), m_capacity_area_time(0.0), m_jobs_completed(0),
      m_jobs_submitted(0), m_pre_start_jobs(0), m_warm_resource_area(0.0),
      m_warm_resource_end(0.0), m_rng(params.m_seed), m_queue_length_sum(0),
      m_queue_length_samples(0), m_queue_length_peak(0) {
  reset_capacity_schedule();
}

template <typename TraceType>
BasicSimulation<TraceType>::BasicSimulation(const Sim_Params &params,
                                            job_cost_function_t cost_function,
                                            backfill_selector_t selector)
    : m_params(params), m_trace(params.m_infile, params.m_trace_format,
                                params.m_timestamp_format, params.m_timezone),
      m_scheduler(std::make_unique<CustomFCFSScheduler>(
          params.m_total_nodes, m_trace.data().size(), params.m_backfill_policy,
          params.m_num_max_candidates, std::move(cost_function),
          std::move(selector), params.m_wait_queue_capacity,
          params.m_wait_queue_overflow)),
      m_custom_scheduler(static_cast<CustomFCFSScheduler *>(m_scheduler.get())),
      m_current_time(0.0), m_job_rejection_capacity(params.m_total_nodes),
      m_capacity_changes(load_capacity_schedule(params.m_capacity_schedule,
                                                params.m_total_nodes)),
      m_next_capacity_change(0), m_current_capacity(params.m_total_nodes),
      m_capacity_area(0.0), m_capacity_area_time(0.0), m_jobs_completed(0),
      m_jobs_submitted(0), m_pre_start_jobs(0), m_warm_resource_area(0.0),
      m_warm_resource_end(0.0), m_rng(params.m_seed), m_queue_length_sum(0),
      m_queue_length_samples(0), m_queue_length_peak(0) {
  reset_capacity_schedule();
}

template <typename TraceType> void BasicSimulation<TraceType>::run() {
  if (m_params.m_verbose) {
    std::cout << "Starting simulation..." << std::endl;
  }

  if (m_params.m_is_time_set &&
      (!std::isfinite(m_params.m_max_time) || m_params.m_max_time < 0.0)) {
    throw std::invalid_argument(
        "--max_time must be finite and nonnegative");
  }
  if (m_params.m_is_time_set &&
      m_params.m_max_time < m_params.m_sim_start_time) {
    throw std::invalid_argument(
        "--max_time must be greater than or equal to --sim_start_time");
  }

  // Must happen before initialize_trace()/run_progressive() (either
  // resolves m_data's capacity from whatever's set here) - unlike
  // resource-history's capacity, which is only needed once recording
  // starts, well after load.
  m_trace.set_job_store_capacity(m_params.m_job_store_capacity);
  m_trace.set_job_store_overflow(m_params.m_job_store_overflow);
  m_trace.set_job_flush_interval(m_params.m_job_flush_interval);
  m_trace.set_memory_pressure_fraction(m_params.m_memory_pressure_fraction);

  if (!m_params.m_infile_list.empty()) {
    if (m_params.m_sim_start_time != 0.0) {
      throw std::runtime_error(
          "--sim_start_time is not supported with --infile_list; warm-start "
          "classification requires one replay trace loaded at the boundary");
    }
    // Progressive loading: REPLAY-format input isn't supported here
    // - REPLAY bypasses the scheduler entirely (begin_time/end_time
    // already fixed in the trace), so there's no notion of "submit
    // this job now" for it to plug into in the first place, and
    // nothing to gain from bounding memory during a run that never
    // makes scheduling decisions at all.
    if (m_trace.dcols().get_trace_mode() == TraceMode::REPLAY) {
      throw std::runtime_error(
          "--infile_list does not support REPLAY-format input "
          "(begin_time/end_time already present) - it only makes "
          "sense for simulation-mode input, where the scheduler "
          "actually decides when each job runs.");
    }
    run_progressive();
    if (m_params.m_verbose) {
      std::cout << "Simulation complete\n" + std::string("Jobs submitted: ") +
                       std::to_string(m_jobs_submitted) + "\n" +
                       std::string("Jobs completed: ") +
                       std::to_string(m_jobs_completed) + "\n";
    }
    return;
  }

  // Initialize: load jobs and determine durations
  initialize_trace();

  if (m_params.m_verbose) {
    std::cout << "Loaded " + std::to_string(m_trace.data().size()) +
                     " jobs from trace\n";
    std::cout << "Running simulation with " +
                     std::to_string(m_params.m_total_nodes) + " nodes\n";
  }

  m_trace.set_resource_history_capacity(m_params.m_resource_history_capacity);

  if (m_params.m_sim_start_time != 0.0) {
    run_warm_start();
  } else {
    // Open outputs before processing so bounded buffers can flush
    // incrementally. Warm-start opens them later, after discarding pre-boundary
    // samples and installing its nonzero baseline.
    m_trace.start_resource_trace(m_params.get_resource_trace(),
                                 m_params.m_total_nodes,
                                 m_params.m_msec_output);
    m_trace.start_simulated_trace(m_params.get_outfile(),
                                  m_params.m_msec_output);

    if (m_trace.dcols().get_trace_mode() == TraceMode::REPLAY) {
      // Replay-format input (begin_time/end_time present): don't consult
      // the scheduler at all - reuse the same bypass logic the standalone
      // tracer binary uses, driven into Trace's own owned context so the rest
      // of this class (write_simulated_trace(), write_resource_trace())
      // sees the result exactly as if the scheduler had run.
      const sim_time_t run_limit =
          m_params.m_is_time_set
              ? m_params.m_max_time
              : std::numeric_limits<sim_time_t>::max();
      sim_time_t replay_end = m_current_time;
      job_no_t replay_job_no =
          static_cast<job_no_t>(m_trace.num_reclaimed());
      for (const auto &job : m_trace.data()) {
        const sim_time_t submit =
            convert_epoch<sim_time_t>(job.get_submit_time());
        const sim_time_t begin =
            convert_epoch<sim_time_t>(job.get_begin_time());
        const sim_time_t end = convert_epoch<sim_time_t>(job.get_end_time());
        if (submit <= run_limit) {
          replay_end = std::max(replay_end, end);
          if (end <= run_limit) {
            ++m_jobs_completed;
          } else if (m_params.m_is_time_set && begin <= run_limit) {
            m_running_jobs[replay_job_no] = {
                begin, static_cast<tdiff_t>(end - begin), job.get_num_nodes()};
          }
        }
        ++replay_job_no;
      }
      m_trace.run_job_trace(std::string(), m_params.m_total_nodes, run_limit);
      m_current_time = m_params.m_is_time_set ? run_limit : replay_end;
    } else {
      // Batch mode: Submit all jobs upfront, then advance to infinity
      // This uses the streaming API internally
      for (num_jobs_t i = 0; i < m_trace.data().size(); ++i) {
        const auto &job = m_trace.job_at(i);
        sim_time_t submit_time =
            convert_epoch<sim_time_t>(job.get_submit_time());
        submit_job(i, submit_time);
      }

      // A configured maximum is an inclusive event-time boundary. Without
      // one, use the internal drain sentinel and stop at the last real event.
      const sim_time_t run_limit =
          m_params.m_is_time_set
              ? m_params.m_max_time
              : std::numeric_limits<sim_time_t>::max();
      advance_to(run_limit);
    }
  }

  // m_jobs_completed is tracked incrementally during the run itself
  // (see the event-processing loop above) - no need to recompute it
  // here, and doing so by iterating m_trace.data() directly would
  // now be wrong anyway, since reclaimed jobs are no longer there to
  // recount.
  if (m_params.m_verbose) {
    std::cout << "Simulation complete\n" + std::string("Jobs submitted: ") +
                     std::to_string(m_jobs_submitted) + "\n" +
                     std::string("Jobs completed: ") +
                     std::to_string(m_jobs_completed) + "\n";
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::print_stats(std::ostream &os) const {
  os << "=== Simulation Statistics ===" << std::endl;
  os << "Total jobs: ";
  if (m_params.m_sim_start_time != 0.0) {
    os << m_jobs_submitted;
  } else {
    os << (m_trace.data().size() + m_trace.num_reclaimed());
  }
  os << std::endl;
  os << "Jobs submitted: " << m_jobs_submitted << std::endl;
  // m_trace.completed_count() (populated via write_job_line(), called
  // both at reclaim time and by write_simulated_trace()'s final
  // flush - already run by the time this is called, see sim.cpp's
  // caller) counts every completed job regardless of whether it's
  // since been reclaimed from m_data - m_jobs_completed only tracks
  // in-flight completions during the run itself and isn't used here.
  os << "Jobs completed: " << m_trace.completed_count() << std::endl;
  os << "Current time: "
     << format_sim_time(m_current_time, m_params.m_msec_output) << std::endl;
  os << "Total nodes: " << m_params.m_total_nodes << std::endl;

  // Calculate metrics - sum+count already accumulated incrementally in
  // Trace as each job was written out (see write_job_line()), so this
  // is correct even for jobs already reclaimed from m_data by now.
  if (m_trace.completed_count() > 0) {
    const auto completed = m_trace.completed_count();

    // Unlike Current time/Makespan above, these are computed averages
    // (division results), which commonly have a fractional part even
    // with integer-second input data (e.g. 220/3 = 73.333...) - that
    // precision is meaningful and was shown by default before
    // msec_output existed, so it's preserved here regardless of
    // msec_output's setting, rather than routed through
    // format_sim_time (whose integer-truncation default is for
    // matching existing trace-output files' conventions, not for
    // these summary statistics).
    os << "Average wait time: " << (m_trace.wait_time_sum() / completed)
       << " sec" << std::endl;
    os << "Average turnaround time: "
       << (m_trace.turnaround_time_sum() / completed) << " sec" << std::endl;
    os << "Makespan: "
       << format_sim_time(m_trace.makespan(), m_params.m_msec_output) << " sec"
       << std::endl;
  }

  // Queue length statistics
  if (m_queue_length_samples > 0) {
    double avg_queue_length =
        static_cast<double>(m_queue_length_sum) / m_queue_length_samples;
    os << "Average queue length: " << avg_queue_length << " jobs" << std::endl;
    os << "Peak queue length: " << m_queue_length_peak << " jobs" << std::endl;
  }
}

template <typename TraceType>
num_jobs_t BasicSimulation<TraceType>::initialize_trace(num_jobs_t max_jobs) {
  // Clear any previously-loaded data first, so this method is safe to
  // call more than once (directly, or via run() after an earlier
  // explicit call - run() calls this internally too). Without this,
  // Job_Io::load() only ever push_back()s and never clears the
  // underlying vector itself, so a second call would silently append
  // to, rather than replace, the first call's jobs - e.g. calling
  // initialize_trace() explicitly and then run() would silently double
  // every job's count.
  m_trace.data().clear();

  // Load trace data
  const auto max_num_jobs =
      (max_jobs > 0u) ? max_jobs
                      : (m_params.m_is_jobs_set ? m_params.m_max_jobs
                                                : static_cast<num_jobs_t>(0u));

  // No .reserve() here anymore - load_data() sizes m_data's capacity
  // itself (resolve_job_store_capacity()), same convention as the wait
  // queue and resource-history.
  int rc = m_trace.load_data(max_num_jobs);
  if (rc != EXIT_SUCCESS) {
    throw std::runtime_error("Failed to load trace data");
  }

  // Sort jobs by submission time
  std::stable_sort(m_trace.data().begin(), m_trace.data().end());

  // Determine actual durations (simulation mode only)
  if (m_trace.dcols().get_trace_mode() == TraceMode::SIMULATION) {
    determine_job_run_time();
  }

  m_current_time = 0.0;
  reset_capacity_schedule();
  m_jobs_submitted = 0;
  m_jobs_completed = 0;
  m_pre_start_jobs = 0;
  m_warm_resource_area = 0.0;
  m_warm_resource_end = 0.0;
  if (m_custom_scheduler != nullptr) {
    m_custom_scheduler->reset_resource_accounting();
  }

  return static_cast<num_jobs_t>(m_trace.data().size());
}

template <typename TraceType>
void BasicSimulation<TraceType>::run_warm_start() {
  const sim_time_t sim_start_time = m_params.m_sim_start_time;
  const sim_time_t run_limit =
      m_params.m_is_time_set
          ? m_params.m_max_time
          : std::numeric_limits<sim_time_t>::max();
  if (m_trace.dcols().get_trace_mode() != TraceMode::REPLAY) {
    throw std::runtime_error(
        "--sim_start_time requires replay-format input with begin_time and "
        "end_time columns so jobs already running at the boundary can be "
        "identified");
  }

  const job_no_t first_job = static_cast<job_no_t>(m_trace.num_reclaimed());
  const job_no_t jobs_end =
      static_cast<job_no_t>(m_trace.num_reclaimed() + m_trace.data().size());
  m_pre_start_jobs = 0;
  m_warm_resource_area = 0.0;
  m_warm_resource_end = sim_start_time;

  for (job_no_t job_no = first_job; job_no < jobs_end; ++job_no) {
    auto &job = m_trace.job_at(job_no);
    const sim_time_t begin = convert_epoch<sim_time_t>(job.get_begin_time());
    const sim_time_t end = convert_epoch<sim_time_t>(job.get_end_time());
    const sim_time_t submit = convert_epoch<sim_time_t>(job.get_submit_time());

    if (begin < sim_start_time) {
      // Historical starts bypass the scheduler. Replay establishes both live
      // allocation and policy-specific state (notably Pcon) exactly once.
      m_trace.enqueue_replay_job(job_no);
      ++m_pre_start_jobs;
      if (end > sim_start_time) {
        // Its departure is fixed historical state, so reservations should
        // use that known end rather than a possibly stale original limit.
        m_running_jobs[job_no] = {begin, job.get_actual_run_time(),
                                  job.get_num_nodes()};
        m_warm_resource_area +=
            static_cast<tdiff_t>(job.get_num_nodes()) * (end - sim_start_time);
        m_warm_resource_end = std::max(m_warm_resource_end, end);
      }
    } else if (submit >= sim_start_time) {
      // Let the normal scheduler replace the historical begin/end times in
      // the counterfactual run, and select this simulated job's duration by
      // the same run-time policy used in an ordinary simulation. Warmup jobs
      // bypass this branch and retain their recorded timing.
      job.prepare_for_resimulation();
      determine_one_job_run_time(job);
    } else {
      // This job was waiting before the boundary. Reconstructing an inherited
      // wait queue is a separate policy decision, so it is intentionally not
      // admitted into either the seed state or the post-boundary workload.
      job.suppress_output();
    }
  }

  // Replay only the history required to establish state at t. Old departures
  // are suppressed before Trace can run any output/reclamation hook.
  while (!m_trace.pending_events().empty()) {
    const auto event = *m_trace.pending_events().begin();
    const sim_time_t event_time = convert_epoch<sim_time_t>(event.get_time());
    if (event_time > sim_start_time) {
      break;
    }
    if (event.is_departure()) {
      m_trace.job_at(event.get_job_idx()).suppress_output();
      --m_pre_start_jobs;
    }
    m_trace.process_single_event();
  }

  m_current_time = sim_start_time;
  apply_capacity_changes(sim_start_time);
  reset_capacity_accounting(sim_start_time);

  // This is the accounting/output boundary: retain occupancy and Pcon state,
  // discard every pre-t sample, and establish a baseline at t.
  m_trace.reset_resource_recording(sim_start_time);
  m_trace.start_resource_trace(m_params.get_resource_trace(),
                               m_params.m_total_nodes, m_params.m_msec_output);
  m_trace.start_simulated_trace(m_params.get_outfile(), m_params.m_msec_output);
  if (m_custom_scheduler != nullptr) {
    m_custom_scheduler->reset_resource_accounting(sim_start_time,
                                                  get_available_nodes());
  }

  // No auxiliary job list is retained: prepared post-boundary records keep
  // their real submission time, while every excluded/finished historical
  // record carries the existing unscheduled sentinel.
  for (job_no_t job_no = first_job; job_no < jobs_end; ++job_no) {
    const auto submit_epoch = m_trace.job_at(job_no).get_submit_time();
    if (submit_epoch != Job_Record::unscheduled_sentinel()) {
      const sim_time_t submit = convert_epoch<sim_time_t>(submit_epoch);
      if (submit >= sim_start_time) {
        submit_job(job_no, submit);
      }
    }
  }

  if (m_pre_start_jobs != 0) {
    if (m_custom_scheduler != nullptr) {
      advance_to_impl<true, true>(run_limit, m_custom_scheduler);
    } else {
      advance_to_impl<false, true>(run_limit, nullptr);
    }
  }

  // The ordinary stage has no historical-job test in its compiled loop. If
  // the configured horizon ended during warm-up, the current time is already
  // at run_limit and this call is an inexpensive no-op.
  advance_to(run_limit);
}

template <typename TraceType>
void BasicSimulation<TraceType>::determine_one_job_run_time(Job_Record &job) {
  // Scheduler uses time_limit as the best estimator for planning (realistic
  // mode). run_time_mode controls how the job's actual execution length is
  // determined.

  tdiff_t run_time;

  switch (m_params.m_run_time_mode) {
  case RunTimeMode::ACTUAL:
    // Read actual_run_time from trace (most realistic)
    run_time = job.get_actual_run_time();
    break;

  case RunTimeMode::DISTRIBUTION:
    // Sample from distribution (realistic with variation)
    run_time =
        sample_run_time(job.get_limit_time(), m_params.m_run_time_distribution,
                        m_params.m_run_time_scale, m_params.m_run_time_stddev);
    job.set_actual_run_time(run_time);
    break;

  case RunTimeMode::LIMIT:
    // Use time_limit in place of run_time (unrealistic, for debugging/testing)
    run_time = job.get_limit_time();
    job.set_actual_run_time(run_time);
    break;
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::determine_job_run_time() {
  for (auto &job : m_trace.data()) {
    determine_one_job_run_time(job);
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::determine_job_run_time(
    const std::vector<job_no_t> &job_nos) {
  for (job_no_t job_no : job_nos) {
    determine_one_job_run_time(m_trace.job_at(job_no));
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::run_progressive() {
  // Minimal reset, equivalent to initialize_trace()'s own tail - no
  // load_data() call here, since there's no single file to load
  // upfront; each file gets loaded as the driving loop below reaches it.
  m_trace.data().clear();
  m_current_time = 0.0;
  reset_capacity_schedule();
  m_jobs_submitted = 0;
  m_jobs_completed = 0;
  m_pre_start_jobs = 0;
  m_warm_resource_area = 0.0;
  m_warm_resource_end = 0.0;
  if (m_custom_scheduler != nullptr) {
    m_custom_scheduler->reset_resource_accounting();
  }

  // Same reasoning as run()'s single-file path: open output files
  // early so reclaiming during the run (which starts happening
  // between files here, unlike single-file batch mode) can flush to
  // them incrementally, not only at the very end.
  m_trace.set_resource_history_capacity(m_params.m_resource_history_capacity);
  m_trace.start_resource_trace(m_params.get_resource_trace(),
                               m_params.m_total_nodes, m_params.m_msec_output);
  m_trace.start_simulated_trace(m_params.get_outfile(), m_params.m_msec_output);

  for (const std::string &fname : m_params.m_infile_list_parsed) {
    if (m_params.m_verbose) {
      std::cout << "Loading " + fname + "...\n";
    }

    // reclaim + grow-or-abort against (what's left unreclaimed) +
    // (this file's job count) - Trace::ensure_batch_capacity(),
    // shared with append_jobs(). Also checks this file's own rows
    // are submit_time-sorted, and that its earliest submit_time
    // isn't before the previous file's latest - see its own doc
    // comment.
    auto job_nos = m_trace.load_next_file(m_current_time, fname);
    if (job_nos.empty()) {
      continue; // an empty file contributes nothing to submit
    }

    // Simulation-mode-only, same condition initialize_trace() uses
    // for the single-file path - REPLAY is rejected before
    // run_progressive() is ever called (see run()), so this is
    // always true here, but kept explicit rather than assumed.
    if (m_trace.dcols().get_trace_mode() == TraceMode::SIMULATION) {
      determine_job_run_time(job_nos);
    }

    // Submit this file's jobs one at a time, in submit_time order
    // (guaranteed by load_next_file(), since m_data stays
    // submit-time sorted) - not all upfront the way single-file
    // batch mode does, since nothing becomes reclaimable until
    // advance_to() actually processes a completion, and upfront
    // submission would mean every file's jobs are already known to
    // m_data before that ever gets a chance to run once.
    for (job_no_t job_no : job_nos) {
      const auto &job = m_trace.job_at(job_no);
      sim_time_t submit_time = convert_epoch<sim_time_t>(job.get_submit_time());
      submit_job(job_no, submit_time);
    }

    // This file's last job was just added to the wait queue -
    // nothing more from it to submit, so this is the point to let
    // time (and reclaiming) actually progress before the next file
    // loads. advance_to() itself is completely unmodified - called
    // exactly as it always has been, just with this file's own
    // last submit_time as the target instead of infinity.
    sim_time_t last_submit_time = convert_epoch<sim_time_t>(
        m_trace.job_at(job_nos.back()).get_submit_time());
    const sim_time_t run_limit =
        m_params.m_is_time_set ? m_params.m_max_time : last_submit_time;
    advance_to(std::min(last_submit_time, run_limit));
    if (m_params.m_is_time_set && last_submit_time >= m_params.m_max_time) {
      break;
    }
  }

  // Drain whatever is still running, or stop at the configured inclusive
  // time boundary.
  const sim_time_t run_limit =
      m_params.m_is_time_set
          ? m_params.m_max_time
          : std::numeric_limits<sim_time_t>::max();
  if (m_current_time < run_limit) {
    advance_to(run_limit);
  }
}

template <typename TraceType>
tdiff_t BasicSimulation<TraceType>::sample_run_time(tdiff_t time_limit,
                                                    DistributionType dist,
                                                    double scale,
                                                    double stddev) {
  if (time_limit <= 0.0) {
    return 0.0;
  }

  switch (dist) {
  case DistributionType::NORMAL: {
    double mean = time_limit * scale;
    double sd = time_limit * stddev;
    std::normal_distribution<double> normal_dist(mean, sd);
    double run_time = m_rng.sample(normal_dist);
    // A real HPC scheduler kills a job at its stated time_limit -
    // it can never actually run longer than that. Cap here so a
    // wide-tailed sample can't silently let a job run past its own
    // limit, which would diverge from real system behavior.
    return std::min(time_limit, std::max(0.0, run_time));
  }

  case DistributionType::LOGNORMAL: {
    double mu = std::log(time_limit * scale);
    double sigma = stddev;
    std::lognormal_distribution<double> lognormal_dist(mu, sigma);
    // Always >= 0 by construction; still cap at time_limit for the
    // same reason as NORMAL above - a real job cannot run past it.
    return std::min(time_limit, m_rng.sample(lognormal_dist));
  }

  case DistributionType::UNIFORM: {
    double min_run_time = time_limit * scale;
    double max_run_time = time_limit * (scale + stddev);
    std::uniform_real_distribution<double> uniform_dist(min_run_time,
                                                        max_run_time);
    // Not capped: unlike NORMAL/LOGNORMAL's unbounded-above tails,
    // this distribution's upper bound is already an explicit,
    // direct function of the caller's own scale/stddev choice -
    // exceeding time_limit here only happens if the caller
    // deliberately set scale + stddev > 1.0.
    return std::max(0.0, m_rng.sample(uniform_dist));
  }

  default:
    return time_limit;
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::write_simulated_trace() {
  const sim_time_t completed_through =
      m_params.m_is_time_set
          ? m_params.m_max_time
          : std::numeric_limits<sim_time_t>::max();
  m_trace.write_simulated_trace(m_params.get_outfile(), m_params.m_msec_output,
                                completed_through);
  if (m_params.m_verbose && !m_params.get_outfile().empty()) {
    std::cout << "Simulated trace written to: " << m_params.get_outfile()
              << std::endl;
  }
}

template <typename TraceType>
void BasicSimulation<TraceType>::flush_completed_jobs() {
  m_trace.flush_completed_jobs(m_current_time);
}

template <typename TraceType>
void BasicSimulation<TraceType>::write_resource_trace(
    const std::string &filename) {
  if (filename.empty()) {
    return;
  }

  // Trace's own context is populated identically regardless of trace
  // mode (both go through the same process_single_event()/
  // process_events_until() choke points), so this is unconditional now -
  // no more separate simulation-mode-only tracking to maintain here.
  m_trace.write_resource_trace(filename, m_params.m_total_nodes,
                               m_params.m_msec_output);
  if (m_params.m_verbose) {
    std::cout << "Resource trace written to: " << filename << std::endl;
  }
}

// Public API methods for online/streaming simulation mode
// Allow external code (e.g., gRPC server) to feed jobs and control simulation

template <typename TraceType>
job_no_t BasicSimulation<TraceType>::append_job(sim_time_t submit_time,
                                                num_nodes_t num_nodes,
                                                const std::string &queue,
                                                tdiff_t limit_time) {
  if (submit_time < m_current_time) {
    throw std::runtime_error(
        "Cannot append job with submit_time < current_time. "
        "submit_time=" +
        std::to_string(submit_time) +
        " but current_time=" + std::to_string(m_current_time));
  }

  // Online callers need not invoke run() first. Open incremental outputs before
  // this insertion can reclaim a completed record from a full job store.
  m_trace.set_job_flush_interval(m_params.m_job_flush_interval);
  m_trace.set_resource_history_capacity(m_params.m_resource_history_capacity);
  m_trace.start_resource_trace(m_params.get_resource_trace(),
                               m_params.m_total_nodes, m_params.m_msec_output);
  m_trace.start_simulated_trace(m_params.get_outfile(), m_params.m_msec_output);

  time_t sec = static_cast<time_t>(submit_time);
  float frac = submit_time - sec;
  epoch_t submit_epoch = {sec, frac};

  job_queue_t q = QueueUnknown;
#if DR_EVT_LEGACY_QUEUE_INPUT
  set_by(q, queue);
#else
  set_by_queue_id(q, queue);
#endif

  job_no_t job_idx = m_trace.append_job(m_current_time, submit_epoch, num_nodes,
                                        q, static_cast<timeout_t>(limit_time));
  submit_job(job_idx, submit_time);
  return job_idx;
}

template <typename TraceType>
std::vector<job_no_t> BasicSimulation<TraceType>::append_jobs(
    const std::vector<Job_Append_Request> &requests) {
  // Same one-time, idempotent setup as append_job(), once for this batch.
  m_trace.set_job_flush_interval(m_params.m_job_flush_interval);
  m_trace.set_resource_history_capacity(m_params.m_resource_history_capacity);
  m_trace.start_resource_trace(m_params.get_resource_trace(),
                               m_params.m_total_nodes, m_params.m_msec_output);
  m_trace.start_simulated_trace(m_params.get_outfile(), m_params.m_msec_output);

  // Validate every request's submit_time before appending any of them
  // - same precondition append_job() enforces per-job, checked here
  // for the whole batch up front (see this function's own doc
  // comment for what "all-or-nothing" does and doesn't cover).
  for (size_t i = 0; i < requests.size(); ++i) {
    if (requests[i].submit_time < m_current_time) {
      throw std::runtime_error(
          "Cannot append job with submit_time < current_time. "
          "request " +
          std::to_string(i) +
          " has submit_time=" + std::to_string(requests[i].submit_time) +
          " but current_time=" + std::to_string(m_current_time));
    }
  }

  std::vector<dr_evt::Job_Append_Request> trace_reqs;
  trace_reqs.reserve(requests.size());
  for (const auto &r : requests) {
    time_t sec = static_cast<time_t>(r.submit_time);
    float frac = r.submit_time - sec;
    job_queue_t q = QueueUnknown;
#if DR_EVT_LEGACY_QUEUE_INPUT
    set_by(q, r.queue);
#else
    set_by_queue_id(q, r.queue);
#endif
    trace_reqs.push_back(
        dr_evt::Job_Append_Request{epoch_t{sec, frac}, r.num_nodes, q,
                                   static_cast<timeout_t>(r.limit_time)});
  }

  std::vector<job_no_t> job_idxs =
      m_trace.append_jobs(m_current_time, trace_reqs);
  for (size_t i = 0; i < job_idxs.size(); ++i) {
    submit_job(job_idxs[i], requests[i].submit_time);
  }
  return job_idxs;
}

template <typename TraceType>
void BasicSimulation<TraceType>::submit_job(job_no_t job_idx,
                                            sim_time_t submit_time) {
  // Validate preconditions
  if (submit_time < m_current_time) {
    throw std::runtime_error(
        "Cannot submit job with submit_time < current_time. "
        "Job " +
        std::to_string(job_idx) +
        " has submit_time=" + std::to_string(submit_time) +
        " but current_time=" + std::to_string(m_current_time));
  }

  // Submit to scheduler (scheduler maintains internal wait queue)
  // Scheduler uses time_limit as the best estimator for planning
  // job_at() below throws its own clear, correctly-bounds-checked
  // error if job_idx is invalid - accounting for m_num_reclaimed,
  // unlike a manual "job_idx >= m_trace.data().size()" check would
  // (data().size() is m_data's *current* physical count, not the
  // total job count ever seen, once anything's been reclaimed).
  auto &job = m_trace.job_at(job_idx);
  tdiff_t run_time_estimate = job.get_limit_time();
  num_nodes_t nodes = job.get_num_nodes();

  if (nodes > m_job_rejection_capacity) {
    // This job can never be scheduled, regardless of how long the
    // simulation runs - total_nodes is fixed for the whole run, so
    // no future state ever frees up enough capacity. Reject it here,
    // before it ever enters the wait queue: leaving it there would
    // silently block every job behind it in FCFS order, and its
    // begin_time/end_time would stay at unscheduled_sentinel()
    // forever - exactly the "reaches output still unresolved" state
    // that shouldn't be possible.
    //
    // Also mark submit_time as the sentinel: m_data's front-reclaim
    // sweep treats that as "skip immediately, will never resolve" -
    // otherwise a rejected job at the front would stall the sweep
    // forever, since end_time never resolves for it either.
    job.set_submit_time(Job_Record::unscheduled_sentinel());
    std::cerr << "Job " << job_idx << " rejected: requests " << nodes
              << " nodes, exceeds total_nodes (" << m_job_rejection_capacity
              << "); this job can never be scheduled." << std::endl;
    return;
  }

  // Snapshot system occupancy at the moment of arrival - same
  // statistic load_data()'s own submission loop records
  // (Job_Record::m_busy_nodes, written out as the trace's
  // "busy_nodes" column), which submit_job() would otherwise never
  // populate for a streaming-arrived job (left at the constructor's
  // default of 0, silently wrong rather than merely unset). Must run
  // before insert_job() below, so it reflects other jobs' occupancy
  // at this moment, not including this job's own allocation.
#if MARK_DAT_PERIOD
  job.set_busy_nodes(get_nodes_in_use(), m_trace.in_dat_period());
#else
  job.set_busy_nodes(get_nodes_in_use());
#endif

  m_scheduler->insert_job(job_idx, submit_time, run_time_estimate, nodes);
  ++m_pending_queue_arrivals[submit_time];
}

template <typename TraceType>
void BasicSimulation<TraceType>::record_queue_arrivals(
    sim_time_t current_time) {
  auto arrivals = m_pending_queue_arrivals.find(current_time);
  if (arrivals == m_pending_queue_arrivals.end()) {
    return;
  }

  const size_t count = arrivals->second;
  const size_t active_after_sync = m_scheduler->active_job_count();
  if (active_after_sync < count) {
    throw std::logic_error(
        "scheduler queue contains fewer jobs than arrived at current time");
  }

  size_t jobs_already_waiting = active_after_sync - count;
  for (size_t i = 0; i < count; ++i) {
    m_queue_length_sum += jobs_already_waiting + i;
    ++m_queue_length_samples;
  }
  m_pending_queue_arrivals.erase(arrivals);
}

template <typename TraceType>
void BasicSimulation<TraceType>::reset_capacity_schedule() {
  m_next_capacity_change = 0;
  m_current_capacity = m_params.m_total_nodes;
  m_trace.reset_resource_capacity(m_params.m_total_nodes);
  reset_capacity_accounting(m_current_time);
}

template <typename TraceType>
void BasicSimulation<TraceType>::reset_capacity_accounting(
    sim_time_t start_time) {
  m_capacity_area = 0.0;
  m_capacity_area_time = start_time;
}

template <typename TraceType>
void BasicSimulation<TraceType>::advance_capacity_accounting_to(
    sim_time_t current_time) {
  if (!std::isfinite(current_time)) {
    return;
  }
  if (current_time < m_capacity_area_time) {
    throw std::logic_error("capacity accounting cannot move backward in time");
  }
  const num_nodes_t effective_capacity =
      std::max(m_current_capacity, get_nodes_in_use());
  m_capacity_area += static_cast<tdiff_t>(effective_capacity) *
                     (current_time - m_capacity_area_time);
  m_capacity_area_time = current_time;
}

template <typename TraceType>
tdiff_t BasicSimulation<TraceType>::capacity_area_through(
    sim_time_t through_time) const {
  tdiff_t area = m_capacity_area;
  if (std::isfinite(through_time) && through_time > m_capacity_area_time) {
    const num_nodes_t effective_capacity =
        std::max(m_current_capacity, get_nodes_in_use());
    area += static_cast<tdiff_t>(effective_capacity) *
            (through_time - m_capacity_area_time);
  }
  return area;
}

template <typename TraceType>
bool BasicSimulation<TraceType>::apply_capacity_changes(
    sim_time_t current_time) {
  bool changed = false;
  while (m_next_capacity_change < m_capacity_changes.size() &&
         m_capacity_changes[m_next_capacity_change].time <= current_time) {
    const auto &change = m_capacity_changes[m_next_capacity_change++];
    m_current_capacity = change.total_nodes;
    m_trace.set_resource_capacity(change.time, change.total_nodes);
    changed = true;
  }
  return changed;
}

template <typename TraceType>
void BasicSimulation<TraceType>::advance_to(sim_time_t target_time) {
  if (m_custom_scheduler != nullptr) {
    advance_to_impl<true, false>(target_time, m_custom_scheduler);
  } else {
    advance_to_impl<false, false>(target_time, nullptr);
  }
}

template <typename TraceType>
template <bool AccountResources, bool WarmStage>
void BasicSimulation<TraceType>::advance_to_impl(
    sim_time_t target_time, CustomFCFSScheduler *custom_scheduler) {
  if constexpr (!AccountResources) {
    (void)custom_scheduler;
  }

  // Validate precondition
  if (target_time < m_current_time) {
    throw std::runtime_error(
        "Cannot advance backwards in time. "
        "target_time=" +
        std::to_string(target_time) +
        " but current_time=" + std::to_string(m_current_time));
  }

  if constexpr (AccountResources) {
    custom_scheduler->advance_resource_accounting_to(m_current_time);
  }

  // Before entering the main loop, account for jobs submitted at the current
  // time and check whether any are eligible. This handles t=0 initially and
  // jobs appended at the current time by a streaming caller.
  m_scheduler->sync_to(m_current_time);
  record_queue_arrivals(m_current_time);
  apply_capacity_changes(m_current_time);
  if (m_scheduler->has_eligible_jobs()) {
    // Call scheduler to evaluate newly arriving jobs
    while (true) {
      num_nodes_t free_nodes = get_available_nodes();
      auto jobs_to_run =
          m_scheduler->schedule(free_nodes, m_running_jobs, m_current_time);

      if (jobs_to_run.empty()) {
        break;
      }

      // Process ALL jobs returned by scheduler (backfilling can return
      // multiple)
      for (job_no_t job : jobs_to_run) {
        m_trace.insert_job(job, m_current_time);
        const auto &record = m_trace.job_at(job);
        m_running_jobs[job] = {m_current_time,
                               static_cast<tdiff_t>(record.get_limit_time()),
                               record.get_num_nodes()};
        m_jobs_submitted++;

        // Records a resource-history sample internally (Trace's own
        // Context, via process_events_until()) - no separate call needed.
        m_trace.run_until_inclusive(m_current_time);
      }
    }
  }
  if constexpr (AccountResources) {
    custom_scheduler->commit_available_nodes(get_available_nodes());
  }

  // Main event loop - process events and make scheduling decisions until
  // complete Compute loop state variables once before entering loop
  size_t active_count = m_scheduler->active_job_count();
  sim_time_t next_arrival = m_scheduler->get_next_arrival_time();
  sim_time_t next_capacity =
      m_next_capacity_change < m_capacity_changes.size()
          ? m_capacity_changes[m_next_capacity_change].time
          : std::numeric_limits<sim_time_t>::max();

  m_queue_length_peak = std::max(m_queue_length_peak, active_count);

  // Continue while: (1) jobs are waiting, (2) jobs are running, or (3) jobs
  // will arrive. A finite streaming advance also consumes capacity changes
  // through its requested boundary even while idle. By contrast, batch mode
  // drains to max() and must not let unused schedule entries keep a completed
  // simulation alive or emit irrelevant resource rows.
  while (active_count > 0 || !m_trace.pending_events().empty() ||
         next_arrival < std::numeric_limits<sim_time_t>::max() ||
         (target_time < std::numeric_limits<sim_time_t>::max() &&
          next_capacity <= target_time)) {
    if (m_params.m_verbose) {
      std::cout << "Loop iter: active=" << active_count
                << " events=" << m_trace.pending_events().size()
                << " next_arrival=" << next_arrival
                << " time=" << m_current_time << std::endl;
    }
    // next_arrival already computed above

    // Find next replay event time
    bool has_replay_event = !m_trace.pending_events().empty();
    sim_time_t next_replay_time = std::numeric_limits<sim_time_t>::max();
    [[maybe_unused]] bool next_is_start = false;
    if (has_replay_event) {
      const auto &event = *m_trace.pending_events().begin();
      next_replay_time = convert_epoch<sim_time_t>(event.get_time());
      next_is_start = event.is_arrival();
    }

    // Decide which event to process
    bool should_schedule = false;

    if (has_replay_event && next_replay_time <= next_arrival &&
        next_replay_time <= next_capacity && next_replay_time <= target_time) {
      // Process replay events at this time
      // Advance time FIRST
      m_current_time = next_replay_time;
      advance_capacity_accounting_to(m_current_time);
      if constexpr (AccountResources) {
        custom_scheduler->advance_resource_accounting_to(m_current_time);
      }
      // Explicit sync: schedule() below only runs if an END event
      // freed resources (should_schedule = processed_end_event).
      // Without this call, a replay step that only processes START
      // events would leave the scheduler's eligibility tracking
      // stale relative to m_current_time, since nothing else
      // would sync it before the queries below.
      m_scheduler->sync_to(m_current_time);
      record_queue_arrivals(m_current_time);

      // Process ALL events at current_time before calling scheduler
      // This ensures END events are processed before START events created by
      // scheduler
      bool processed_end_event = false;

      while (!m_trace.pending_events().empty()) {
        const auto &event = *m_trace.pending_events().begin();
        sim_time_t event_time = convert_epoch<sim_time_t>(event.get_time());

        if (event_time != m_current_time) {
          break; // No more events at current_time
        }

        bool is_end = !event.is_arrival();
        job_no_t event_job_idx = event.get_job_idx();

        if (is_end) {
          if constexpr (WarmStage) {
            auto &job = m_trace.job_at(event_job_idx);
            const sim_time_t begin =
                convert_epoch<sim_time_t>(job.get_begin_time());
            if (begin < m_params.m_sim_start_time) {
              // Mutate before Trace processes the departure: a periodic flush
              // at this event can then neither write nor account the seed.
              job.suppress_output();
              if (m_pre_start_jobs == 0) {
                throw std::logic_error(
                    "warm-start historical-job count underflow");
              }
              --m_pre_start_jobs;
            } else {
              ++m_jobs_completed;
            }
          } else {
            ++m_jobs_completed;
          }
        }

        // Process this event (END or START) - records a
        // resource-history sample internally (Trace's own Context).
        m_trace.process_single_event();

        // If END event: remove from running_jobs
        if (is_end) {
          processed_end_event = true;
          m_running_jobs.erase(event_job_idx);
        }
      }

      const bool capacity_changed = apply_capacity_changes(m_current_time);

      // Only call scheduler if we processed END events (resources freed)
      should_schedule = processed_end_event || capacity_changed ||
                        next_arrival == m_current_time;

    } else if (next_arrival < std::numeric_limits<sim_time_t>::max() &&
               next_arrival <= next_capacity && next_arrival <= target_time) {
      // Job arrival - advance time FIRST
      // Note: Check next_arrival < infinity to avoid infinite loop
      // If no jobs arriving, scheduler should pick from waiting queue instead
      m_current_time = next_arrival;
      advance_capacity_accounting_to(m_current_time);
      if constexpr (AccountResources) {
        custom_scheduler->advance_resource_accounting_to(m_current_time);
      }
      // Explicit sync, matching the replay-event branch above for
      // symmetry - should_schedule is always true in this branch
      // (set unconditionally below), so schedule()'s own internal
      // sync_to() call would already cover this in practice, but
      // this doesn't rely on that.
      m_scheduler->sync_to(m_current_time);
      record_queue_arrivals(m_current_time);
      apply_capacity_changes(m_current_time);

      // jobs_at_next_arrival already collected during wait_queue scan
      // TODO: Pass jobs_at_next_arrival to scheduler for efficient evaluation
      // For now, just set flag to schedule
      should_schedule = true;
    } else if (next_capacity < std::numeric_limits<sim_time_t>::max() &&
               next_capacity <= target_time) {
      m_current_time = next_capacity;
      advance_capacity_accounting_to(m_current_time);
      if constexpr (AccountResources) {
        custom_scheduler->advance_resource_accounting_to(m_current_time);
      }
      m_scheduler->sync_to(m_current_time);
      record_queue_arrivals(m_current_time);
      apply_capacity_changes(m_current_time);
      should_schedule = true;
    } else {
      // No arrivals and no replay events before target_time
      if (m_params.m_verbose) {
        std::cout << "ELSE block: active=" << m_scheduler->active_job_count()
                  << " events=" << m_trace.pending_events().size()
                  << " time=" << m_current_time << std::endl;
      }
      // No events to process - exit loop
      break;
    }

    // Scheduling loop - let scheduler make decisions after processing END
    // events
    if (should_schedule) {
      // Keep calling scheduler until it can't start any more jobs
      while (true) {
        num_nodes_t free_nodes = get_available_nodes();

        auto jobs_to_run =
            m_scheduler->schedule(free_nodes, m_running_jobs, m_current_time);

        if (jobs_to_run.empty()) {
          break; // Scheduler can't start anything else
        }

        // Process ALL jobs returned by scheduler (backfilling can return
        // multiple)
        for (job_no_t job : jobs_to_run) {
          m_trace.insert_job(job, m_current_time);
          const auto &record = m_trace.job_at(job);
          m_running_jobs[job] = {m_current_time,
                                 static_cast<tdiff_t>(record.get_limit_time()),
                                 record.get_num_nodes()};
          m_jobs_submitted++;

          // Process this START event - records a resource-history
          // sample internally (Trace's own Context).
          while (!m_trace.pending_events().empty()) {
            const auto &event = *m_trace.pending_events().begin();
            sim_time_t event_time = convert_epoch<sim_time_t>(event.get_time());

            // Only process START events at current_time for this job
            if (event_time != m_current_time)
              break;
            if (!event.is_arrival())
              break; // Hit an END event, stop (shouldn't happen)

            m_trace.process_single_event();
            break; // Process only one START event per job
          }
        }
      }
    }
    if constexpr (AccountResources) {
      custom_scheduler->commit_available_nodes(get_available_nodes());
    }

    // Update loop state variables at end of iteration
    active_count = m_scheduler->active_job_count();
    next_arrival = m_scheduler->get_next_arrival_time();
    next_capacity = m_next_capacity_change < m_capacity_changes.size()
                        ? m_capacity_changes[m_next_capacity_change].time
                        : std::numeric_limits<sim_time_t>::max();

    // Peak queue length after all events and scheduling at this timestamp.
    m_queue_length_peak = std::max(m_queue_length_peak, active_count);

    if constexpr (WarmStage) {
      // Transition only after every peer event and scheduling decision at the
      // last historical departure's timestamp has settled.
      if (m_pre_start_jobs == 0) {
        return;
      }
    }
  }

  // Loop exited - log final state for debugging
  if (m_params.m_verbose) {
    std::cout << "Loop exited: active=" << active_count
              << " events=" << m_trace.pending_events().size()
              << " time=" << m_current_time << std::endl;
  }

  // Don't record spurious final state - last event already recorded the final
  // state

  // Honor the documented postcondition (m_current_time == target_time)
  // even when the loop above exited early because nothing was left to
  // process before target_time (an idle gap - e.g. all currently-known
  // jobs finished, and the next arrival, if any, is later than
  // target_time). Without this, m_current_time stays stuck at the last
  // real event, silently understating elapsed time to any caller of
  // get_current_time() and weakening submit_job()'s own precondition
  // check (submit_time < m_current_time) against a stale value. This
  // is pure bookkeeping - every scheduling decision above was already
  // made using real event times, never target_time, so this can't
  // change any of them.
  const bool drain_to_completion =
      target_time == std::numeric_limits<sim_time_t>::max();
  if constexpr (AccountResources) {
    if (!drain_to_completion && m_trace.get_nodes_in_use() > 0) {
      custom_scheduler->advance_resource_accounting_to(target_time);
      advance_capacity_accounting_to(target_time);
    }
  }
  // max() is the internal/public drain sentinel, not a meaningful simulated
  // timestamp. After a drain, preserve the last real event time rather than
  // exposing max() (which also overflows integer-formatted CLI output).
  if (!drain_to_completion) {
    m_current_time = target_time;
  }
}

template <typename TraceType>
num_nodes_t BasicSimulation<TraceType>::get_nodes_in_use() const {
  return m_trace.get_nodes_in_use();
}

template <typename TraceType>
tdiff_t BasicSimulation<TraceType>::get_resource_area() const {
  if (m_custom_scheduler == nullptr) {
    throw std::logic_error(
        "resource-area accounting is available only with Custom FCFS");
  }
  return m_custom_scheduler->resource_area_through(m_current_time);
}

template <typename TraceType>
typename BasicSimulation<TraceType>::Backfill_Window
BasicSimulation<TraceType>::get_backfill_window() const {
  Backfill_Window window{
      m_current_time, get_available_nodes(), get_fcfs_head_shadow_time(), {}};

  // A head that can start now has no future window to describe.  Likewise,
  // without a waiting head there is no EASY reservation.
  if (window.shadow_time <= m_current_time) {
    return window;
  }

  // m_running_jobs stores the actual start time. The scheduler reserves
  // against each job's limit time, so this deliberately does the same.
  std::map<sim_time_t, num_nodes_t> releases_by_time;
  for (const auto &[job_idx, job] : m_running_jobs) {
    (void)job_idx;
    const sim_time_t end_time = job.start_time + job.run_time;
    if (end_time > m_current_time && end_time <= window.shadow_time) {
      releases_by_time[end_time] += job.nodes;
    }
  }

  window.releases.reserve(releases_by_time.size());
  for (const auto &[time, nodes] : releases_by_time) {
    window.releases.push_back({time, nodes});
  }
  return window;
}

template <typename TraceType>
tdiff_t
BasicSimulation<TraceType>::get_prediction_horizon(double utilization) const {
  if (m_custom_scheduler == nullptr) {
    throw std::logic_error(
        "prediction horizon is available only with Custom FCFS");
  }
  return m_custom_scheduler->prediction_horizon(m_running_jobs, m_current_time,
                                                utilization);
}

template <typename TraceType>
typename BasicSimulation<TraceType>::Statistics
BasicSimulation<TraceType>::get_statistics() const {
  Statistics stats;

  // Basic counters
  stats.jobs_submitted = m_jobs_submitted;
  stats.jobs_completed = m_jobs_completed;
  stats.jobs_running = m_running_jobs.size();
  stats.jobs_waiting = m_scheduler->active_job_count();
  stats.current_time = m_current_time;

  // Resource utilization
  stats.total_nodes = m_params.m_total_nodes;
  stats.nodes_in_use = get_nodes_in_use();
  stats.nodes_available = get_available_nodes();

  // Calculate wait times and turnaround times
  tdiff_t total_wait = 0.0;
  tdiff_t total_turnaround = 0.0;
  sim_time_t max_completion = 0.0;
  sim_time_t max_scheduled_completion = 0.0;
  num_jobs_t completed_count = 0;
  tdiff_t total_node_seconds = 0.0;

  for (const auto &job : m_trace.data()) {
    // Only count jobs that actually completed. Job_Record::is_scheduled()
    // (backed by a dedicated max-value sentinel) is the correct check
    // here: a job legitimately starting at simulation time 0 has
    // begin_time/end_time == 0 under the old convention, which the
    // previous begin_time-based check incorrectly treated as "never
    // started," silently excluding it from these averages. This
    // matches the same convention now used for m_jobs_completed
    // above (see the end-of-run() completion count).
    if (!job.is_scheduled()) {
      continue;
    }

    const sim_time_t completion =
        convert_epoch<sim_time_t>(job.get_end_time());
    max_scheduled_completion = std::max(max_scheduled_completion, completion);
    total_node_seconds +=
        static_cast<tdiff_t>(job.get_num_nodes()) * job.get_actual_run_time();
    if (completion <= stats.current_time) {
      tdiff_t wait = job.get_wait_time();
      tdiff_t exec = job.get_actual_run_time();

      total_wait += wait;
      total_turnaround += (wait + exec);
      max_completion = std::max(max_completion, completion);
      completed_count++;
    }

  }

  stats.avg_wait_time =
      (completed_count > 0) ? total_wait / completed_count : 0.0;
  stats.avg_turnaround_time =
      (completed_count > 0) ? total_turnaround / completed_count : 0.0;
  stats.makespan = max_completion;

  if (m_custom_scheduler != nullptr) {
    // Custom FCFS maintains live resource-area accounting at settled
    // scheduling boundaries. It remains accurate for partial streaming
    // snapshots and does not add bookkeeping to any other scheduler.
    stats.resource_area =
        m_custom_scheduler->resource_area_through(stats.current_time);
    const sim_time_t accounting_horizon =
        std::isfinite(stats.current_time) && stats.nodes_in_use > 0
            ? stats.current_time
            : m_custom_scheduler->m_resource_area_time;
    const tdiff_t capacity_area = capacity_area_through(accounting_horizon);
    stats.utilization =
        capacity_area > 0.0 ? stats.resource_area / capacity_area : 0.0;
  } else {
    // Preserve the original post-hoc statistic for standard schedulers.
    stats.resource_area = total_node_seconds + m_warm_resource_area;
    const sim_time_t accounting_end =
        std::max(max_scheduled_completion, m_warm_resource_end);
    const tdiff_t capacity_area = capacity_area_through(accounting_end);
    stats.utilization =
        capacity_area > 0.0 ? stats.resource_area / capacity_area : 0.0;
  }

  return stats;
}

template class BasicSimulation<Trace>;
template class BasicSimulation<PconTrace>;

} // namespace dr_evt
