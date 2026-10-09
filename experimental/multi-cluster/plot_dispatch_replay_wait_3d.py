#!/usr/bin/env python3
"""Plot wait times produced by replaying recorded dispatch assignments.

The input CSVs are per-job outputs from ``replay_dispatch.py``. The script
compares two replays of the same recorded system assignments, normally one
using adapted wall-time limits and one using exact limits. It does not perform
dispatching or choose a system for any job.
"""

import argparse
import csv
from pathlib import Path

import matplotlib.colors as colors
import matplotlib.pyplot as plt
import numpy as np


NODE_EDGES = np.array([1, 2, 4, 8, 16, 32, 64, 128, 257, 515], dtype=float)
LIMIT_EDGES = np.array([2**power for power in range(0, 17)], dtype=float)


def load_bins(path):
    """Return counts and wait statistics over node/limit bins."""
    counts = np.zeros((len(NODE_EDGES) - 1, len(LIMIT_EDGES) - 1), dtype=int)
    wait_sums = np.zeros_like(counts, dtype=float)
    minimums = np.full_like(wait_sums, np.inf)
    maximums = np.full_like(wait_sums, -np.inf)
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            nodes = float(row["num_nodes"])
            limit = float(row["submitted_time_limit"])
            wait = float(row["wait_time"])
            node_bin = np.searchsorted(NODE_EDGES, nodes, side="right") - 1
            limit_bin = np.searchsorted(LIMIT_EDGES, limit, side="right") - 1
            node_bin = min(node_bin, counts.shape[0] - 1)
            limit_bin = min(limit_bin, counts.shape[1] - 1)
            if node_bin < 0 or limit_bin < 0:
                raise ValueError(f"invalid row in {path}: {row}")
            counts[node_bin, limit_bin] += 1
            wait_sums[node_bin, limit_bin] += wait
            minimums[node_bin, limit_bin] = min(
                minimums[node_bin, limit_bin], wait
            )
            maximums[node_bin, limit_bin] = max(
                maximums[node_bin, limit_bin], wait
            )
    means = np.divide(
        wait_sums,
        counts,
        out=np.zeros_like(wait_sums),
        where=counts != 0,
    )
    minimums[counts == 0] = 0.0
    maximums[counts == 0] = 0.0
    return counts, {"minimum": minimums, "maximum": maximums, "mean": means}


def load_waits(path):
    """Return per-job realized waits in days."""
    with path.open(newline="", encoding="utf-8") as stream:
        return np.array(
            [float(row["wait_time"]) / 86400.0 for row in csv.DictReader(stream)]
        )


