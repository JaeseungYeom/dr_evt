#!/usr/bin/env python3
"""Replay the system assignments recorded by a prior dispatch run.

This script does not dispatch jobs again. It reads each job's recorded
``system_id`` from a dispatch CSV, submits that job to the same system's
independent DR_EVT simulation, and optionally recalculates its submitted wall
time. This isolates scheduling effects while keeping the original dispatch
decisions unchanged.
"""

import argparse
import csv
import math
import sys
from pathlib import Path

import dr_evt


def read_system_sizes(path):
    """Return configured node capacity keyed by system identifier."""
    with path.open(newline="", encoding="utf-8") as stream:
        return {
            row["#machine"]: int(row["size"])
            for row in csv.DictReader(stream)
        }


def submitted_limit(row, policy, maximum):
    """Calculate the requested limit for one recorded dispatch decision."""
    actual = float(row["actual_duration"])
    if policy == "actual-duration":
        return math.ceil(actual)

    limit = min(float(row["predicted_time_limit"]), maximum)
    while limit < actual and limit < maximum:
        limit = min(limit * 2.0, maximum)
    limit = math.ceil(limit)
    if limit > maximum:
        raise ValueError(f"job {row['job_id']} exceeds maximum limit")
    return limit


def replay(
    mapping,
    systems_path,
    policy,
    maximum,
    details_output=None,
    submit_policy="original",
):
    """Replay recorded dispatch assignments in independent simulations."""
    sizes = read_system_sizes(systems_path)
    queue = "pbatch" if dr_evt.legacy_queue_input else "1"
    total_turnaround = 0.0
    total_wait = 0.0
    completed = 0
    arrivals = 0
    detail_stream = None
    detail_writer = None
    if details_output is not None:
        detail_stream = details_output.open("w", newline="", encoding="utf-8")
        detail_writer = csv.writer(detail_stream, lineterminator="\n")
        detail_writer.writerow(
            (
                "job_id",
                "system_id",
                "submit_time",
                "start_time",
                "end_time",
                "wait_time",
                "turnaround_time",
                "num_nodes",
                "actual_duration",
                "submitted_time_limit",
                "node_seconds",
            )
        )
    for system, nodes in sizes.items():
        params = dr_evt.SimParams()
        params.infile = str(mapping)
        params.total_nodes = nodes
        params.trace_format = "simple"
        params.timestamp_format = "epoch"
        params.run_time_mode = dr_evt.RunTimeMode.ACTUAL
        params.backfill_policy = dr_evt.BackfillPolicy.EASY
        params.priority_policy = dr_evt.PriorityPolicy.FCFS
        params.verbose = False
        simulation = dr_evt.Simulation(params)
        system_arrivals = 0
        first_submit = math.inf
        node_seconds = 0.0
        job_records = []
        with mapping.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["system_id"] != system:
                    continue
                original_submit_time = float(row["submit_time"])
                submit_time = (
                    0.0
                    if submit_policy == "all-at-zero"
                    else original_submit_time
                )
                actual_duration = float(row["actual_duration"])
                requested_nodes = int(row["effective_nodes"])
                first_submit = min(first_submit, submit_time)
                node_seconds += requested_nodes * actual_duration
                simulation.advance_to(submit_time)
                limit = submitted_limit(row, policy, maximum)
                internal_id = simulation.append_job(
                    submit_time,
                    requested_nodes,
                    queue,
                    limit,
                    actual_duration,
                )
                job_records.append(
                    (
                        internal_id,
                        row["job_id"],
                        submit_time,
                        requested_nodes,
                        actual_duration,
                        limit,
                    )
                )
                simulation.advance_to(submit_time)
                system_arrivals += 1
        simulation.advance_to(sys.float_info.max)
        stats = simulation.get_statistics()
        if stats.jobs_submitted != system_arrivals:
            raise RuntimeError(
                f"{system}: submitted {stats.jobs_submitted} of "
                f"{system_arrivals} jobs"
            )
        if stats.jobs_submitted != stats.jobs_completed:
            raise RuntimeError(f"{system}: incomplete simulation")
        total_turnaround += stats.avg_turnaround_time * stats.jobs_completed
        total_wait += stats.avg_wait_time * stats.jobs_completed
        completed += stats.jobs_completed
        arrivals += system_arrivals
        workload_horizon = stats.makespan - first_submit
        interval_utilization = node_seconds / (nodes * workload_horizon)
        estimated_makespan = node_seconds / (interval_utilization * nodes)
        if detail_writer is not None:
            statuses = simulation.get_job_statuses(
                [record[0] for record in job_records]
            )
            for record, status in zip(job_records, statuses, strict=True):
                _, job_id, submit_time, requested_nodes, actual, limit = record
                start_time = status.start_time
                end_time = status.end_time
                if start_time is None or end_time is None:
                    raise RuntimeError(f"{system}: job {job_id} is incomplete")
                detail_writer.writerow(
                    (
                        job_id,
                        system,
                        submit_time,
                        start_time,
                        end_time,
                        start_time - submit_time,
                        end_time - submit_time,
                        requested_nodes,
                        actual,
                        limit,
                        requested_nodes * actual,
                    )
                )
        print(
            f"{system}: jobs={stats.jobs_completed} "
            f"average_wait={stats.avg_wait_time:.8g} "
            f"average_turnaround={stats.avg_turnaround_time:.8g} "
            f"node_seconds={node_seconds:.8g} capacity_nodes={nodes} "
            f"makespan={workload_horizon:.8g} "
            f"interval_utilization={interval_utilization:.8g} "
            f"formula_makespan={estimated_makespan:.8g}"
        )

    if completed != arrivals:
        raise RuntimeError(f"completed {completed} of {arrivals} jobs")
    print(
        f"overall: jobs={completed} average_wait={total_wait / completed:.8g} "
        f"average_turnaround={total_turnaround / completed:.8g}"
    )
    if detail_stream is not None:
        detail_stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", type=Path, help="recorded dispatch CSV")
    parser.add_argument("--systems", required=True, type=Path)
    parser.add_argument(
        "--limit-policy",
        required=True,
        choices=("adapted-limit", "actual-duration"),
    )
    parser.add_argument("--max-time-limit", type=float, default=43200.0)
    parser.add_argument(
        "--submit-policy",
        choices=("original", "all-at-zero"),
        default="original",
        help="preserve recorded arrivals or submit every job at time zero",
    )
    parser.add_argument(
        "--details-output",
        type=Path,
        help="optional per-job realized scheduling CSV",
    )
    args = parser.parse_args()
    replay(
        args.mapping,
        args.systems,
        args.limit_policy,
        args.max_time_limit,
        args.details_output,
        args.submit_policy,
    )


if __name__ == "__main__":
    main()
