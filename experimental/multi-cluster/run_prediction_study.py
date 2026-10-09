#!/usr/bin/env python3
"""Run and summarize the multi-cluster prediction-policy study.

The study uses ten Lassen synthetic job streams and compares ideal,
application-average, and RAJAPerf predictions under the turnaround-aware and
RelPerfOnly dispatch policies. Each combination is run with adapted predicted
wall times and with wall time set to actual duration.  A model-based case can
be added by passing ``--model-prediction``.
"""

import argparse
import csv
import glob
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
DISPATCH_POLICIES = ("turnaround", "RelPerfOnly")
SUPPORTED_DISPATCH_POLICIES = (*DISPATCH_POLICIES, "WaitTimeOnly")
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


def cases_for_dispatch_policy(dispatch_policy, cases):
    """Return the prediction cases needed by one dispatch policy."""
    if dispatch_policy == "WaitTimeOnly":
        return [("wait_time_only", cases[0][1])]
    return cases


def resolve_jobs_glob(root, jobs_glob):
    """Resolve a caller-selected trace glob relative to the repository root."""
    if jobs_glob is None:
        return str(
            root
            / "experimental/synthesize/lassen/synthetic_traces/100000"
            / "synthetic_jobs_100000_min60s_successful_*.csv"
        )
    pattern = Path(jobs_glob)
    return str(pattern if pattern.is_absolute() else root / pattern)


def validate_inputs(
    root, executable, systems, cases, jobs_glob=None, jobs_per_trace=JOBS_PER_TRACE
):
    """Validate all static inputs before consuming an allocation."""
    required = [
        executable,
        root / "experimental/multi-cluster/apps.csv",
        systems,
        *(prediction for _, prediction in cases),
    ]
    pattern = jobs_glob or resolve_jobs_glob(root, None)
    traces = [Path(name).resolve() for name in sorted(glob.glob(pattern))]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(missing))
    if len(traces) != 10:
        raise ValueError(f"expected exactly 10 job traces, found {len(traces)}")
    for trace in traces:
        with trace.open(encoding="utf-8") as stream:
            rows = sum(1 for _ in stream) - 1
        if rows != jobs_per_trace:
            raise ValueError(
                f"{trace} contains {rows} jobs, expected {jobs_per_trace}"
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
    jobs_per_trace = getattr(args, "jobs_per_trace", JOBS_PER_TRACE)
    stem = f"{dispatch_policy}.{wall_time_policy}.{case}.run_{run_number:02d}"
    dispatch = args.output_dir / f"{stem}.dispatch.csv"
    log = args.output_dir / f"{stem}.log"
    marker = args.output_dir / f"{stem}.complete"
    marker_text = (
        f"dispatch_policy={dispatch_policy}\n"
        f"wall_time_policy={wall_time_policy}\n"
        f"{getattr(args, 'marker_metadata', '')}"
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
                and rows + record["dropped_jobs"] == jobs_per_trace
            ):
                print(f"skip validated {stem}", flush=True)
                return record
        except (OSError, ValueError):
            pass

    marker.unlink(missing_ok=True)
    command = build_command(
        args,
        root,
        dispatch_policy,
        wall_time_policy,
        prediction,
        trace,
        dispatch,
    )
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
        or rows + record["dropped_jobs"] != jobs_per_trace
    ):
        raise RuntimeError(
            f"{stem} is incomplete: dispatch rows={rows}, "
            f"reported jobs={record['jobs']}, dropped={record['dropped_jobs']}"
        )
    marker.write_text(marker_text, encoding="utf-8")
    return record


