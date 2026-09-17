#!/usr/bin/env python3
"""Plot a capacity trace as wrapped, uniformly spaced rectangular segments."""

import argparse
import csv
import math
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import StrMethodFormatter

from discover_maintenance import TimestampFormatter


COLORS = {
    "full": "#b9d4e8",
    "reduced": "#d17a22",
    "paused": "#7a0177",
}


def load_cases(path, timezone):
    with open(path, newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("{} is empty".format(path))
    simulator_format = "time" in rows[0] and "total_nodes" in rows[0]
    if simulator_format:
        full_capacity = max(int(row["total_nodes"]) for row in rows)
        values = [int(row["total_nodes"]) for row in rows]
        time_field = "time"
    else:
        full_capacity = max(int(row["structural_capacity_nodes"]) for row in rows)
        values = [int(row["normal_queue_capacity_nodes"]) for row in rows]
        time_field = "start"
    changes = [0]
    for index in range(1, len(rows)):
        if values[index] != values[index - 1]:
            changes.append(index)
    cases = []
    for case_index, row_index in enumerate(changes):
        end_index = changes[case_index + 1] if case_index + 1 < len(changes) else len(rows)
        capacity = values[row_index]
        if capacity == 0:
            kind = "paused"
        elif capacity < full_capacity:
            kind = "reduced"
        else:
            kind = "full"
        cases.append(
            {
                "date": datetime.fromtimestamp(
                    int(rows[row_index][time_field]), timezone
                ),
                "capacity": capacity,
                "kind": kind,
                "duration_hours": end_index - row_index,
            }
        )
    return cases, full_capacity


def plot_cases(cases, full_capacity, output, title, timezone_name, cases_per_row, dpi):
    row_count = int(math.ceil(len(cases) / float(cases_per_row)))
    figure, axes = plt.subplots(
        row_count, 1, figsize=(18, max(3.2 * row_count, 5)), squeeze=False
    )
    axes = [row[0] for row in axes]
    for row_index, axis in enumerate(axes):
        first = row_index * cases_per_row
        last = min(len(cases), first + cases_per_row)
        block = cases[first:last]
        positions = list(range(len(block)))
        values = [case["capacity"] for case in block]
        colors = [COLORS[case["kind"]] for case in block]

        # Width 1 and align=edge make adjacent cases a continuous bar silhouette.
        axis.bar(
            positions, values, width=1.0, align="edge", color=colors,
            edgecolor="#333333", linewidth=0.45,
        )
        for position, case in zip(positions, block):
            if case["kind"] == "paused":
                # A zero-height capacity bar is otherwise invisible. Keep the
                # value at zero and emphasize only its baseline.
                axis.hlines(
                    0, position, position + 1, colors=COLORS["paused"],
                    linewidth=6.0, zorder=5,
                )
        outline_x = list(range(len(block) + 1))
        outline_y = values + [values[-1]]
        axis.step(outline_x, outline_y, where="post", color="#222222", linewidth=0.75)
        axis.axhline(
            full_capacity, color="#555555", linestyle="--", linewidth=1.0
        )
        axis.set_xlim(0, len(block))
        axis.set_ylim(-0.02 * full_capacity, 1.06 * full_capacity)
        axis.set_xticks([value + 0.5 for value in positions])
        axis.set_xticklabels(
            [case["date"].strftime("%Y-%m-%d") for case in block],
            rotation=60, ha="right", fontsize=8,
        )
        axis.set_ylabel("Capacity\n(nodes)")
        axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        axis.grid(axis="y", alpha=0.20)
        axis.set_title(
            "Change cases {:d}–{:d}".format(first + 1, last),
            loc="left", fontsize=10,
        )
    axes[0].legend(
        handles=[
            Patch(facecolor=COLORS["full"], edgecolor="#333333", label="Full capacity"),
            Patch(facecolor=COLORS["reduced"], edgecolor="#333333", label="Inferred reduction"),
            Line2D(
                [0], [0], color=COLORS["paused"], linewidth=6.0,
                label="Queue paused (capacity 0 line)",
            ),
        ],
        loc="lower right", fontsize=8, ncol=3,
    )
    axes[-1].set_xlabel(
        "Capacity-change date ({}, uniform spacing; duration not to scale)".format(
            timezone_name
        )
    )
    figure.suptitle(title, fontsize=15)
    figure.subplots_adjust(
        top=0.96, bottom=0.08, left=0.08, right=0.99, hspace=0.80
    )
    figure.savefig(output, dpi=dpi)
    plt.close(figure)


def parse_args():
    parser = argparse.ArgumentParser(description="Plot a capacity silhouette")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--cases-per-row", type=int, default=21)
    parser.add_argument("--dpi", type=int, default=180)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.cases_per_row <= 0:
        raise ValueError("--cases-per-row must be positive")
    timezone = TimestampFormatter("epoch", args.timezone).timezone
    cases, full_capacity = load_cases(args.input, timezone)
    plot_cases(
        cases, full_capacity, args.output, args.title, args.timezone,
        args.cases_per_row, args.dpi,
    )
    print("{} ({} change cases)".format(args.output, len(cases)))


if __name__ == "__main__":
    main()
