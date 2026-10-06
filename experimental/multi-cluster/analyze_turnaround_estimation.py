#!/usr/bin/env python3
"""Evaluate predicted turnaround against realized dispatch-replay timings.

For each prediction-utilization value, this script replays the recorded
``system_id`` assignments without redispatching jobs.  It pairs the resulting
submit-to-completion time with ``predicted_turnaround`` from the dispatch CSV,
then writes per-job MAPE and SMAPE summaries and a comparison plot.

The replay is necessary because dispatch CSVs contain predicted turnaround but
do not contain each job's realized start and completion timestamps.
"""

import argparse
import contextlib
import csv
import io
import math
import os
import pathlib
import re
import sys
import tempfile


DEFAULT_UTILIZATIONS = (
    "0.6",
    "0.7",
    "0.75",
    "0.8",
    "0.85",
    "0.9",
    "0.95",
    "1.0",
)
OVERALL_PATTERN = re.compile(r"average_turnaround_time=([-+0-9.eE]+)")


def use_installed_python_module():
    """Add the DR_EVT Python module below CMAKE_INSTALL_PREFIX to sys.path."""
    prefix_text = os.environ.get("CMAKE_INSTALL_PREFIX")
    if not prefix_text:
        raise ValueError("CMAKE_INSTALL_PREFIX is not set")
    prefix = pathlib.Path(prefix_text)
    configured_libdir = os.environ.get("CMAKE_INSTALL_LIBDIR")
    libdirs = (configured_libdir,) if configured_libdir else ("lib", "lib64")
    candidates = [prefix / name / "python" for name in libdirs]
    matches = [
        directory
        for directory in candidates
        if any(directory.glob("dr_evt*.so"))
    ]
    if len(matches) != 1:
        searched = ", ".join(str(path) for path in candidates)
        raise ValueError(f"cannot uniquely locate installed dr_evt module in {searched}")
    sys.path.insert(0, str(matches[0]))


