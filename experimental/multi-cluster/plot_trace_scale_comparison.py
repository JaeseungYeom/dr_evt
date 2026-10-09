#!/usr/bin/env python3
r"""Plot common policies across caller-selected trace-scale campaigns.

Each ``--campaign LABEL=CSV[,CSV]`` input names one plot panel and one or more
aggregate summary CSVs.  Every summary must use the schema emitted by
``run_kt_scheduling_study.py`` and collectively contain ten-run, adapted-limit
records for Turnaround, RelPerfOnly, and WaitTimeOnly.

``--output-dir`` receives the exact selected records in
``trace-scale-comparison-data.csv`` and turnaround/slowdown figures in both
PNG and PDF.  Input and output paths are always supplied by the caller; the
script does not depend on local campaign directories.

Example::

    python plot_trace_scale_comparison.py \
      --campaign 100K=/results/100k/summary_aggregate.csv,/results/100k-wait/summary_aggregate.csv \
      --campaign 25K=/results/25k/summary_aggregate.csv \
      --output-dir /results/trace-scale-plots
"""

import argparse
import csv
from pathlib import Path


POLICIES = (
    ("turnaround", "Turnaround-aware", "#4C78A8"),
    ("RelPerfOnly", "RelPerfOnly", "#F58518"),
)
CASES = (
    ("ideal", "Ideal"),
    ("fully_trained", "Full"),
    ("knowledge_transfer_1_percent", "KT 1%"),
    ("knowledge_transfer_3_percent", "KT 3%"),
    ("knowledge_transfer_5_percent", "KT 5%"),
    ("sys_bench", "Sys bench"),
)
METRICS = (
    (
        "average_turnaround_time",
        "Mean turnaround time ($10^3$ s)",
        "turnaround-comparison",
        0.001,
    ),
    (
        "average_bounded_slowdown",
        "Mean bounded slowdown",
        "bounded-slowdown-comparison",
        1.0,
    ),
)


def parse_campaign_spec(value):
    """Parse ``LABEL=CSV[,CSV]`` without assuming repository-local inputs."""
    label, separator, path_list = value.partition("=")
    paths = tuple(Path(item) for item in path_list.split(",") if item)
    if not separator or not label or not paths:
        raise argparse.ArgumentTypeError(
            "campaign must have the form LABEL=CSV[,CSV]"
        )
    return label, paths


def load_campaign(paths, case_aliases=None):
    """Load both adapted-limit policies for the six prediction cases."""
    case_aliases = case_aliases or {}
    records = {}
    for path in paths:
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                key = (row["dispatch_policy"], row["wall_time_policy"], row["case"])
                if key in records:
                    raise ValueError(f"duplicate configuration {key} in {path}")
                records[key] = row

    selected = []
    for case, _ in CASES:
        source_case = case_aliases.get(case, case)
        case_rows = []
        for policy, _, _ in POLICIES:
            key = (policy, "adapted-limit", source_case)
            if key not in records:
                raise ValueError(f"missing configuration {key} in campaign inputs")
            row = records[key]
            if int(row["runs"]) != 10:
                raise ValueError(
                    f"configuration {key} has {row['runs']} runs, expected 10"
                )
            case_rows.append(row)
        selected.append(case_rows)
    wait_key = ("WaitTimeOnly", "adapted-limit", "wait_time_only")
    if wait_key not in records:
        raise ValueError(f"missing configuration {wait_key} in campaign inputs")
    wait_row = records[wait_key]
    if int(wait_row["runs"]) != 10:
        raise ValueError(
            f"configuration {wait_key} has {wait_row['runs']} runs, expected 10"
        )
    return {"cases": selected, "wait": wait_row}