def bin_label(lower, upper):
    """Format a half-open integral bin."""
    if upper == lower + 1:
        return str(int(lower))
    return f"{int(lower)}–{int(upper - 1)}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapted", required=True, type=Path)
    parser.add_argument("--exact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--histogram-output", type=Path)
    parser.add_argument("--cdf-output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--system-label",
        help="optional machine name included in plot titles",
    )
    args = parser.parse_args()
    context = f" ({args.system_label})" if args.system_label else ""

    datasets = [
        ("Adapted limit", *load_bins(args.adapted)),
        ("Exact limit", *load_bins(args.exact)),
    ]
    maximum_count = max(int(counts.max()) for _, counts, _ in datasets)
    normalization = colors.LogNorm(vmin=1, vmax=max(maximum_count, 1))
    color_map = plt.colormaps["viridis"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for statistic in ("minimum", "maximum", "mean"):
        maximum_wait = max(
            float(values[statistic].max()) for _, _, values in datasets
        ) / 86400.0
        figure = plt.figure(figsize=(20, 9))
        for panel, (title, counts, values) in enumerate(datasets, start=1):
            axis = figure.add_subplot(1, 2, panel, projection="3d")
            occupied = np.nonzero(counts)
            x = occupied[0].astype(float)
            y = occupied[1].astype(float)
            heights = values[statistic][occupied] / 86400.0
            bar_colors = color_map(normalization(counts[occupied]))
            axis.bar3d(
                x,
                y,
                np.zeros_like(heights),
                0.82,
                0.82,
                heights,
                color=bar_colors,
                shade=True,
            )
            axis.set_title(title)
            axis.set_xlabel("Requested nodes", labelpad=14)
            axis.set_ylabel("Time limit (seconds)", labelpad=18)
            axis.set_zlabel(
                f"{statistic.title()} realized wait (days)", labelpad=12
            )
            axis.set_zlim(0, maximum_wait * 1.03)
            axis.set_xticks(np.arange(len(NODE_EDGES) - 1) + 0.41)
            axis.set_xticklabels(
                [
                    bin_label(a, b)
                    for a, b in zip(NODE_EDGES[:-1], NODE_EDGES[1:])
                ],
                rotation=18,
                ha="right",
            )
            shown_y = np.arange(0, len(LIMIT_EDGES) - 1, 3)
            axis.set_yticks(shown_y + 0.41)
            axis.set_yticklabels(
                [
                    bin_label(LIMIT_EDGES[index], LIMIT_EDGES[index + 1])
                    for index in shown_y
                ],
                rotation=-12,
                ha="left",
            )
            axis.tick_params(axis="x", labelsize=8, pad=1)
            axis.tick_params(axis="y", labelsize=8, pad=1)
            axis.tick_params(axis="z", labelsize=9, pad=3)
            axis.view_init(elev=27, azim=-58)

        figure.subplots_adjust(
            left=0.02, right=0.89, bottom=0.08, top=0.9, wspace=0.08
        )
        scalar = plt.cm.ScalarMappable(norm=normalization, cmap=color_map)
        scalar.set_array([])
        figure.colorbar(
            scalar,
            ax=figure.axes,
            shrink=0.68,
            pad=0.08,
            label="Jobs in bin (log scale)",
        )
        figure.suptitle(
            f"Dispatch replay{context}: {statistic} waiting time by job size and limit"
        )
        plot_path = args.output.with_name(
            f"{args.output.stem}.{statistic}{args.output.suffix}"
        )
        figure.savefig(plot_path, dpi=180, bbox_inches="tight")
        figure.savefig(plot_path.with_suffix(".pdf"), bbox_inches="tight")
        plt.close(figure)

    if args.histogram_output:
        waits = [
            ("Adapted limit", load_waits(args.adapted)),
            ("Exact limit", load_waits(args.exact)),
        ]
        maximum_wait = max(float(values.max()) for _, values in waits)
        bins = np.linspace(0.0, maximum_wait, 81)
        figure, axis = plt.subplots(figsize=(10, 6))
        for label, values in waits:
            axis.hist(
                values,
                bins=bins,
                histtype="step",
                linewidth=2,
                label=label,
            )
        axis.set_xlabel("Waiting time (days)")
        axis.set_ylabel("Jobs (log scale)")
        axis.set_yscale("log")
        axis.set_title(
            f"Dispatch replay{context}: distribution of job waiting times"
        )
        axis.grid(axis="y", alpha=0.25)
        axis.legend()
        figure.tight_layout()
        args.histogram_output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.histogram_output, dpi=180)
        figure.savefig(args.histogram_output.with_suffix(".pdf"))
        plt.close(figure)

    if args.cdf_output:
        waits = [
            ("Adapted limit", load_waits(args.adapted)),
            ("Exact limit", load_waits(args.exact)),
        ]
        figure, axis = plt.subplots(figsize=(10, 6))
        for label, values in waits:
            sorted_values = np.sort(values)
            cumulative = np.arange(1, len(sorted_values) + 1) / len(
                sorted_values
            )
            axis.step(sorted_values, cumulative, where="post", label=label)
        axis.set_xlabel("Waiting time (days)")
        axis.set_ylabel("Cumulative fraction of jobs")
        axis.set_ylim(0.0, 1.0)
        axis.set_title(f"Dispatch replay{context}: CDF of job waiting times")
        axis.grid(alpha=0.25)
        axis.legend()
        figure.tight_layout()
        args.cdf_output.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.cdf_output, dpi=180)
        figure.savefig(args.cdf_output.with_suffix(".pdf"))
        plt.close(figure)

    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        with args.summary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(
                (
                    "policy",
                    "nodes_min",
                    "nodes_max",
                    "limit_min",
                    "limit_max",
                    "jobs",
                    "minimum_wait_seconds",
                    "maximum_wait_seconds",
                    "mean_wait_seconds",
                )
            )
            for title, counts, values in datasets:
                for node_bin, limit_bin in zip(*np.nonzero(counts)):
                    writer.writerow(
                        (
                            title,
                            int(NODE_EDGES[node_bin]),
                            int(NODE_EDGES[node_bin + 1] - 1),
                            int(LIMIT_EDGES[limit_bin]),
                            int(LIMIT_EDGES[limit_bin + 1] - 1),
                            int(counts[node_bin, limit_bin]),
                            values["minimum"][node_bin, limit_bin],
                            values["maximum"][node_bin, limit_bin],
                            values["mean"][node_bin, limit_bin],
                        )
                    )


if __name__ == "__main__":
    main()
