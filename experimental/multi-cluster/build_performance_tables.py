#!/usr/bin/env python3
"""Build separate ground-truth and prediction performance tables."""

import argparse
import csv
import math
import pathlib
import sys
from collections import defaultdict


def normalized_identity(row, ranks_field):
    """Return the case- and whitespace-normalized workload identity."""
    return (
        row["app"].strip().lower(),
        "".join(row["args"].split()).lower(),
        int(row[ranks_field]),
    )


def csv_reader(path):
    """Open a CSV and normalize an optional ``#`` on its first header."""
    stream = path.open(newline="")
    reader = csv.DictReader(stream)
    if reader.fieldnames and reader.fieldnames[0].startswith("#"):
        reader.fieldnames[0] = reader.fieldnames[0][1:]
    return stream, reader


def read_system_modes(path):
    """Return prediction columns and measurement-machine mappings."""
    stream, reader = csv_reader(path)
    with stream:
        if not reader.fieldnames or not {"machine", "GPU"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have machine and GPU columns")
        modes = {}
        for row in reader:
            machine = row["machine"].strip()
            if row["GPU"] == "CPU-only":
                modes[machine] = machine
            elif row["GPU"] == "GPU-enabled":
                modes[f"{machine}-cpu"] = f"{machine}-cpu"
                modes[f"{machine}-gpu"] = machine
            else:
                raise ValueError(f"{path}: invalid GPU value {row['GPU']!r}")
    return modes


def read_requirements(path):
    """Return normalized application compatibility requirements."""
    stream, reader = csv_reader(path)
    with stream:
        if not reader.fieldnames or not {"app", "sys_requirement"}.issubset(
            reader.fieldnames
        ):
            raise ValueError(f"{path} must have app and sys_requirement columns")
        return {
            row["app"].strip().lower(): row["sys_requirement"].strip() for row in reader
        }


def positive_value(path, row_number, column, text):
    """Parse one finite positive numeric CSV value."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{path}: row {row_number}: invalid {column} value")
    return value


def read_measurements(path, column_machines):
    """Index runtimes by normalized workload identity and output column."""
    machine_columns = {machine: column for column, machine in column_machines.items()}
    stream, reader = csv_reader(path)
    measurements = defaultdict(dict)
    with stream:
        required = {"machine", "rank", "app", "args", "actual_run_time"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            columns = ", ".join(sorted(required))
            raise ValueError(f"{path} must have columns: {columns}")
        for row_number, row in enumerate(reader, start=2):
            column = machine_columns.get(row["machine"].strip())
            if column is None:
                continue
            identity = normalized_identity(row, "rank")
            if column in measurements[identity]:
                raise ValueError(
                    f"{path}: duplicate {column} measurement at row {row_number}"
                )
            measurements[identity][column] = positive_value(
                path, row_number, "actual_run_time", row["actual_run_time"]
            )
    return measurements


def read_predictions(path, modes):
    """Group prediction rows and retain performance-column order."""
    stream, reader = csv_reader(path)
    grouped = defaultdict(list)
    with stream:
        required = {"app", "args", "ranks", *modes}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} is missing required workload columns")
        performance_order = [
            column
            for column in reader.fieldnames
            if column not in {"app", "args", "ranks"}
        ]
        mode_order = [column for column in performance_order if column in modes]
        for row_number, row in enumerate(reader, start=2):
            for column in performance_order:
                if row[column].strip():
                    positive_value(path, row_number, column, row[column])
            grouped[normalized_identity(row, "ranks")].append(row)
    return grouped, mode_order, performance_order


def compatible_modes(requirement, modes):
    """Return modes allowed by one application requirement."""
    if requirement == "CPU-only":
        return [mode for mode in modes if not mode.endswith("-gpu")]
    if requirement == "GPU-only":
        return [mode for mode in modes if mode.endswith("-gpu")]
    if requirement == "GPU-portable":
        return list(modes)
    raise ValueError(f"invalid sys_requirement {requirement!r}")


def build_rows(
    prediction_groups,
    measurements,
    requirements,
    mode_order,
    reference_order=None,
):
    """Build aligned ground-truth and prediction rows."""
    reference_order = reference_order or mode_order
    ground_truth_rows = []
    prediction_rows = []
    skipped = 0
    for identity, candidates in prediction_groups.items():
        app = identity[0]
        if app not in requirements:
            continue
        measured = measurements.get(identity, {})
        actual_modes = [
            mode
            for mode in compatible_modes(requirements[app], mode_order)
            if mode in measured
        ]
        if not actual_modes:
            skipped += 1
            continue

        external_references = [
            column
            for column in reference_order
            if column not in mode_order and column in measured
        ]
        reference = external_references[0] if external_references else actual_modes[0]
        reference_runtime = measured[reference]
        prediction_candidates = [row for row in candidates if row[reference].strip()]
        prediction = (
            max(
                prediction_candidates,
                key=lambda row: sum(
                    bool(row[mode].strip()) and mode in measured
                    for mode in mode_order
                ),
            )
            if prediction_candidates
            else None
        )
        identity_values = {
            "App": candidates[0]["app"],
            "Args": candidates[0]["args"],
            "Ranks": candidates[0]["ranks"],
        }
        ground_truth = dict(identity_values)
        predicted = dict(identity_values)
        for mode in mode_order:
            ground_truth[mode] = (
                format(reference_runtime / measured[mode], ".17g")
                if mode in actual_modes
                else ""
            )
            predicted[mode] = (
                format(float(prediction[mode]) / float(prediction[reference]), ".17g")
                if prediction is not None
                and mode in actual_modes
                and prediction[mode].strip()
                else ""
            )
        ground_truth_rows.append(ground_truth)
        prediction_rows.append(predicted)
    return ground_truth_rows, prediction_rows, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=pathlib.Path)
    parser.add_argument("--measurements", required=True, type=pathlib.Path)
    parser.add_argument("--applications", required=True, type=pathlib.Path)
    parser.add_argument("--systems", required=True, type=pathlib.Path)
    parser.add_argument("--ground-truth-output", required=True, type=pathlib.Path)
    parser.add_argument("--prediction-output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    mode_machines = read_system_modes(args.systems)
    requirements = read_requirements(args.applications)
    predictions, mode_order, performance_order = read_predictions(
        args.predictions, mode_machines
    )
    column_machines = {column: column for column in performance_order}
    column_machines.update(mode_machines)
    measurements = read_measurements(args.measurements, column_machines)
    ground_truth_rows, prediction_rows, skipped = build_rows(
        predictions,
        measurements,
        requirements,
        mode_order,
        performance_order,
    )

    fields = ["App", "Args", "Ranks", *mode_order]
    for path, rows in (
        (args.ground_truth_output, ground_truth_rows),
        (args.prediction_output, prediction_rows),
    ):
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(
        f"Wrote {len(ground_truth_rows)} aligned workloads; "
        f"skipped {skipped} without compatible prediction/measurement pairs",
        file=sys.stderr,
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
