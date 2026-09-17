#!/usr/bin/env python3
"""Build a simulation trace whose initial allocation matches history at t."""

import argparse
import csv
from datetime import datetime
import os
import sys
import time


def parse_time(value, timezone):
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        return parsed.timestamp()
    old_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = timezone
        time.tzset()
        return time.mktime(parsed.timetuple()) + parsed.microsecond / 1e6
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def value(row, *names):
    for name in names:
        if name in row and row[name] != "":
            return row[name]
    raise ValueError("missing required column (one of: {})".format(
        ", ".join(names)))


def fmt(number):
    return "{:.15g}".format(number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("historical_trace")
    parser.add_argument("output_trace")
    parser.add_argument("--start-time", required=True,
                        help="epoch seconds or ISO timestamp")
    parser.add_argument("--workload-trace",
                        help="future simulation input; defaults to the historical trace")
    parser.add_argument("--timezone", default="America/Los_Angeles",
                        help="timezone for ISO timestamps without an offset")
    parser.add_argument("--total-nodes", type=int,
                        help="fail if initialization jobs exceed this capacity")
    args = parser.parse_args()

    start = parse_time(args.start_time, args.timezone)
    with open(args.historical_trace, newline="") as stream:
        history = list(csv.DictReader(stream))

    initial = []
    for index, row in enumerate(history):
        begin = parse_time(value(row, "begin_time", "start_time"), args.timezone)
        end = parse_time(value(row, "end_time"), args.timezone)
        # A job that begins exactly at the boundary is running at that
        # boundary and must be installed before ordinary arrivals at the same
        # timestamp. Jobs ending at the boundary are already complete.
        if begin <= start < end:
            nodes = int(value(row, "num_nodes", "nodes", "nodes_requested"))
            remaining = end - start
            initial.append({
                "job_submit_time": fmt(start),
                "num_nodes": str(nodes),
                "time_limit": fmt(remaining),
                "actual_run_time": fmt(remaining),
                "initialization": "1",
                "source_job_index": str(index),
            })

    required_nodes = sum(int(row["num_nodes"]) for row in initial)
    if args.total_nodes is not None and required_nodes > args.total_nodes:
        raise SystemExit(
            "initial state requires {} nodes, exceeding --total-nodes={}".format(
                required_nodes, args.total_nodes))

    workload_path = args.workload_trace or args.historical_trace
    with open(workload_path, newline="") as stream:
        workload = list(csv.DictReader(stream))

    future = []
    for index, row in enumerate(workload):
        submit = parse_time(value(row, "job_submit_time", "submit_time"),
                            args.timezone)
        if submit < start:
            continue
        nodes = int(value(row, "num_nodes", "nodes", "nodes_requested"))
        limit = float(value(row, "time_limit", "requested_time", "wall_time"))
        actual = None
        for name in ("actual_run_time", "duration", "actual_duration", "run_time"):
            if row.get(name, "") != "":
                actual = float(row[name])
                break
        if actual is None and row.get("begin_time", "") and row.get("end_time", ""):
            actual = (parse_time(row["end_time"], args.timezone) -
                      parse_time(row["begin_time"], args.timezone))
        if actual is None:
            actual = limit
        future.append({
            "job_submit_time": fmt(submit),
            "num_nodes": str(nodes),
            "time_limit": fmt(limit),
            "actual_run_time": fmt(actual),
            "initialization": "0",
            "source_job_index": str(index),
        })

    # Initialization rows precede ordinary arrivals at the same timestamp, so
    # stable FCFS insertion establishes historical occupancy first.
    rows = initial + future
    rows.sort(key=lambda row: (float(row["job_submit_time"]),
                               0 if row["initialization"] == "1" else 1,
                               int(row["source_job_index"])))
    fields = ["job_submit_time", "num_nodes", "time_limit",
              "actual_run_time", "initialization", "source_job_index"]
    with open(args.output_trace, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("initialization_jobs={}".format(len(initial)))
    print("initialization_nodes={}".format(required_nodes))
    print("future_jobs={}".format(len(future)))
    print("output={}".format(args.output_trace))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("error: {}".format(error), file=sys.stderr)
        sys.exit(1)
