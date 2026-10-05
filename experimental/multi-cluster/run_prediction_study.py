#!/usr/bin/env python3
"""Run and summarize the multi-cluster prediction-baseline study.

The study uses ten Lassen synthetic job streams and compares ideal,
application-average, and RAJAPerf predictions.  A model-based case can be
added later by passing ``--model-prediction``.
"""

import argparse
import csv
import math
import re
import statistics
import subprocess
import sys
from pathlib import Path


METRICS = (
    "average_turnaround_time",
    "average_bounded_slowdown",
    "average_run_time",
    "average_speedup",
)
OVERALL_RE = re.compile(
    r"^overall: jobs=(?P<jobs>\d+) dropped_jobs=(?P<dropped_jobs>\d+) "
    + r" ".join(rf"{name}=(?P<{name}>[-+0-9.eE]+)" for name in METRICS)
    + r"$",
    re.MULTILINE,
)


def parse_overall(text):
    """Extract the overall metric record from one dispatcher log."""
    matches = list(OVERALL_RE.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"expected one overall line, found {len(matches)}")
    match = matches[0]
    return {
        "jobs": int(match.group("jobs")),
        "dropped_jobs": int(match.group("dropped_jobs")),
        **{name: float(match.group(name)) for name in METRICS},
    }


def prediction_cases(root, model_prediction):
    """Return ordered (case, prediction table) pairs for this study."""
    experiment = root / "experimental/multi-cluster"
    cases = [
        ("ideal", experiment / "ground_truth.csv"),
        ("app_avg", experiment / "prediction.app_avg.csv"),
        ("rajaperf", experiment / "prediction.rajaperf.csv"),
    ]
    if model_prediction is not None:
        cases.insert(1, ("model", model_prediction.resolve()))
    return cases


def validate_inputs(root, executable, cases):
    """Validate all static inputs before consuming an allocation."""
    required = [
        executable,
        root / "experimental/multi-cluster/apps.csv",
        root / "experimental/multi-cluster/machines.csv",
        *(prediction for _, prediction in cases),
    ]
    traces = sorted(
        (root / "experimental/synthesize/lassen/synthetic_traces").glob(
            "synthetic_jobs_100000_min60s_successful_*.csv"
        )
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(missing))
    if len(traces) != 10:
        raise ValueError(f"expected exactly 10 job traces, found {len(traces)}")
    for trace in traces:
        with trace.open(encoding="utf-8") as stream:
            rows = sum(1 for _ in stream) - 1
        if rows != 100_000:
            raise ValueError(f"{trace} contains {rows} jobs, expected 100000")
    return traces


