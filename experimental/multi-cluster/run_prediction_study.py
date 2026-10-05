#!/usr/bin/env python3
"""Run and summarize the multi-cluster prediction-policy study.

The study uses ten Lassen synthetic job streams and compares ideal,
application-average, and RAJAPerf predictions under the turnaround-aware and
IPDPS24 dispatch policies.  Each combination is run with adapted predicted
wall times and with wall time set to actual duration.  A model-based case can
be added by passing ``--model-prediction``.
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
DISPATCH_POLICIES = ("turnaround", "IPDPS24")
WALL_TIME_POLICIES = ("adapted-limit", "actual-duration")
JOBS_PER_TRACE = 100_000
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
        if rows != JOBS_PER_TRACE:
            raise ValueError(
                f"{trace} contains {rows} jobs, expected {JOBS_PER_TRACE}"
            )
    return traces


def run_case(
    args,
    root,
    dispatch_policy,
    wall_time_policy,
    case,
    prediction,
    trace,
    run_number,
):
    """Run one case unless its validated completion marker already exists."""
    stem = f"{dispatch_policy}.{wall_time_policy}.{case}.run_{run_number:02d}"
    dispatch = args.output_dir / f"{stem}.dispatch.csv"
    log = args.output_dir / f"{stem}.log"
    marker = args.output_dir / f"{stem}.complete"
    marker_text = (
        f"dispatch_policy={dispatch_policy}\n"
        f"wall_time_policy={wall_time_policy}\n"
    )

    if (
        marker.is_file()
        and marker.read_text(encoding="utf-8") == marker_text
        and dispatch.is_file()
        and log.is_file()
    ):
        try:
            record = parse_overall(log.read_text(encoding="utf-8"))
            with dispatch.open(encoding="utf-8") as stream:
                rows = sum(1 for _ in stream) - 1
            if (
                rows == record["jobs"]
                and rows + record["dropped_jobs"] == JOBS_PER_TRACE
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
        "--dispatch-policy",
        dispatch_policy,
        "--wall-time-policy",
        wall_time_policy,
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
    if (
        rows != record["jobs"]
        or rows + record["dropped_jobs"] != JOBS_PER_TRACE
    ):
        raise RuntimeError(
            f"{stem} is incomplete: dispatch rows={rows}, "
            f"reported jobs={record['jobs']}, dropped={record['dropped_jobs']}"
        )
    marker.write_text(marker_text, encoding="utf-8")
    return record


def write_results(output_dir, records):
    """Write per-run data and ten-run aggregate tables."""
    per_run_fields = (
        "dispatch_policy",
        "wall_time_policy",
        "case",
        "run",
        "jobs",
        "dropped_jobs",
        *METRICS,
    )
    for filename in ("summary.csv", "metrics_per_run.csv"):
        with (output_dir / filename).open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(
                stream, fieldnames=per_run_fields, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(records)

    grouped = {}
    for record in records:
        key = (
            record["dispatch_policy"],
            record["wall_time_policy"],
            record["case"],
        )
        grouped.setdefault(key, []).append(record)

    summary_rows = []
    for (dispatch_policy, wall_time_policy, case), case_records in grouped.items():
        if len(case_records) != 10:
            raise ValueError(
                f"{dispatch_policy}/{wall_time_policy}/{case} has "
                f"{len(case_records)} complete runs, expected 10"
            )
        row = {
            "dispatch_policy": dispatch_policy,
            "wall_time_policy": wall_time_policy,
            "case": case,
            "runs": len(case_records),
        }
        dropped = [record["dropped_jobs"] for record in case_records]
        row["dropped_jobs_mean"] = statistics.fmean(dropped)
        row["dropped_jobs_stddev"] = statistics.pstdev(dropped)
        for metric in METRICS:
            values = [record[metric] for record in case_records]
            row[f"{metric}_mean"] = statistics.fmean(values)
            row[f"{metric}_stddev"] = statistics.pstdev(values)
        summary_rows.append(row)

    summary_csv = output_dir / "summary_aggregate.csv"
    fields = [
        "dispatch_policy",
        "wall_time_policy",
        "case",
        "runs",
        "dropped_jobs_mean",
        "dropped_jobs_stddev",
    ] + [
        column for metric in METRICS for column in (f"{metric}_mean", f"{metric}_stddev")
    ]
    with summary_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
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
        "| Dispatch | Wall time | Prediction | Dropped jobs | Turnaround time (sec) | Bounded slowdown | Run time (sec) | Speedup |",
        "|---|---|---|---:|---:|---:|---:|---:|",
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
            f"| {row['dispatch_policy']} | {row['wall_time_policy']} | "
            f"{labels[row['case']]} | {dropped} | "
            + " | ".join(cells)
            + " |"
        )
    if not any(key[2] == "model" for key in grouped):
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

    cases = list(dict.fromkeys(row["case"] for row in summary_rows))
    configurations = list(
        dict.fromkeys(
            (row["dispatch_policy"], row["wall_time_policy"])
            for row in summary_rows
        )
    )
    indexed = {
        (row["dispatch_policy"], row["wall_time_policy"], row["case"]): row
        for row in summary_rows
    }
    case_labels = {
        "ideal": "Ideal",
        "model": "Model-based",
        "app_avg": "Application average",
        "rajaperf": "RAJAPerf",
    }
    configuration_labels = {
        ("turnaround", "adapted-limit"): "Turnaround",
        ("turnaround", "actual-duration"): "Turnaround / limit = duration",
        ("IPDPS24", "adapted-limit"): "IPDPS24",
        ("IPDPS24", "actual-duration"): "IPDPS24 / limit = duration",
    }
    titles = {
        "average_turnaround_time": "Average turnaround time (sec)",
        "average_bounded_slowdown": "Average bounded slowdown",
        "average_run_time": "Average run time (sec)",
        "average_speedup": "Average speedup",
    }
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756"]
    x_positions = list(range(len(cases)))
    width = min(0.18, 0.8 / max(len(configurations), 1))
    for axis, metric in zip(axes.flat, METRICS):
        for index, configuration in enumerate(configurations):
            offset = (index - (len(configurations) - 1) / 2) * width
            positions = [position + offset for position in x_positions]
            rows = [indexed[(*configuration, case)] for case in cases]
            axis.bar(
                positions,
                [row[f"{metric}_mean"] for row in rows],
                width,
                yerr=[row[f"{metric}_stddev"] for row in rows],
                capsize=3,
                color=colors[index % len(colors)],
                label=configuration_labels.get(
                    configuration, " / ".join(configuration)
                ),
            )
        axis.set_title(titles[metric])
        axis.set_xticks(x_positions)
        axis.set_xticklabels(
            [case_labels.get(case, case.replace("_", " ")) for case in cases]
        )
        axis.grid(axis="y", alpha=0.25)
    fig.suptitle("Multi-cluster prediction policies (mean ± SD, 10 traces)")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="outside lower center",
        ncol=min(2, len(labels)),
        frameon=False,
    )
    fig.tight_layout(rect=(0, 0.1, 1, 0.96))
    fig.savefig(output_dir / "summary.png", dpi=180)
    fig.savefig(output_dir / "summary.pdf")
    plt.close(fig)


def load_completed(output_dir, cases, dispatch_policies, wall_time_policies):
    """Load metrics for aggregate-only mode from validated run artifacts."""
    records = []
    for dispatch_policy in dispatch_policies:
        for wall_time_policy in wall_time_policies:
            for case, _ in cases:
                for run_number in range(1, 11):
                    qualified_stem = (
                        f"{dispatch_policy}.{wall_time_policy}.{case}."
                        f"run_{run_number:02d}"
                    )
                    expected_marker = (
                        f"dispatch_policy={dispatch_policy}\n"
                        f"wall_time_policy={wall_time_policy}\n"
                    )
                    marker = output_dir / f"{qualified_stem}.complete"
                    log = output_dir / f"{qualified_stem}.log"
                    dispatch = output_dir / f"{qualified_stem}.dispatch.csv"
                    if not (
                        marker.is_file() and log.is_file() and dispatch.is_file()
                    ):
                        raise FileNotFoundError(
                            f"missing completed run: {qualified_stem}"
                        )

                    if marker.read_text(encoding="utf-8") != expected_marker:
                        raise ValueError(
                            f"configuration mismatch for {qualified_stem}"
                        )
                    record = parse_overall(log.read_text(encoding="utf-8"))
                    with dispatch.open(encoding="utf-8") as stream:
                        rows = sum(1 for _ in stream) - 1
                    if (
                        rows != record["jobs"]
                        or rows + record["dropped_jobs"] != JOBS_PER_TRACE
                    ):
                        raise ValueError(
                            f"incomplete run {qualified_stem}: dispatch rows={rows}, "
                            f"reported jobs={record['jobs']}, "
                            f"dropped={record['dropped_jobs']}"
                        )
                    records.append(
                        {
                            "dispatch_policy": dispatch_policy,
                            "wall_time_policy": wall_time_policy,
                            "case": case,
                            "run": run_number,
                            **record,
                        }
                    )
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
    parser.add_argument(
        "--dispatch-policy",
        action="append",
        choices=DISPATCH_POLICIES,
        help="dispatch policy to run; repeat as needed (default: both)",
    )
    parser.add_argument(
        "--wall-time-policy",
        action="append",
        choices=WALL_TIME_POLICIES,
        help="wall-time policy to run; repeat as needed (default: both)",
    )
    parser.add_argument("--model-prediction", type=Path)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.max_time_limit) or args.max_time_limit <= 0:
        parser.error("--max-time-limit must be finite and positive")

    root = Path(__file__).resolve().parents[2]
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = prediction_cases(root, args.model_prediction)
    dispatch_policies = args.dispatch_policy or list(DISPATCH_POLICIES)
    wall_time_policies = args.wall_time_policy or list(WALL_TIME_POLICIES)

    if args.aggregate_only:
        records = load_completed(
            args.output_dir, cases, dispatch_policies, wall_time_policies
        )
    else:
        if args.executable is None:
            parser.error("--executable is required unless --aggregate-only is used")
        args.executable = args.executable.resolve()
        traces = validate_inputs(root, args.executable, cases)
        records = []
        for dispatch_policy in dispatch_policies:
            for wall_time_policy in wall_time_policies:
                for case, prediction in cases:
                    for run_number, trace in enumerate(traces, start=1):
                        record = run_case(
                            args,
                            root,
                            dispatch_policy,
                            wall_time_policy,
                            case,
                            prediction,
                            trace,
                            run_number,
                        )
                        records.append(
                            {
                                "dispatch_policy": dispatch_policy,
                                "wall_time_policy": wall_time_policy,
                                "case": case,
                                "run": run_number,
                                **record,
                            }
                        )

    summary_rows = write_results(args.output_dir, records)
    plot_results(args.output_dir, summary_rows)
    print(f"wrote results to {args.output_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
