#!/usr/bin/env python3
"""Detect review candidates for reduced capacity or a paused queue."""

import argparse
import csv
from datetime import datetime
import math
import os
import sys
import time


def parse_time(value, timezone):
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def pick(row, *names):
    for name in names:
        if row.get(name, "") != "":
            return row[name]
    raise ValueError("missing required column (one of: {})".format(
        ", ".join(names)))


def merge_periods(periods, minimum_duration):
    periods.sort(key=lambda item: (item[0], item[1]))
    merged = []
    for start, end, reason, peak_allocated, peak_waiting in periods:
        if merged and start <= merged[-1][1]:
            current = merged[-1]
            current[1] = max(current[1], end)
            current[2].add(reason)
            current[3] = max(current[3], peak_allocated)
            current[4] = max(current[4], peak_waiting)
        else:
            merged.append([start, end, {reason}, peak_allocated, peak_waiting])
    return [item for item in merged if item[1] - item[0] >= minimum_duration]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("historical_trace")
    parser.add_argument("candidates_csv")
    parser.add_argument("--capacity-schedule",
                        help="also write simulator time,total_nodes CSV")
    parser.add_argument("--total-nodes", type=int,
                        help="known normal capacity; defaults to observed peak")
    parser.add_argument("--utilization-threshold", type=float, default=0.25)
    parser.add_argument("--minimum-duration", type=float, default=3600.0)
    parser.add_argument("--minimum-waiting-jobs", type=int, default=1)
    parser.add_argument("--capacity-headroom", type=float, default=1.05)
    parser.add_argument("--queue-id", action="append", default=[],
                        help="limit detection to these q_id values (repeatable)")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    args = parser.parse_args()
    if not 0.0 <= args.utilization_threshold <= 1.0:
        parser.error("--utilization-threshold must be between 0 and 1")
    if args.minimum_duration <= 0 or args.capacity_headroom < 1.0:
        parser.error("duration must be positive and headroom must be >= 1")

    selected_queues = set(args.queue_id)
    events = {}
    with open(args.historical_trace, newline="") as stream:
        for row in csv.DictReader(stream):
            queue = row.get("q_id", row.get("queue", ""))
            if selected_queues and queue not in selected_queues:
                continue
            submit = parse_time(pick(row, "job_submit_time", "submit_time"),
                                args.timezone)
            begin = parse_time(pick(row, "begin_time", "start_time"),
                               args.timezone)
            end = parse_time(pick(row, "end_time"), args.timezone)
            nodes = int(pick(row, "num_nodes", "nodes", "nodes_requested"))
            if not submit <= begin <= end:
                raise ValueError("job times must satisfy submit <= begin <= end")
            events.setdefault(submit, [0, 0, 0])[0] += 1
            events.setdefault(begin, [0, 0, 0])[0] -= 1
            events[begin][1] += nodes
            events[begin][2] += 1
            events.setdefault(end, [0, 0, 0])[1] -= nodes

    if not events:
        raise ValueError("no matching jobs found")
    times = sorted(events)
    allocated = 0
    waiting = 0
    peak = 0
    states = []
    waiting_since = None
    last_start = None
    for pos, point in enumerate(times[:-1]):
        wait_delta, allocation_delta, starts = events[point]
        old_waiting = waiting
        waiting += wait_delta
        allocated += allocation_delta
        if waiting < 0 or allocated < 0:
            raise ValueError("trace produces negative waiting/allocation state")
        peak = max(peak, allocated)
        if waiting == 0:
            waiting_since = None
        elif old_waiting == 0 or waiting_since is None:
            waiting_since = point
        if starts:
            last_start = point
        states.append((point, times[pos + 1], allocated, waiting,
                       waiting_since, last_start))

    normal_capacity = args.total_nodes if args.total_nodes is not None else peak
    if normal_capacity <= 0:
        raise ValueError("normal capacity must be positive")
    threshold_nodes = normal_capacity * args.utilization_threshold
    raw = []
    for start, end, used, queued, queue_since, last_started in states:
        if queued < args.minimum_waiting_jobs or end <= start:
            continue
        if used <= threshold_nodes:
            raw.append((start, end, "low_allocation_with_backlog", used, queued))
        anchor = queue_since
        if last_started is not None and (anchor is None or last_started > anchor):
            anchor = last_started
        if anchor is not None and end - anchor >= args.minimum_duration:
            raw.append((anchor, end, "no_starts_with_backlog", used, queued))

    periods = merge_periods(raw, args.minimum_duration)
    fieldnames = ["start_time", "end_time", "duration_seconds",
                  "inferred_nodes", "reason", "peak_allocated",
                  "peak_waiting_jobs"]
    with open(args.candidates_csv, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for start, end, reasons, used, queued in periods:
            inferred = min(normal_capacity,
                           int(math.ceil(used * args.capacity_headroom)))
            writer.writerow({
                "start_time": "{:.15g}".format(start),
                "end_time": "{:.15g}".format(end),
                "duration_seconds": "{:.15g}".format(end - start),
                "inferred_nodes": inferred,
                "reason": "+".join(sorted(reasons)),
                "peak_allocated": used,
                "peak_waiting_jobs": queued,
            })

    if args.capacity_schedule:
        changes = {}
        for start, end, reasons, used, queued in periods:
            del reasons, queued
            inferred = min(normal_capacity,
                           int(math.ceil(used * args.capacity_headroom)))
            if inferred >= normal_capacity:
                continue
            changes[start] = min(changes.get(start, normal_capacity), inferred)
            changes[end] = normal_capacity
        with open(args.capacity_schedule, "w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["time", "total_nodes"])
            previous = normal_capacity
            for point in sorted(changes):
                capacity = changes[point]
                if capacity != previous:
                    writer.writerow(["{:.15g}".format(point), capacity])
                    previous = capacity

    print("normal_capacity={}".format(normal_capacity))
    print("candidate_periods={}".format(len(periods)))
    print("warning=capacity is inferred from workload evidence; review before simulation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("error: {}".format(error), file=sys.stderr)
        sys.exit(1)