def load_pairs(dispatch_path, details_path):
    """Return positive predicted/realized turnaround pairs for matching jobs."""
    actual = {}
    with details_path.open(newline="", encoding="utf-8") as stream:
        for row_number, row in enumerate(csv.DictReader(stream), start=2):
            key = (row["system_id"], row["job_id"])
            if key in actual:
                raise ValueError(
                    f"{details_path}:{row_number}: duplicate job {key!r}"
                )
            value = float(row["turnaround_time"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"{details_path}:{row_number}: invalid turnaround {value}"
                )
            actual[key] = value

    pairs = []
    seen = set()
    with dispatch_path.open(newline="", encoding="utf-8") as stream:
        for row_number, row in enumerate(csv.DictReader(stream), start=2):
            key = (row["system_id"], row["job_id"])
            if key in seen:
                raise ValueError(
                    f"{dispatch_path}:{row_number}: duplicate job {key!r}"
                )
            seen.add(key)
            if key not in actual:
                raise ValueError(f"{details_path}: missing job {key!r}")
            predicted = float(row["predicted_turnaround"])
            if not math.isfinite(predicted) or predicted <= 0:
                raise ValueError(
                    f"{dispatch_path}:{row_number}: invalid prediction {predicted}"
                )
            pairs.append((predicted, actual[key]))

    extra = actual.keys() - seen
    if extra:
        raise ValueError(f"{details_path}: contains {len(extra)} unmatched jobs")
    if not pairs:
        raise ValueError(f"{dispatch_path}: no dispatched jobs")
    return pairs


def calculate_errors(pairs):
    """Return mean turnaround, MAPE, and SMAPE for per-job value pairs."""
    count = len(pairs)
    return {
        "jobs": count,
        "mean_predicted_turnaround_seconds": sum(
            predicted for predicted, _ in pairs
        )
        / count,
        "mean_replayed_turnaround_seconds": sum(actual for _, actual in pairs)
        / count,
        "mape_percent": 100.0
        * sum(abs(predicted - actual) / actual for predicted, actual in pairs)
        / count,
        "smape_percent": 100.0
        * sum(
            2.0 * abs(predicted - actual) / (abs(predicted) + abs(actual))
            for predicted, actual in pairs
        )
        / count,
    }


def reported_turnaround(log_path):
    """Read the overall mean turnaround reported by one dispatch run."""
    match = OVERALL_PATTERN.search(log_path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"{log_path}: missing overall turnaround metric")
    return float(match.group(1))


def write_summary(records, path):
    """Write one turnaround-error record per utilization value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)


def plot_summary(records, output):
    """Plot MAPE and SMAPE against prediction utilization."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    utilization = [record["prediction_utilization"] for record in records]
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for axis, field, title in (
        (axes[0], "mape_percent", "MAPE"),
        (axes[1], "smape_percent", "SMAPE"),
    ):
        axis.plot(utilization, [record[field] for record in records], marker="o")
        axis.set_title(title)
        axis.set_xlabel("Prediction utilization")
        axis.set_ylabel("Error (%)")
        axis.grid(alpha=0.3)
    figure.suptitle("Per-job turnaround estimation error")
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    figure.savefig(output.with_suffix(".pdf"))
    plt.close(figure)


def analyze(study_dir, systems, utilizations, output_prefix):
    """Replay all requested runs and produce turnaround-error artifacts."""
    use_installed_python_module()
    import replay_dispatch

    records = []
    with tempfile.TemporaryDirectory(prefix="dr_evt-turnaround-error-") as directory:
        details_dir = pathlib.Path(directory)
        for value in utilizations:
            stem = f"turnaround.actual-duration.ideal.u{value}"
            dispatch_path = study_dir / f"{stem}.dispatch.csv"
            log_path = study_dir / f"{stem}.log"
            complete_path = study_dir / f"{stem}.complete"
            for path in (dispatch_path, log_path, complete_path):
                if not path.is_file():
                    raise ValueError(f"incomplete run: missing {path}")

            details_path = details_dir / f"u{value}.csv"
            replay_output = io.StringIO()
            with contextlib.redirect_stdout(replay_output):
                replay_dispatch.replay(
                    dispatch_path,
                    systems,
                    "actual-duration",
                    43200.0,
                    details_path,
                )
            metrics = calculate_errors(load_pairs(dispatch_path, details_path))
            reported = reported_turnaround(log_path)
            replayed = metrics["mean_replayed_turnaround_seconds"]
            difference = replayed - reported
            if not math.isclose(replayed, reported, rel_tol=1e-6, abs_tol=1e-6):
                print(
                    f"warning: u{value}: per-job replay mean {replayed:.8g} "
                    f"differs from reported mean {reported:.8g} "
                    f"by {difference:.8g} seconds",
                    file=sys.stderr,
                )
            records.append(
                {
                    "prediction_utilization": float(value),
                    **metrics,
                    "reported_mean_turnaround_seconds": reported,
                    "replay_reported_difference_seconds": difference,
                }
            )

    write_summary(records, output_prefix.with_suffix(".csv"))
    plot_summary(records, output_prefix.with_suffix(".png"))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    script_dir = pathlib.Path(__file__).resolve().parent
    parser.add_argument(
        "--study-dir",
        type=pathlib.Path,
        default=script_dir / "prediction-utilization-study",
    )
    parser.add_argument(
        "--systems", type=pathlib.Path, default=script_dir / "machines.csv"
    )
    parser.add_argument(
        "--utilizations", nargs="+", default=DEFAULT_UTILIZATIONS
    )
    parser.add_argument(
        "--output-prefix",
        type=pathlib.Path,
        help="output path without extension (default: STUDY-DIR/turnaround-estimation-error)",
    )
    args = parser.parse_args()
    output_prefix = args.output_prefix or args.study_dir / "turnaround-estimation-error"
    analyze(args.study_dir, args.systems, args.utilizations, output_prefix)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}")
