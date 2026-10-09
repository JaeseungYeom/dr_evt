#!/usr/bin/env python3
r"""Validate, replay, and summarize a completed KT scheduling campaign.

``--output-dir`` must contain the ``*.complete``, ``*.log``, and
``*.dispatch.csv`` files created by ``run_kt_scheduling_study.py``.
``--systems`` is the ``machine,size,GPU`` CSV used for that campaign.  The
script validates the run matrix, invokes ``replay_dispatch.py`` for each
recorded assignment, and never recomputes dispatch decisions.

The same directory receives refreshed scheduling summaries plus
``replay_metrics_per_run.csv``, ``average_wait_by_system.csv``,
``application_execution_mode_counts.csv``, ``analysis.md``, and wait plots in
PNG/PDF format.  Existing valid replay logs are reused.

Example::

    python analyze_kt_scheduling_campaign.py \
      --output-dir /results/kt-scheduling \
      --systems machines.csv \
      --jobs-per-trace 25000
"""

import argparse
import csv
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import run_prediction_study as study


STEM_RE = re.compile(
    r"^(turnaround|RelPerfOnly|WaitTimeOnly)\."
    r"(adapted-limit|actual-duration)\.(.+)\.run_(\d{2})$"
)
REPLAY_RE = re.compile(
    r"^(?P<system>[^:]+): jobs=(?P<jobs>\d+) "
    r"average_wait=(?P<wait>[-+0-9.eE]+) "
    r"average_turnaround=(?P<turnaround>[-+0-9.eE]+)"
)
CASE_ORDER = (
    "ideal",
    "fully_trained",
    "knowledge_transfer_1_percent",
    "knowledge_transfer_3_percent",
    "knowledge_transfer_5_percent",
    "app_average_per_machine",
    "app_average_5_percent",
    "sys_bench",
    "wait_time_only",
)
CASE_LABELS = {
    "ideal": "Ideal",
    "fully_trained": "Fully trained",
    "knowledge_transfer_1_percent": "KT 1%",
    "knowledge_transfer_3_percent": "KT 3%",
    "knowledge_transfer_5_percent": "KT 5%",
    "app_average_per_machine": "App average",
    "app_average_5_percent": "App average (5% train)",
    "sys_bench": "Sys bench",
    "wait_time_only": "WaitTimeOnly",
}


def read_systems(path):
    """Return ordered system capacities from a systems CSV."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        reader.fieldnames[0] = reader.fieldnames[0].lstrip("#")
        return {row["machine"]: int(row["size"]) for row in reader}


def read_replay(path, systems):
    """Parse and validate one replay report."""
    rows = {}
    overall = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = REPLAY_RE.match(line)
        if not match:
            continue
        record = {
            "jobs": int(match.group("jobs")),
            "average_wait": float(match.group("wait")),
            "average_turnaround": float(match.group("turnaround")),
        }
        name = match.group("system")
        if name == "overall":
            overall = record
        else:
            rows[name] = record
    if set(rows) != set(systems) or overall is None:
        raise ValueError(f"incomplete replay report: {path}")
    if sum(row["jobs"] for row in rows.values()) != overall["jobs"]:
        raise ValueError(f"replay job total mismatch: {path}")
    return rows, overall


def parse_stem(marker):
    """Return policy fields encoded by a completion-marker filename."""
    match = STEM_RE.fullmatch(marker.name.removesuffix(".complete"))
    if not match:
        raise ValueError(f"unrecognized completion marker: {marker}")
    dispatch, wall_time, case, run = match.groups()
    return dispatch, wall_time, case, int(run)


def validate_matrix(group_counts):
    """Require the intended 290-run campaign matrix."""
    expected = {
        (dispatch, wall_time, case)
        for dispatch in ("turnaround", "RelPerfOnly")
        for wall_time in study.WALL_TIME_POLICIES
        for case in CASE_ORDER
        if case not in {"app_average_5_percent", "wait_time_only"}
    }
    if any(key[2] == "app_average_5_percent" for key in group_counts):
        expected.update(
            (dispatch, wall_time, "app_average_5_percent")
            for dispatch in ("turnaround", "RelPerfOnly")
            for wall_time in study.WALL_TIME_POLICIES
        )
    if any(key[0] == "WaitTimeOnly" for key in group_counts):
        expected.add(("WaitTimeOnly", "adapted-limit", "wait_time_only"))
    if set(group_counts) != expected:
        missing = sorted(expected - set(group_counts))
        extra = sorted(set(group_counts) - expected)
        raise ValueError(f"campaign matrix mismatch: missing={missing}, extra={extra}")
    incomplete = {key: count for key, count in group_counts.items() if count != 10}
    if incomplete:
        raise ValueError(f"campaign groups do not contain ten runs: {incomplete}")


def write_csv(path, fieldnames, rows):
    """Write dictionaries with stable Unix newlines."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def format_duration(seconds):
    """Format seconds and the equivalent hours."""
    return f"{seconds:,.0f} s ({seconds / 3600:.2f} h)"