def build_command(
    args, root, dispatch_policy, wall_time_policy, prediction, trace, dispatch
):
    """Build the MPI dispatcher command for one prediction-study run."""
    ground_truth = getattr(
        args, "ground_truth", root / "experimental/multi-cluster/ground_truth.csv"
    )
    applications = getattr(
        args, "applications", root / "experimental/multi-cluster/apps.csv"
    )
    return [
        *args.launcher,
        "-n",
        str(args.ranks),
        str(args.executable),
        "--jobs",
        str(trace),
        "--ground-truth",
        str(ground_truth),
        "--prediction",
        str(prediction),
        "--applications",
        str(applications),
        "--systems",
        str(args.systems),
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


def write_results(output_dir, records, missing_model_note=True):
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
        "app_average_per_machine": "App average (100%)",
        "app_average_5_percent": "App average (5% train)",
        "rajaperf": "RAJAPerf",
        "wait_time_only": "WaitTimeOnly",
    }
    dispatch_labels = {
        "RelPerfOnly": "RelPerfOnly",
        "WaitTimeOnly": "WaitTimeOnly",
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
            f"| {dispatch_labels.get(row['dispatch_policy'], row['dispatch_policy'])} | "
            f"{row['wall_time_policy']} | "
            f"{labels.get(row['case'], row['case'].replace('_', ' ').title())} | "
            f"{dropped} | "
            + " | ".join(cells)
            + " |"
        )
    if missing_model_note and not any(key[2] == "model" for key in grouped):
        lines.extend(
            ["", "Model-based prediction was not run because no model prediction table was supplied."]
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_rows


def rows_for_summary_plot(summary_rows):
    """Return adapted-limit rows displayed in the summary visualization."""
    return [
        row for row in summary_rows if row["wall_time_policy"] == "adapted-limit"
    ]


def plot_results(output_dir, summary_rows):
    """Plot adapted-limit mean metrics with run-to-run error bars."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError(
            "matplotlib is required for the plot; tables were still generated"
        ) from error

    summary_rows = rows_for_summary_plot(summary_rows)
    if not summary_rows:
        raise ValueError("summary plot requires adapted-limit results")

    all_cases = list(dict.fromkeys(row["case"] for row in summary_rows))
    configurations = list(
        dict.fromkeys(
            (row["dispatch_policy"], row["wall_time_policy"])
            for row in summary_rows
        )
    )
    prediction_cases = [
        case
        for case in all_cases
        if case
        not in {
            "wait_time_only",
            "app_average_per_machine",
            "app_average_5_percent",
        }
    ]
    show_wait_time_baseline = bool(
        prediction_cases
        and any(
            configuration[0] == "WaitTimeOnly"
            for configuration in configurations
        )
    )
    cases = prediction_cases
    bar_configurations = [
        configuration
        for configuration in configurations
        if not (show_wait_time_baseline and configuration[0] == "WaitTimeOnly")
    ]
    indexed = {
        (row["dispatch_policy"], row["wall_time_policy"], row["case"]): row
        for row in summary_rows
    }
    case_labels = {
        "ideal": "Ideal",
        "model": "Model-based",
        "app_avg": "Application average",
        "rajaperf": "RAJAPerf",
        "fully_trained": "Fully trained",
        "knowledge_transfer_1_percent": "KT 1%",
        "knowledge_transfer_3_percent": "KT 3%",
        "knowledge_transfer_5_percent": "KT 5%",
        "app_average_per_machine": "App average",
        "app_average_5_percent": "App average (5% train)",
        "sys_bench": "Sys bench",
        "wait_time_only": "WaitTimeOnly",
    }
    configuration_labels = {
        ("turnaround", "adapted-limit"): "Turnaround",
        ("turnaround", "actual-duration"): "Turnaround / limit = duration",
        ("RelPerfOnly", "adapted-limit"): "RelPerfOnly",
        ("RelPerfOnly", "actual-duration"): "RelPerfOnly / limit = duration",
        ("WaitTimeOnly", "adapted-limit"): "WaitTimeOnly",
        ("WaitTimeOnly", "actual-duration"): "WaitTimeOnly / limit = duration",
    }
    y_labels = {
        "average_turnaround_time": "Average turnaround time (sec)",
        "average_bounded_slowdown": "Average bounded slowdown",
        "average_run_time": "Average run time (sec)",
        "average_speedup": "Average speedup",
    }
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2"]
    x_positions = list(range(len(cases)))
    width = min(0.18, 0.8 / max(len(bar_configurations), 1))
    for panel, (axis, metric) in enumerate(zip(axes.flat, METRICS)):
        for index, configuration in enumerate(bar_configurations):
            offset = (index - (len(bar_configurations) - 1) / 2) * width
            available = [
                (position, indexed[(*configuration, case)])
                for position, case in zip(x_positions, cases)
                if (*configuration, case) in indexed
            ]
            positions = [position + offset for position, _ in available]
            rows = [row for _, row in available]
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
        if show_wait_time_baseline:
            for configuration in configurations:
                if configuration[0] != "WaitTimeOnly":
                    continue
                row = indexed[(*configuration, "wait_time_only")]
                mean = row[f"{metric}_mean"]
                stddev = row[f"{metric}_stddev"]
                color = "#B279A2"
                axis.axhspan(
                    max(0.0, mean - stddev),
                    mean + stddev,
                    color=color,
                    alpha=0.12,
                    label="_nolegend_",
                )
                axis.axhline(
                    mean,
                    color=color,
                    linewidth=2,
                    linestyle="--",
                    label=configuration_labels.get(
                        configuration, " / ".join(configuration)
                    ),
                )
        axis.set_ylabel(y_labels[metric])
        axis.set_xlabel(f"({chr(ord('a') + panel)})", labelpad=10)
        axis.set_xticks(x_positions)
        axis.set_xticklabels(
            [case_labels.get(case, case.replace("_", " ")) for case in cases],
            rotation=20,
            ha="right",
            rotation_mode="anchor",
        )
        axis.grid(axis="y", alpha=0.25)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    axes.flat[0].legend(
        handles,
        labels,
        loc="upper right",
        ncol=1,
        frameon=True,
        fontsize="small",
    )
    fig.tight_layout()
    fig.savefig(output_dir / "summary.png", dpi=180)
    fig.savefig(output_dir / "summary.pdf")
    plt.close(fig)


def load_completed(
    output_dir,
    cases,
    dispatch_policies,
    wall_time_policies,
    marker_metadata="",
    jobs_per_trace=JOBS_PER_TRACE,
):
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
                        f"{marker_metadata}"
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
                        or rows + record["dropped_jobs"] != jobs_per_trace
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
        "--systems",
        type=Path,
        help="systems CSV (default: experimental/multi-cluster/machines.csv)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experimental/multi-cluster/dispatch-exp-results"),
    )
    parser.add_argument(
        "--launcher", nargs="+", default=["srun"], help="MPI launcher prefix (default: srun)"
    )
    parser.add_argument("--ranks", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-time-limit", type=float, default=43200.0)
    parser.add_argument(
        "--jobs-glob",
        help="trace glob relative to the repository root (default: 100K traces)",
    )
    parser.add_argument(
        "--jobs-per-trace",
        type=int,
        default=JOBS_PER_TRACE,
        help="required number of jobs in each trace (default: 100000)",
    )
    parser.add_argument(
        "--dispatch-policy",
        action="append",
        choices=SUPPORTED_DISPATCH_POLICIES,
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
    if args.jobs_per_trace <= 0:
        parser.error("--jobs-per-trace must be greater than zero")

    root = Path(__file__).resolve().parents[2]
    custom_jobs = args.jobs_glob is not None or args.jobs_per_trace != JOBS_PER_TRACE
    args.jobs_glob = resolve_jobs_glob(root, args.jobs_glob)
    args.marker_metadata = (
        f"jobs_glob={args.jobs_glob}\njobs_per_trace={args.jobs_per_trace}\n"
        if custom_jobs
        else ""
    )
    args.systems = (
        args.systems or root / "experimental/multi-cluster/machines.csv"
    ).resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = prediction_cases(root, args.model_prediction)
    dispatch_policies = args.dispatch_policy or list(DISPATCH_POLICIES)
    wall_time_policies = args.wall_time_policy or list(WALL_TIME_POLICIES)

    if args.aggregate_only:
        records = []
        for dispatch_policy in dispatch_policies:
            records.extend(
                load_completed(
                    args.output_dir,
                    cases_for_dispatch_policy(dispatch_policy, cases),
                    [dispatch_policy],
                    wall_time_policies,
                    args.marker_metadata,
                    args.jobs_per_trace,
                )
            )
    else:
        if args.executable is None:
            parser.error("--executable is required unless --aggregate-only is used")
        args.executable = args.executable.resolve()
        traces = validate_inputs(
            root,
            args.executable,
            args.systems,
            cases,
            args.jobs_glob,
            args.jobs_per_trace,
        )
        records = []
        for dispatch_policy in dispatch_policies:
            for wall_time_policy in wall_time_policies:
                for case, prediction in cases_for_dispatch_policy(
                    dispatch_policy, cases
                ):
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
