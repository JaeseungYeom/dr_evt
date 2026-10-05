#!/usr/bin/env python3
"""Generate RAJAPerf and application-average prediction baselines."""

import argparse
import csv
import math
import pathlib
import statistics


IDENTITY = ("App", "Args", "Ranks")


def positive(text, context):
    """Parse a positive finite timing or speedup."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{context} must be positive and finite")
    return value


def read_ground_truth(path):
    """Read the ground-truth table and return rows plus mode columns."""
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(IDENTITY).issubset(reader.fieldnames):
            raise ValueError(f"{path} must contain {', '.join(IDENTITY)}")
        modes = [name for name in reader.fieldnames if name not in IDENTITY]
        rows = list(reader)
    if not rows or not modes:
        raise ValueError(f"{path} has no workload or performance data")
    return rows, modes


def rajaperf_speedups(path, modes, reference="borax"):
    """Average per-kernel timing ratios against the reference machine."""
    with path.open(newline="") as stream:
        records = list(csv.reader(stream))
    if len(records) < 2 or records[0][0].strip().lower() != "machine":
        raise ValueError(f"{path} must start with a machine column")
    timings = {row[0].strip(): row[1:] for row in records[1:] if row}
    if reference not in timings:
        raise ValueError(f"{path} has no reference row {reference!r}")
    machine_for_mode = {
        mode: mode[:-4] if mode.endswith("-gpu") else mode for mode in modes
    }
    result = {}
    reference_values = timings[reference]
    for mode, machine in machine_for_mode.items():
        if machine not in timings:
            raise ValueError(f"{path} has no row for mode {mode!r} ({machine!r})")
        ratios = []
        for index, (base, target) in enumerate(
            zip(reference_values, timings[machine], strict=True), start=2
        ):
            if not base.strip() or not target.strip():
                continue
            ratios.append(
                positive(base, f"{path}: reference column {index}")
                / positive(target, f"{path}: {machine} column {index}")
            )
        if not ratios:
            raise ValueError(f"{path}: no shared kernels for {reference} and {machine}")
        result[mode] = statistics.fmean(ratios)
    return result


def rajaperf_rows(ground_truth, modes, speedups):
    """Assign one machine-level RAJAPerf prediction to every system mode."""
    rows = []
    for actual in ground_truth:
        row = {name: actual[name] for name in IDENTITY}
        row.update({mode: format(speedups[mode], ".17g") for mode in modes})
        rows.append(row)
    return rows


def application_average_rows(ground_truth, modes):
    """Predict each mode by its mean for the same application and rank count."""
    groups = {}
    for row_number, row in enumerate(ground_truth, start=2):
        for mode in modes:
            if row[mode].strip():
                key = (row["App"], row["Ranks"], mode)
                groups.setdefault(key, []).append(
                    positive(row[mode], f"ground truth row {row_number} {mode}")
                )
    means = {key: statistics.fmean(values) for key, values in groups.items()}
    output = []
    for actual in ground_truth:
        row = {name: actual[name] for name in IDENTITY}
        for mode in modes:
            key = (actual["App"], actual["Ranks"], mode)
            row[mode] = (
                format(means[key], ".17g") if actual[mode].strip() else ""
            )
        output.append(row)
    return output


def write_table(path, modes, rows):
    """Write one prediction table."""
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*IDENTITY, *modes])
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=pathlib.Path)
    parser.add_argument("--machine-rep", required=True, type=pathlib.Path)
    parser.add_argument("--rajaperf-output", required=True, type=pathlib.Path)
    parser.add_argument("--app-avg-output", required=True, type=pathlib.Path)
    parser.add_argument("--reference", default="borax")
    args = parser.parse_args()

    ground_truth, modes = read_ground_truth(args.ground_truth)
    speedups = rajaperf_speedups(args.machine_rep, modes, args.reference)
    write_table(
        args.rajaperf_output, modes, rajaperf_rows(ground_truth, modes, speedups)
    )
    write_table(
        args.app_avg_output, modes, application_average_rows(ground_truth, modes)
    )
    print(f"RAJAPerf mean speedups relative to {args.reference}:")
    for mode in modes:
        print(f"  {mode}: {speedups[mode]:.6g}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}")
