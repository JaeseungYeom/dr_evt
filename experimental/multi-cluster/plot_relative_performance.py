#!/usr/bin/env python3
"""Plot measured versus predicted relative performance by execution mode."""

import argparse
import csv
import math
import pathlib
import sys


IDENTITY_COLUMNS = {"App", "Args", "Ranks", "app", "args", "ranks"}


def available_systems(fieldnames):
    """Return performance columns in table order."""
    return [field for field in fieldnames or () if field not in IDENTITY_COLUMNS]


def resolve_system(name, available):
    """Resolve an exact mode or an unsuffixed GPU-machine alias."""
    if name in available:
        return name
    gpu_name = f"{name}-gpu"
    if gpu_name in available:
        return gpu_name
    raise ValueError(f"unknown system {name!r}; choose from: {', '.join(available)}")


def load_points(ground_truth_path, prediction_path, requested_systems=None):
    """Load positive actual/predicted pairs grouped by execution mode."""
    with ground_truth_path.open(newline="") as actual_stream, prediction_path.open(
        newline=""
    ) as predicted_stream:
        actual_reader = csv.DictReader(actual_stream)
        predicted_reader = csv.DictReader(predicted_stream)
        available = available_systems(actual_reader.fieldnames)
        if not available:
            raise ValueError(f"{ground_truth_path} has no performance columns")
        if available_systems(predicted_reader.fieldnames) != available:
            raise ValueError("ground-truth and prediction columns differ")
        systems = (
            [resolve_system(name, available) for name in requested_systems]
            if requested_systems
            else available
        )
        if len(set(systems)) != len(systems):
            raise ValueError("the requested systems resolve to duplicates")
        points = {system: [] for system in systems}
        skipped = {system: 0 for system in systems}
        for row_number, (row, predicted_row) in enumerate(
            zip(actual_reader, predicted_reader, strict=True), start=2
        ):
            app_field = "App" if "App" in row else "app"
            identity = tuple(row.get(name, row.get(name.lower())) for name in ("App", "Args", "Ranks"))
            predicted_identity = tuple(predicted_row.get(name, predicted_row.get(name.lower())) for name in ("App", "Args", "Ranks"))
            if identity != predicted_identity:
                raise ValueError(f"workload identity mismatch at row {row_number}")
            app = row[app_field].strip()
            for system in systems:
                actual_text = row[system].strip()
                predicted_text = predicted_row[system].strip()
                if not actual_text and not predicted_text:
                    continue
                if not actual_text or not predicted_text:
                    skipped[system] += 1
                    continue
                actual = float(actual_text)
                predicted = float(predicted_text)
                if (
                    not math.isfinite(actual)
                    or not math.isfinite(predicted)
                    or actual <= 0
                    or predicted <= 0
                ):
                    raise ValueError(f"row {row_number}: invalid {system} pair")
                points[system].append((actual, predicted, app))
        skipped = {system: count for system, count in skipped.items() if count}
        if skipped:
            detail = ", ".join(
                f"{system}={count}" for system, count in skipped.items()
            )
            print(
                f"warning: skipped unpaired actual/predicted cells: {detail}",
                file=sys.stderr,
            )
    return points


def display_name(system):
    """Return a concise plot title for one execution mode."""
    if system.endswith("-gpu"):
        return f"{system[:-4]} (GPU)"
    if system.endswith("-cpu"):
        return f"{system[:-4]} (CPU)"
    return system


def plot_points(points, output, logarithmic=True):
    """Create a multi-panel actual-versus-predicted scatter plot."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    systems = list(points)
    if not systems:
        raise ValueError("no systems to plot")
    applications = sorted(
        {app for system_points in points.values() for _, _, app in system_points}
    )
    palette = plt.get_cmap("tab10")
    colors = {app: palette(index % 10) for index, app in enumerate(applications)}
    columns = min(4, len(systems))
    rows = math.ceil(len(systems) / columns)
    figure, axes = plt.subplots(
        rows, columns, figsize=(4.2 * columns, 4.0 * rows), squeeze=False
    )

    for axis, system in zip(axes.flat, systems):
        system_points = points[system]
        for app in applications:
            app_points = [(x, y) for x, y, label in system_points if label == app]
            if app_points:
                axis.scatter(
                    [point[0] for point in app_points],
                    [point[1] for point in app_points],
                    s=18,
                    alpha=0.7,
                    color=colors[app],
                    edgecolors="none",
                    label=app,
                )
        if system_points:
            values = [value for x, y, _ in system_points for value in (x, y)]
            low, high = min(values), max(values)
            if logarithmic:
                low /= 1.15
                high *= 1.15
                axis.set_xscale("log")
                axis.set_yscale("log")
            else:
                padding = max((high - low) * 0.08, high * 0.01)
                low = max(0.0, low - padding)
                high += padding
            axis.plot([low, high], [low, high], "--", color="0.35", linewidth=1)
            axis.set_xlim(low, high)
            axis.set_ylim(low, high)
            mape = (
                100
                * sum(
                    abs(predicted - actual) / actual
                    for actual, predicted, _ in system_points
                )
                / len(system_points)
            )
            axis.set_title(
                f"{display_name(system)}\nN={len(system_points)}, MAPE={mape:.2f}%"
            )
        else:
            axis.set_title(f"{display_name(system)}\nNo paired samples")
        axis.set_xlabel("Actual relative performance")
        axis.set_ylabel("Predicted relative performance")
        axis.grid(True, which="both", alpha=0.25)

    for axis in axes.flat[len(systems) :]:
        axis.set_visible(False)
    handles = [
        plt.Line2D(
            [],
            [],
            linestyle="none",
            marker="o",
            markersize=6,
            color=colors[app],
            label=app,
        )
        for app in applications
    ]
    figure.legend(
        handles=handles,
        loc="outside lower center",
        ncol=min(4, len(applications)),
        frameon=False,
    )
    figure.suptitle("Actual vs. predicted relative performance")
    figure.tight_layout(rect=(0, 0.08, 1, 0.96))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180, bbox_inches="tight")
    if output.suffix.lower() != ".pdf":
        figure.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=pathlib.Path)
    parser.add_argument("--prediction", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument(
        "--systems",
        nargs="+",
        help="systems/modes to plot (default: all; e.g. tuolumne tuolumne-cpu)",
    )
    parser.add_argument(
        "--linear", action="store_true", help="use linear instead of log axes"
    )
    args = parser.parse_args()
    points = load_points(args.ground_truth, args.prediction, args.systems)
    plot_points(points, args.output, logarithmic=not args.linear)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}")