def run_case(args, root, case, prediction, trace, run_number):
    """Run one case unless its validated completion marker already exists."""
    stem = f"{case}.run_{run_number:02d}"
    dispatch = args.output_dir / f"{stem}.dispatch.csv"
    log = args.output_dir / f"{stem}.log"
    marker = args.output_dir / f"{stem}.complete"

    if marker.is_file() and dispatch.is_file() and log.is_file():
        try:
            record = parse_overall(log.read_text(encoding="utf-8"))
            with dispatch.open(encoding="utf-8") as stream:
                rows = sum(1 for _ in stream) - 1
            if (
                rows == record["jobs"]
                and rows + record["dropped_jobs"] == 100_000
            ):
                print(f"skip validated {stem}", flush=True)
                return record
        except (OSError, ValueError):
            pass

    marker.unlink(missing_ok=True)
    command = [
        *args.launcher,
        "-n",
        str(args.ranks),
        str(args.executable),
        "--jobs",
        str(trace),
        "--ground-truth",
        str(root / "experimental/multi-cluster/ground_truth.csv"),
        "--prediction",
        str(prediction),
        "--applications",
        str(root / "experimental/multi-cluster/apps.csv"),
        "--systems",
        str(root / "experimental/multi-cluster/machines.csv"),
        "--seed",
        str(args.seed),
        "--max-time-limit",
        str(args.max_time_limit),
        "--output",
        str(dispatch),
    ]
    print(f"run {stem}: {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=root, text=True, capture_output=True)
    combined = result.stdout + result.stderr
    log.write_text(combined, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{stem} failed with status {result.returncode}; see {log}")

    record = parse_overall(combined)
    with dispatch.open(encoding="utf-8") as stream:
        rows = sum(1 for _ in stream) - 1
    if rows != record["jobs"] or rows + record["dropped_jobs"] != 100_000:
        raise RuntimeError(
            f"{stem} is incomplete: dispatch rows={rows}, "
            f"reported jobs={record['jobs']}, dropped={record['dropped_jobs']}"
        )
    marker.write_text("complete\n", encoding="utf-8")
    return record


def write_results(output_dir, records):
    """Write per-run data and ten-run aggregate tables."""
    per_run = output_dir / "metrics_per_run.csv"
    with per_run.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("case", "run", "jobs", "dropped_jobs", *METRICS),
        )
        writer.writeheader()
        writer.writerows(records)

    grouped = {}
    for record in records:
        grouped.setdefault(record["case"], []).append(record)

    summary_rows = []
    for case, case_records in grouped.items():
        if len(case_records) != 10:
            raise ValueError(f"{case} has {len(case_records)} complete runs, expected 10")
        row = {"case": case, "runs": len(case_records)}
        dropped = [record["dropped_jobs"] for record in case_records]
        row["dropped_jobs_mean"] = statistics.fmean(dropped)
        row["dropped_jobs_stddev"] = statistics.pstdev(dropped)
        for metric in METRICS:
            values = [record[metric] for record in case_records]
            row[f"{metric}_mean"] = statistics.fmean(values)
            row[f"{metric}_stddev"] = statistics.pstdev(values)
        summary_rows.append(row)

    summary_csv = output_dir / "summary.csv"
    fields = ["case", "runs", "dropped_jobs_mean", "dropped_jobs_stddev"] + [
        column for metric in METRICS for column in (f"{metric}_mean", f"{metric}_stddev")
    ]
    with summary_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_md = output_dir / "summary.md"
    labels = {
        "ideal": "Ideal (100% accurate)",
        "model": "Model-based",
        "app_avg": "Application average",
        "rajaperf": "RAJAPerf",
    }
    lines = [
        "# Multi-cluster prediction study",
        "",
        "Values are the mean ± population standard deviation over 10 job traces.",
        "",
        "| Prediction | Dropped jobs | Turnaround time | Bounded slowdown | Run time | Speedup |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        cells = []
        for metric in METRICS:
            cells.append(f"{row[f'{metric}_mean']:.6g} ± {row[f'{metric}_stddev']:.6g}")
        dropped = (
            f"{row['dropped_jobs_mean']:.6g} ± "
            f"{row['dropped_jobs_stddev']:.6g}"
        )
        lines.append(
            f"| {labels[row['case']]} | {dropped} | "
            + " | ".join(cells)
            + " |"
        )
    if "model" not in grouped:
        lines.extend(
            ["", "Model-based prediction was not run because no model prediction table was supplied."]
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_rows


def plot_results(output_dir, summary_rows):
    """Create a four-panel mean-metric plot with run-to-run error bars."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError(
            "matplotlib is required for the plot; tables were still generated"
        ) from error

    labels = [row["case"].replace("_", " ") for row in summary_rows]
    titles = {
        "average_turnaround_time": "Average turnaround time",
        "average_bounded_slowdown": "Average bounded slowdown",
        "average_run_time": "Average run time",
        "average_speedup": "Average selected speedup",
    }
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756"]
    for axis, metric in zip(axes.flat, METRICS):
        means = [row[f"{metric}_mean"] for row in summary_rows]
        errors = [row[f"{metric}_stddev"] for row in summary_rows]
        axis.bar(labels, means, yerr=errors, capsize=4, color=colors[: len(labels)])
        axis.set_title(titles[metric])
        axis.grid(axis="y", alpha=0.25)
        axis.tick_params(axis="x", rotation=15)
    fig.suptitle("Multi-cluster prediction policies (mean ± SD, 10 traces)")
    fig.tight_layout()
    fig.savefig(output_dir / "summary.png", dpi=180)
    plt.close(fig)


def load_completed(output_dir, cases):
    """Load metrics for aggregate-only mode from validated run artifacts."""
    records = []
    for case, _ in cases:
        for run_number in range(1, 11):
            stem = f"{case}.run_{run_number:02d}"
            marker = output_dir / f"{stem}.complete"
            log = output_dir / f"{stem}.log"
            if not marker.is_file() or not log.is_file():
                raise FileNotFoundError(f"missing completed run: {stem}")
            records.append({"case": case, "run": run_number, **parse_overall(log.read_text())})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="installed mpi_performance_dispatch")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experimental/multi-cluster/prediction-study-results"),
    )
    parser.add_argument(
        "--launcher", nargs="+", default=["srun"], help="MPI launcher prefix (default: srun)"
    )
    parser.add_argument("--ranks", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-time-limit", type=float, default=43200.0)
    parser.add_argument("--model-prediction", type=Path)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.max_time_limit) or args.max_time_limit <= 0:
        parser.error("--max-time-limit must be finite and positive")

    root = Path(__file__).resolve().parents[2]
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = prediction_cases(root, args.model_prediction)

    if args.aggregate_only:
        records = load_completed(args.output_dir, cases)
    else:
        if args.executable is None:
            parser.error("--executable is required unless --aggregate-only is used")
        args.executable = args.executable.resolve()
        traces = validate_inputs(root, args.executable, cases)
        records = []
        for case, prediction in cases:
            for run_number, trace in enumerate(traces, start=1):
                record = run_case(args, root, case, prediction, trace, run_number)
                records.append({"case": case, "run": run_number, **record})

    summary_rows = write_results(args.output_dir, records)
    plot_results(args.output_dir, summary_rows)
    print(f"wrote results to {args.output_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
