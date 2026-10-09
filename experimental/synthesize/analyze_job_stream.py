#!/usr/bin/env python3
"""Calculate capacity and scheduling metrics from a completed job trace."""

import argparse
import csv
import heapq
import math
from pathlib import Path


REQUIRED_FIELDS = ("num_nodes", "begin_time", "end_time")


def read_jobs(path):
    """Read and validate (begin, end, nodes, submit) records from *path*."""
    jobs = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        missing = [
            field for field in REQUIRED_FIELDS if field not in reader.fieldnames
        ]
        submit_field = (
            "submit_time"
            if reader.fieldnames and "submit_time" in reader.fieldnames
            else "job_submit_time"
        )
        if not reader.fieldnames or submit_field not in reader.fieldnames:
            missing.append("submit_time or job_submit_time")
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                submit = float(row[submit_field])
                begin = float(row["begin_time"])
                end = float(row["end_time"])
                nodes = int(row["num_nodes"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{row_number}: invalid job record") from error
            if not all(math.isfinite(value) for value in (submit, begin, end)):
                raise ValueError(f"{path}:{row_number}: timestamps must be finite")
            if nodes <= 0:
                raise ValueError(f"{path}:{row_number}: num_nodes must be positive")
            if not submit <= begin <= end:
                raise ValueError(
                    f"{path}:{row_number}: require submit_time <= begin_time <= end_time"
                )
            jobs.append((begin, end, nodes, submit))
    if not jobs:
        raise ValueError(f"{path}: no jobs")
    return jobs


def peak_concurrent_nodes(jobs):
    """Return the largest node allocation active at the same instant."""
    active = []
    active_nodes = 0
    peak_nodes = 0
    peak_time = 0.0
    for begin, end, nodes, _ in sorted(jobs):
        if begin == end:
            continue
        while active and active[0][0] <= begin:
            _, released_nodes = heapq.heappop(active)
            active_nodes -= released_nodes
        active_nodes += nodes
        heapq.heappush(active, (end, nodes))
        if active_nodes > peak_nodes:
            peak_nodes = active_nodes
            peak_time = begin
    return peak_nodes, peak_time


def calculate_metrics(jobs, total_nodes=None, slowdown_bound=10.0):
    """Calculate trace-wide scheduling metrics."""
    if total_nodes is not None and total_nodes <= 0:
        raise ValueError("total_nodes must be positive")
    if not math.isfinite(slowdown_bound) or slowdown_bound <= 0:
        raise ValueError("slowdown_bound must be finite and positive")

    peak_nodes, peak_time = peak_concurrent_nodes(jobs)
    capacity = peak_nodes if total_nodes is None else total_nodes
    first_submit = min(job[3] for job in jobs)
    last_end = max(job[1] for job in jobs)
    observation_time = last_end - first_submit
    if observation_time <= 0:
        raise ValueError("trace observation interval must be positive")

    total_node_seconds = 0.0
    total_turnaround = 0.0
    total_duration = 0.0
    total_bounded_slowdown = 0.0
    for begin, end, nodes, submit in jobs:
        duration = end - begin
        turnaround = end - submit
        total_node_seconds += nodes * duration
        total_turnaround += turnaround
        total_duration += duration
        total_bounded_slowdown += max(
            1.0, turnaround / max(duration, slowdown_bound)
        )

    count = len(jobs)
    return {
        "jobs": count,
        "largest_job_nodes": max(job[2] for job in jobs),
        "peak_concurrent_nodes": peak_nodes,
        "peak_time": peak_time,
        "operational_nodes": capacity,
        "capacity_source": "peak_concurrent" if total_nodes is None else "provided",
        "first_submit_time": first_submit,
        "last_end_time": last_end,
        "observation_time_seconds": observation_time,
        "total_node_seconds": total_node_seconds,
        "utilization": total_node_seconds / (capacity * observation_time),
        "average_turnaround_time_seconds": total_turnaround / count,
        "average_bounded_slowdown": total_bounded_slowdown / count,
        "average_duration_seconds": total_duration / count,
        "bounded_slowdown_threshold_seconds": slowdown_bound,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="completed job-trace CSV")
    parser.add_argument(
        "--total-nodes",
        type=int,
        help="known system capacity; default: inferred peak concurrent nodes",
    )
    parser.add_argument(
        "--bounded-slowdown-threshold",
        type=float,
        default=10.0,
        help="runtime lower bound in seconds (default: 10)",
    )
    args = parser.parse_args()

    try:
        metrics = calculate_metrics(
            read_jobs(args.input),
            total_nodes=args.total_nodes,
            slowdown_bound=args.bounded_slowdown_threshold,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    for name, value in metrics.items():
        if isinstance(value, float):
            print(f"{name}={value:.10g}")
        else:
            print(f"{name}={value}")


if __name__ == "__main__":
    main()
