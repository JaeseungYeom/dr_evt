#!/usr/bin/env python3
r"""Build a Borax-relative application-average prediction table.

The long-form training CSV must contain ``app,args,ranks,source_machine,
target_machine,true_relative_runtime``.  For each workload and source machine,
the target value is divided by the matching ``--reference`` target (``borax``
by default).  The resulting ratios are averaged first across sources and then
across argument variants for the same ``(App, Ranks, target machine)``.

``--ground-truth`` supplies the output workload rows and execution-mode column
names.  It must be a wide CSV beginning with ``App,Args,Ranks``.  ``--output``
is another wide CSV with that same schema; cells without both ground-truth
coverage and a training estimate are left empty.

Example::

    python build_app_average_from_training.py \
      --training combined_train_5percent.csv \
      --ground-truth ground_truth.csv \
      --output application_average_5pct_training_relative_performance_borax.csv
"""

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


IDENTITY = ("App", "Args", "Ranks")
TRAINING_FIELDS = {
    "app",
    "args",
    "ranks",
    "source_machine",
    "target_machine",
    "true_relative_runtime",
}


def positive(value, context):
    """Parse one positive finite relative-performance value."""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{context} must be positive and finite")
    return parsed


def read_ground_truth(path):
    """Read the target workload layout and execution-mode columns."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        by_lower = {field.lower(): field for field in reader.fieldnames or ()}
        if not all(field.lower() in by_lower for field in IDENTITY):
            raise ValueError(f"{path} must contain {', '.join(IDENTITY)}")
        source_identity = tuple(by_lower[field.lower()] for field in IDENTITY)
        modes = [field for field in reader.fieldnames if field not in source_identity]
        rows = []
        for source in reader:
            row = {
                output: source[input_name]
                for output, input_name in zip(IDENTITY, source_identity)
            }
            row.update({mode: source[mode] for mode in modes})
            rows.append(row)
    if not rows or not modes:
        raise ValueError(f"{path} has no workload or performance data")
    return rows, modes


def training_means(path, modes, reference="borax"):
    """Return Borax-relative means keyed by application, ranks, and mode.

    Each long-form value is relative to its ``source_machine``. A target value
    is divided by the matching reference-target value for the same workload
    and source. Multiple source-machine estimates of one workload are averaged
    before workload values are averaged, so workloads receive equal weight.
    """
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not TRAINING_FIELDS.issubset(reader.fieldnames):
            missing = sorted(TRAINING_FIELDS - set(reader.fieldnames or ()))
            raise ValueError(f"{path} is missing fields: {', '.join(missing)}")
        indexed = {}
        for row_number, row in enumerate(reader, start=2):
            key = (
                row["app"].strip(),
                row["args"].strip(),
                str(int(row["ranks"])),
                row["source_machine"].strip(),
                row["target_machine"].strip(),
            )
            if key in indexed:
                raise ValueError(f"{path}:{row_number}: duplicate training row {key}")
            indexed[key] = positive(
                row["true_relative_runtime"],
                f"{path}:{row_number}:true_relative_runtime",
            )

    per_workload = defaultdict(list)
    for (app, arguments, ranks, source, target), value in indexed.items():
        if target not in modes:
            continue
        reference_key = (app, arguments, ranks, source, reference)
        if reference_key not in indexed:
            continue
        per_workload[(app, arguments, ranks, target)].append(
            value / indexed[reference_key]
        )

    grouped = defaultdict(list)
    for (app, _arguments, ranks, target), estimates in per_workload.items():
        grouped[(app, ranks, target)].append(statistics.fmean(estimates))
    return {key: statistics.fmean(values) for key, values in grouped.items()}


def build_rows(ground_truth, modes, means):
    """Project training means onto the standard prediction-table layout."""
    output = []
    for actual in ground_truth:
        row = {field: actual[field] for field in IDENTITY}
        for mode in modes:
            key = (actual["App"], actual["Ranks"], mode)
            row[mode] = (
                format(means[key], ".17g")
                if actual[mode].strip() and key in means
                else ""
            )
        output.append(row)
    return output


def write_table(path, modes, rows):
    """Write a simulator-compatible wide prediction table."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=[*IDENTITY, *modes], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--training",
        required=True,
        type=Path,
        help="long-form training CSV containing source/target relative runtimes",
    )
    parser.add_argument(
        "--ground-truth",
        required=True,
        type=Path,
        help="wide ground-truth CSV that defines workloads and output modes",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="wide prediction CSV to create"
    )
    parser.add_argument(
        "--reference",
        default="borax",
        help="reference target_machine used for normalization (default: borax)",
    )
    args = parser.parse_args()

    ground_truth, modes = read_ground_truth(args.ground_truth)
    means = training_means(args.training, modes, args.reference)
    rows = build_rows(ground_truth, modes, means)
    write_table(args.output, modes, rows)
    populated = sum(bool(row[mode]) for row in rows for mode in modes)
    print(
        f"wrote {len(rows)} workloads and {populated} predictions to {args.output}"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}")