def read_aggregate_summary(path):
    """Read an aggregate summary with numeric fields restored."""
    numeric_fields = {
        "runs",
        "dropped_jobs_mean",
        "dropped_jobs_stddev",
        *(
            f"{metric}_{suffix}"
            for metric in study.METRICS
            for suffix in ("mean", "stddev")
        ),
    }
    with path.open(newline="", encoding="utf-8") as stream:
        return [
            {
                field: (
                    int(value)
                    if field == "runs"
                    else float(value)
                    if field in numeric_fields
                    else value
                )
                for field, value in row.items()
            }
            for row in csv.DictReader(stream)
        ]


def read_plot_baselines(paths):
    """Read nonduplicated WaitTimeOnly rows for inclusion in summary plots."""
    rows = []
    seen = set()
    for path in paths:
        for row in read_aggregate_summary(path):
            key = (
                row["dispatch_policy"],
                row["wall_time_policy"],
                row["case"],
            )
            if key != ("WaitTimeOnly", "adapted-limit", "wait_time_only"):
                raise ValueError(f"unexpected plot baseline in {path}: {key}")
            if key in seen:
                raise ValueError(f"duplicate plot baseline in {path}: {key}")
            seen.add(key)
            rows.append(row)
    return rows


def plot_waits(output_dir, wait_rows, systems):
    """Plot average replayed wait by prediction case and system."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    configurations = (
        ("turnaround", "adapted-limit"),
        ("turnaround", "actual-duration"),
        ("RelPerfOnly", "adapted-limit"),
        ("RelPerfOnly", "actual-duration"),
    )
    labels = {
        ("turnaround", "adapted-limit"): "Turnaround",
        ("turnaround", "actual-duration"): "Turnaround / limit = duration",
        ("RelPerfOnly", "adapted-limit"): "RelPerfOnly",
        ("RelPerfOnly", "actual-duration"): "RelPerfOnly / limit = duration",
    }
    indexed = {
        (row["dispatch_policy"], row["wall_time_policy"], row["case"], row["system"]): row
        for row in wait_rows
    }
    cases = [
        case
        for case in CASE_ORDER
        if case
        not in {
            "wait_time_only",
            "app_average_per_machine",
            "app_average_5_percent",
        }
    ]
    fig, axes = plt.subplots(3, 2, figsize=(16, 13), sharex=True)
    colors = ("#4C78A8", "#F58518", "#54A24B", "#E45756")
    positions = list(range(len(cases)))
    width = 0.18
    for axis, system in zip(axes.flat, systems):
        for index, configuration in enumerate(configurations):
            values = [
                indexed[(*configuration, case, system)]["average_wait_seconds"] / 3600
                for case in cases
            ]
            offsets = [
                position + (index - 1.5) * width for position in positions
            ]
            axis.bar(
                offsets,
                values,
                width,
                color=colors[index],
                label=labels[configuration],
            )
        baseline_key = ("WaitTimeOnly", "adapted-limit", "wait_time_only", system)
        if baseline_key in indexed:
            baseline = indexed[baseline_key]
            axis.axhline(
                baseline["average_wait_seconds"] / 3600,
                color="#B279A2",
                linestyle="--",
                linewidth=2,
                label="WaitTimeOnly",
            )
        axis.set_title(f"{system} ({systems[system]} nodes)")
        axis.set_ylabel("Average wait (hours)")
        axis.grid(axis="y", alpha=0.25)
    axes.flat[-1].axis("off")
    for axis in axes.flat[:-1]:
        axis.set_xticks(positions)
        axis.set_xticklabels(
            [CASE_LABELS[case] for case in cases], rotation=25, ha="right"
        )
    handles, labels_text = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels_text, loc="outside lower center", ncol=3, frameon=False)
    fig.suptitle("Replayed average wait by system (job-weighted over 10 traces)")
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))
    fig.savefig(output_dir / "average_wait_by_system.png", dpi=180)
    fig.savefig(output_dir / "average_wait_by_system.pdf")
    plt.close(fig)


def analyze(output_dir, systems_path, jobs_per_trace, plot_baseline_summaries=()):
    """Validate artifacts and write combined campaign analysis outputs."""
    systems = read_systems(systems_path)
    records = []
    group_counts = defaultdict(int)
    replay_runs = []
    wait_totals = defaultdict(lambda: [0, 0.0, set()])
    placements = defaultdict(lambda: {"CPU": 0, "GPU": 0})

    markers = sorted(output_dir.glob("*.complete"))
    for marker in markers:
        dispatch_policy, wall_time_policy, case, run = parse_stem(marker)
        stem = marker.name.removesuffix(".complete")
        log = output_dir / f"{stem}.log"
        mapping = output_dir / f"{stem}.dispatch.csv"
        replay_log = output_dir / f"{stem}.replay.log"
        if not all(path.is_file() and path.stat().st_size for path in (log, mapping, replay_log)):
            raise FileNotFoundError(f"missing or empty artifact for {stem}")
        marker_text = marker.read_text(encoding="utf-8")
        if (
            f"dispatch_policy={dispatch_policy}\n" not in marker_text
            or f"wall_time_policy={wall_time_policy}\n" not in marker_text
        ):
            raise ValueError(f"marker metadata mismatch: {marker}")

        metric = study.parse_overall(log.read_text(encoding="utf-8"))
        replay_systems, replay_overall = read_replay(replay_log, systems)
        row_count = 0
        with mapping.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                row_count += 1
                if case in {"ideal", "knowledge_transfer_5_percent", "wait_time_only"}:
                    key = (
                        dispatch_policy,
                        wall_time_policy,
                        case,
                        row["App"],
                    )
                    placements[key][row["execution_mode"]] += 1
        if row_count != metric["jobs"] or row_count + metric["dropped_jobs"] != jobs_per_trace:
            raise ValueError(f"job accounting mismatch for {stem}")
        if replay_overall["jobs"] != row_count:
            raise ValueError(f"replay job accounting mismatch for {stem}")
        if not math.isclose(
            replay_overall["average_turnaround"],
            metric["average_turnaround_time"],
            rel_tol=2e-7,
            # Two WaitTimeOnly traces differ by less than one second after
            # replay; all other campaign runs reproduce the logged value.
            abs_tol=1.0,
        ):
            raise ValueError(f"replay turnaround mismatch for {stem}")

        records.append(
            {
                "dispatch_policy": dispatch_policy,
                "wall_time_policy": wall_time_policy,
                "case": case,
                "run": run,
                **metric,
            }
        )
        group_counts[(dispatch_policy, wall_time_policy, case)] += 1
        replay_runs.append(
            {
                "dispatch_policy": dispatch_policy,
                "wall_time_policy": wall_time_policy,
                "case": case,
                "run": run,
                "jobs": replay_overall["jobs"],
                "average_wait_seconds": replay_overall["average_wait"],
                "average_wait_hours": replay_overall["average_wait"] / 3600,
            }
        )
        for system, replay_row in replay_systems.items():
            key = (dispatch_policy, wall_time_policy, case, system)
            wait_totals[key][0] += replay_row["jobs"]
            wait_totals[key][1] += replay_row["jobs"] * replay_row["average_wait"]
            wait_totals[key][2].add(run)

    validate_matrix(group_counts)
    expected_runs = sum(group_counts.values())
    if len(records) != expected_runs:
        raise ValueError(
            f"found {len(records)} validated runs, expected {expected_runs}"
        )

    records.sort(
        key=lambda row: (
            ("turnaround", "RelPerfOnly", "WaitTimeOnly").index(row["dispatch_policy"]),
            study.WALL_TIME_POLICIES.index(row["wall_time_policy"]),
            CASE_ORDER.index(row["case"]),
            row["run"],
        )
    )
    summary_rows = study.write_results(output_dir, records, missing_model_note=False)
    plot_rows = [*summary_rows, *read_plot_baselines(plot_baseline_summaries)]
    study.plot_results(output_dir, plot_rows)

    replay_runs.sort(
        key=lambda row: (
            ("turnaround", "RelPerfOnly", "WaitTimeOnly").index(row["dispatch_policy"]),
            study.WALL_TIME_POLICIES.index(row["wall_time_policy"]),
            CASE_ORDER.index(row["case"]),
            row["run"],
        )
    )
    write_csv(
        output_dir / "replay_metrics_per_run.csv",
        replay_runs[0].keys(),
        replay_runs,
    )

    wait_rows = []
    for key, (jobs, wait_sum, runs) in wait_totals.items():
        dispatch_policy, wall_time_policy, case, system = key
        average = wait_sum / jobs
        wait_rows.append(
            {
                "dispatch_policy": dispatch_policy,
                "wall_time_policy": wall_time_policy,
                "case": case,
                "system": system,
                "nodes": systems[system],
                "runs": len(runs),
                "jobs": jobs,
                "average_wait_seconds": average,
                "average_wait_hours": average / 3600,
            }
        )
    wait_rows.sort(
        key=lambda row: (
            ("turnaround", "RelPerfOnly", "WaitTimeOnly").index(row["dispatch_policy"]),
            study.WALL_TIME_POLICIES.index(row["wall_time_policy"]),
            CASE_ORDER.index(row["case"]),
            tuple(systems).index(row["system"]),
        )
    )
    write_csv(output_dir / "average_wait_by_system.csv", wait_rows[0].keys(), wait_rows)

    placement_rows = []
    for key, counts in placements.items():
        dispatch_policy, wall_time_policy, case, app = key
        placement_rows.append(
            {
                "dispatch_policy": dispatch_policy,
                "wall_time_policy": wall_time_policy,
                "case": case,
                "application": app,
                "CPU": counts["CPU"],
                "GPU": counts["GPU"],
                "total": counts["CPU"] + counts["GPU"],
            }
        )
    placement_rows.sort(
        key=lambda row: (
            ("turnaround", "RelPerfOnly", "WaitTimeOnly").index(row["dispatch_policy"]),
            study.WALL_TIME_POLICIES.index(row["wall_time_policy"]),
            CASE_ORDER.index(row["case"]),
            row["application"],
        )
    )
    write_csv(
        output_dir / "application_execution_mode_counts.csv",
        placement_rows[0].keys(),
        placement_rows,
    )

    lines = [
        "# KT scheduling campaign analysis",
        "",
        f"Validated {len(records)} completed simulations.",
        "Waits were obtained by replaying recorded assignments with `replay_dispatch.py`.",
        "",
        "## Average wait by system",
        "",
        "| Dispatch | Wall time | Prediction | System | Nodes | Jobs | Average wait |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in wait_rows:
        lines.append(
            f"| {row['dispatch_policy']} | {row['wall_time_policy']} | "
            f"{CASE_LABELS[row['case']]} | {row['system']} | {row['nodes']} | "
            f"{row['jobs']:,} | {format_duration(row['average_wait_seconds'])} |"
        )
    for selected_case in ("ideal", "knowledge_transfer_5_percent", "wait_time_only"):
        if not any(row["case"] == selected_case for row in placement_rows):
            continue
        lines.extend(
            [
                "",
                f"## Application CPU/GPU counts: {CASE_LABELS[selected_case]}",
                "",
                "Counts are totals over 10 traces.",
                "",
                "| Dispatch | Wall time | Application | CPU | GPU | Total |",
                "|---|---|---|---:|---:|---:|",
            ]
        )
        for row in placement_rows:
            if row["case"] == selected_case:
                lines.append(
                    f"| {row['dispatch_policy']} | {row['wall_time_policy']} | "
                    f"{row['application']} | {row['CPU']:,} | {row['GPU']:,} | "
                    f"{row['total']:,} |"
                )
    (output_dir / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    plot_waits(output_dir, wait_rows, systems)
    print(f"validated {len(records)} runs and wrote combined analysis to {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="completed campaign directory; analysis files are written here",
    )
    parser.add_argument(
        "--systems",
        required=True,
        type=Path,
        help="machine,size,GPU systems CSV used by the campaign",
    )
    parser.add_argument(
        "--jobs-per-trace",
        type=int,
        default=25_000,
        help="submitted plus dropped jobs expected per run (default: 25000)",
    )
    parser.add_argument(
        "--plot-baseline-summary",
        action="append",
        default=[],
        type=Path,
        help="aggregate WaitTimeOnly CSV to include in summary plots",
    )
    args = parser.parse_args()
    analyze(
        args.output_dir.resolve(),
        args.systems.resolve(),
        args.jobs_per_trace,
        [path.resolve() for path in args.plot_baseline_summary],
    )


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
