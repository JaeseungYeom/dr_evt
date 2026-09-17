#!/usr/bin/env python3
"""Build hourly normal-queue capacity scenarios from maintenance evidence.

Structural capacity is held constant. Queue-pause/shutdown periods expose zero
capacity to the normal queue. The optional sensitivity scenario also applies
the report's evidence-derived value during reduced-capacity periods.
"""

import argparse
import csv
import json
import math

from discover_maintenance import TimestampFormatter


def build_rows(timeline, periods, capacity, include_reduced, timezone_name):
    timestamps = TimestampFormatter("iso", timezone_name)
    by_hour = {}
    for period in periods:
        start = int(period["start"])
        end = int(period["end"])
        for hour in range(start, end, 3600):
            by_hour[hour] = period

    rows = []
    for item in timeline:
        start = int(item["start"])
        end = int(item["end"])
        period = by_hour.get(start)
        state = "full_capacity" if period is None else period["state"]
        available = capacity
        assumption = "full_structural_capacity"
        if state in ("queue_pause_or_maintenance", "full_shutdown"):
            available = 0
            assumption = "normal_queue_paused"
        elif state == "reduced_capacity" and include_reduced:
            available = min(
                capacity,
                max(0, int(math.ceil(
                    float(period["potential_effective_capacity_lower_nodes"])
                ))),
            )
            assumption = "inferred_reduced_capacity_sensitivity"
        elif state == "reduced_capacity":
            assumption = "reduced_capacity_ignored_full_assumed"
        elif state == "backfill_suppression":
            assumption = "no_capacity_reduction_full_assumed"

        rows.append(
            {
                "start": start,
                "end": end,
                "start_local": timestamps.value(start),
                "end_local": timestamps.value(end),
                "timezone": timezone_name,
                "structural_capacity_nodes": capacity,
                "normal_queue_capacity_nodes": available,
                "state": state,
                "assumption": assumption,
            }
        )
    return rows


def simulator_rows(rows, capacity):
    """Convert hourly analysis rows to DR_EVT capacity change points."""
    if not rows:
        raise ValueError("timeline contains no capacity rows")

    changes = []
    previous = None
    for row in rows:
        available = int(row["normal_queue_capacity_nodes"])
        if available != previous:
            changes.append({"time": int(row["start"]), "total_nodes": available})
            previous = available

    # A simulator change point remains effective indefinitely. Restore normal
    # capacity after a reduced final interval instead of accidentally extending
    # that inferred state beyond the analyzed timeline.
    final_end = int(rows[-1]["end"])
    if previous != capacity:
        changes.append({"time": final_end, "total_nodes": capacity})
    return changes


def write_rows(path, rows, simulator_format=False, capacity=None):
    if simulator_format:
        fields = ["time", "total_nodes"]
        rows = simulator_rows(rows, capacity)
    else:
        fields = [
            "start", "end", "start_local", "end_local", "timezone",
            "structural_capacity_nodes", "normal_queue_capacity_nodes",
            "state", "assumption",
        ]
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Build normal-queue capacity traces from a detector report"
    )
    parser.add_argument("--report", default="maintenance_report.json")
    parser.add_argument("--timeline", default="capacity_timeline.csv")
    parser.add_argument(
        "--capacity", type=int,
        help="structural node capacity (default: report normal capacity)",
    )
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument(
        "--with-reduced-output",
        default="resource_capacity_with_reduced_capacity.csv",
    )
    parser.add_argument(
        "--without-reduced-output",
        default="resource_capacity_without_reduced_capacity.csv",
    )
    parser.add_argument(
        "--simulator-format", action="store_true",
        help=(
            "write compact DR_EVT --capacity_schedule files with columns "
            "time,total_nodes instead of the detailed hourly analysis format"
        ),
    )
    args = parser.parse_args()
    if args.capacity is not None and args.capacity <= 0:
        parser.error("--capacity must be positive")

    with open(args.report, encoding="utf-8") as stream:
        report = json.load(stream)
    with open(args.timeline, newline="", encoding="utf-8") as stream:
        timeline = list(csv.DictReader(stream))

    capacity = args.capacity
    if capacity is None:
        capacity = int(round(float(report["normal_capacity"]["inferred_nodes"])))
    if capacity <= 0:
        raise ValueError("report normal capacity must be positive")

    with_reduced = build_rows(
        timeline, report["periods"], capacity, True, args.timezone
    )
    without_reduced = build_rows(
        timeline, report["periods"], capacity, False, args.timezone
    )
    write_rows(
        args.with_reduced_output, with_reduced, args.simulator_format, capacity
    )
    write_rows(
        args.without_reduced_output,
        without_reduced,
        args.simulator_format,
        capacity,
    )
    print(args.with_reduced_output)
    print(args.without_reduced_output)


if __name__ == "__main__":
    main()