def write_plot_data(campaigns, output_dir):
    """Write the exact aggregate records selected for the comparison plots."""
    fields = (
        "campaign",
        "dispatch_policy",
        "case",
        "case_label",
        "runs",
        "average_turnaround_time_mean",
        "average_turnaround_time_stddev",
        "average_bounded_slowdown_mean",
        "average_bounded_slowdown_stddev",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "trace-scale-comparison-data.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for campaign_name, campaign in campaigns:
            for (case, case_label), case_rows in zip(CASES, campaign["cases"]):
                for row in case_rows:
                    writer.writerow(
                        {
                            "campaign": campaign_name,
                            "dispatch_policy": row["dispatch_policy"],
                            "case": case,
                            "case_label": case_label,
                            **{field: row[field] for field in fields[4:]},
                        }
                    )
            wait = campaign["wait"]
            writer.writerow(
                {
                    "campaign": campaign_name,
                    "dispatch_policy": wait["dispatch_policy"],
                    "case": "wait_time_only",
                    "case_label": "WaitTimeOnly",
                    **{field: wait[field] for field in fields[4:]},
                }
            )


def plot_metric(campaigns, metric, ylabel, output_stem, scale, output_dir):
    """Write one four-panel IEEE double-column figure in PNG and PDF."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("matplotlib is required to create comparison plots") from error

    plt.rcParams.update(
        {
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 8,
            "legend.fontsize": 7,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    figure, axes = plt.subplots(1, 4, figsize=(7.16, 2.35))
    legend_handles = []
    for axis, (campaign_name, campaign) in zip(axes, campaigns):
        records = campaign["cases"]
        maximum = 0.0
        width = 0.38
        for policy_index, (_, _, color) in enumerate(POLICIES):
            policy_rows = [case_rows[policy_index] for case_rows in records]
            means = [float(row[f"{metric}_mean"]) * scale for row in policy_rows]
            errors = [
                float(row[f"{metric}_stddev"]) * scale for row in policy_rows
            ]
            positions = [
                index + (policy_index - (len(POLICIES) - 1) / 2) * width
                for index in range(len(CASES))
            ]
            bars = axis.bar(
                positions,
                means,
                yerr=errors,
                color=color,
                width=width,
                capsize=1.5,
                error_kw={"elinewidth": 0.7, "capthick": 0.7},
            )
            if len(legend_handles) <= policy_index:
                legend_handles.append(bars[0])
            maximum = max(
                maximum,
                max(mean + error for mean, error in zip(means, errors)),
            )
        wait_mean = float(campaign["wait"][f"{metric}_mean"]) * scale
        wait_error = float(campaign["wait"][f"{metric}_stddev"]) * scale
        axis.axhspan(
            max(0.0, wait_mean - wait_error),
            wait_mean + wait_error,
            color="#B279A2",
            alpha=0.12,
            zorder=0,
        )
        wait_line = axis.axhline(
            wait_mean,
            color="#B279A2",
            linestyle="--",
            linewidth=1.1,
            zorder=1,
        )
        if len(legend_handles) == len(POLICIES):
            legend_handles.append(wait_line)
        maximum = max(maximum, wait_mean + wait_error)
        axis.set_xticks(range(len(CASES)))
        axis.set_xticklabels(
            [label for _, label in CASES],
            rotation=45,
            ha="right",
            rotation_mode="anchor",
            fontsize=5.5,
        )
        axis.set_ylim(0, maximum * 1.20)
        axis.text(
            0.5,
            0.97,
            campaign_name,
            transform=axis.transAxes,
            ha="center",
            va="top",
            fontsize=8,
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.8,
                "pad": 1,
            },
        )
        axis.grid(axis="y", alpha=0.25, linewidth=0.5)
        axis.set_axisbelow(True)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    axes[0].set_ylabel(ylabel)
    figure.legend(
        legend_handles,
        [*[label for _, label, _ in POLICIES], "WaitTimeOnly"],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=3,
        frameon=False,
        columnspacing=1.5,
        handlelength=1.5,
    )
    figure.subplots_adjust(left=0.075, right=0.995, bottom=0.27, top=0.86, wspace=0.50)
    output_dir.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_dir / f"{output_stem}.png", dpi=300)
    figure.savefig(output_dir / f"{output_stem}.pdf")
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--campaign",
        action="append",
        required=True,
        type=parse_campaign_spec,
        metavar="LABEL=CSV[,CSV]",
        help="campaign label and aggregate summary CSVs; repeat for each panel",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="directory for the selected-data CSV and PNG/PDF figures",
    )
    args = parser.parse_args()

    campaigns = [
        (name, load_campaign([path.resolve() for path in paths]))
        for name, paths in args.campaign
    ]
    output_dir = args.output_dir.resolve()
    write_plot_data(campaigns, output_dir)
    for metric, ylabel, output_stem, scale in METRICS:
        plot_metric(campaigns, metric, ylabel, output_stem, scale, output_dir)
    print(f"wrote comparison plots to {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"error: {error}")
