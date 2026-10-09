#!/usr/bin/env python3
r"""Run and summarize the Borax knowledge-transfer scheduling study.

OVERVIEW
========
The script prepares simulator-compatible tables for seven prediction cases,
runs ``mpi_performance_dispatch`` on ten 100,000-job traces, and generates
per-run and aggregate scheduling metrics. No input location is built into the
script; every input and the output directory must be supplied on the command
line.

Prediction cases:

* ``ideal`` uses the normalized ground-truth table as its prediction.
* ``fully_trained`` uses ``--fully-trained``.
* ``knowledge_transfer_1_percent`` uses ``--knowledge-transfer-1``.
* ``knowledge_transfer_3_percent`` uses ``--knowledge-transfer-3``.
* ``knowledge_transfer_5_percent`` uses ``--knowledge-transfer-5``.
* ``app_average_per_machine`` is generated from ground truth. Each prediction
  is the arithmetic mean for the same (application, ranks, execution mode).
* ``sys_bench`` is generated from ``--machine-rep``. For each execution mode,
  the mean Borax-time / target-time ratio across shared benchmark positions is
  assigned to every workload.

The optional ``app_average_5_percent`` case uses ``--app-average-5-percent``
and is run only when explicitly selected with ``--case``. It is not part of
the seven-case default campaign.

EXAMPLE
=======
Run the complete default matrix (2 dispatch policies x 2 wall-time policies x
7 prediction cases x 10 traces = 280 simulations):

    python run_kt_scheduling_study.py \
      --ground-truth /data/ground_truth_borax.csv \
      --fully-trained /data/fully_trained.csv \
      --knowledge-transfer-1 /data/knowledge_transfer_1-percent.csv \
      --knowledge-transfer-3 /data/knowledge_transfer_3-percent.csv \
      --knowledge-transfer-5 /data/knowledge_transfer_5-percent.csv \
      --machine-rep /data/machine_rep.txt \
      --applications /data/apps.csv \
      --systems /data/machines.csv \
      --jobs-glob '/data/traces/synthetic_jobs_*.csv' \
      --executable /install/bin/mpi_performance_dispatch \
      --output-dir /results/kt-scheduling

Add ``--prepare-only`` to validate and create the seven input tables without
running MPI. Add ``--aggregate-only`` to rebuild summaries from completed
runs. Repeat ``--dispatch-policy`` or ``--wall-time-policy`` to select a subset
of their respective defaults. For example, selecting one of each produces 70
runs instead of 280.

The optional ``--dispatch-policy WaitTimeOnly`` baseline ignores the supplied
relative-performance predictions, uses ground truth only for realized runtime
and feasibility, and chooses the system with the smallest estimated wait. It
runs one ``wait_time_only`` case per trace instead of redundantly repeating all
seven prediction cases. Select one ``--wall-time-policy`` to run exactly ten
WaitTimeOnly simulations; omitting it runs both wall-time policies (twenty
runs).

INPUT FORMATS
=============
Ground truth (``--ground-truth``)
---------------------------------
CSV with case-insensitive identity headers ``App,Args,Ranks`` followed by all
execution-mode columns implied by ``--systems``. Values are positive finite
relative speedups; an empty cell means that mode has no measurement. Extra
machine columns are ignored. Example:

    app,args,ranks,dane,mammoth,matrix-cpu,matrix-gpu
    amg,-problem1-p442-n12812864,32,1.0085,1.0726,1.1237,

Model predictions
-----------------
``--fully-trained`` and the three ``--knowledge-transfer-*`` inputs use the
the required execution-mode columns. They may include metadata such as
``source_machine`` and extra machine columns; those columns are ignored. Rows
may be in a different order or omitted when no prediction is available, but a
prediction row not present in ground truth is rejected. Missing or empty
predictions invoke per-job wait-only fallback when no feasible prediction is
available. Non-finite or non-positive predictions are made unavailable and recorded in
``input_tables/rejected_predictions.csv``.

Applications (``--applications``)
---------------------------------
CSV with ``app`` (or ``#app``) and ``sys_requirement`` columns. Requirements
are ``CPU-only``, ``GPU-only``, or ``GPU-portable``. This table is also the
application allowlist used by the dispatcher.

Systems (``--systems``)
-----------------------
CSV with ``machine`` (or ``#machine``), ``size``, and ``GPU`` columns. ``GPU``
is ``CPU-only`` or ``GPU-enabled``. A CPU-only row named ``dane`` requires the
``dane`` performance column. A GPU-enabled row named ``matrix`` requires both
``matrix-cpu`` and ``matrix-gpu``.

System benchmark (``--machine-rep``)
------------------------------------
CSV whose first column is ``machine`` and whose remaining columns are matched
benchmark timings. It must contain the ``borax`` reference row and rows for all
configured modes (GPU modes use the unsuffixed machine row). Blank benchmark
cells are allowed, but each target must share at least one positive finite
timing with Borax.

Job traces (``--jobs-glob``)
----------------------------
A quoted glob that must resolve to exactly ten CSV files. Each must contain
the number of jobs selected by ``--jobs-per-trace`` (default: 100,000) plus a
header. Job columns are those accepted by
``mpi_performance_dispatch``: ``submit_time``, ``num_nodes``, ``time_limit``,
and ``duration``; ``job_id`` and queue columns are optional.

Executable and launcher
-----------------------
``--executable`` is the installed ``mpi_performance_dispatch`` executable and
is required except in prepare-only or aggregate-only mode. ``--launcher``
defaults to ``srun`` and ``--ranks`` defaults to 6 (one controller plus five
systems). ``--working-directory`` defaults to the caller's current directory.

OUTPUTS
=======
The script creates the following beneath ``--output-dir``:

``input_tables/ground_truth.csv``
    Normalized actual values used by every simulation.
``input_tables/{fully_trained,knowledge_transfer_1_percent,
knowledge_transfer_3_percent,knowledge_transfer_5_percent}.csv``
    Normalized model prediction tables.
``input_tables/app_average_per_machine.csv``
    Persisted application-average baseline, suitable for actual-vs-predicted
    plotting against ``input_tables/ground_truth.csv``.
``input_tables/sys_bench.csv``
    Persisted system-benchmark baseline, also suitable for plotting.
``input_tables/rejected_predictions.csv``
    Source row and value for each invalid prediction omitted during preparation.
``<dispatch>.<wall-time>.<case>.run_NN.dispatch.csv``
    Per-job scheduling decisions for one simulation.
``<dispatch>.<wall-time>.<case>.run_NN.log``
    Simulator output containing the overall metrics.
``<dispatch>.<wall-time>.<case>.run_NN.complete``
    Completion marker. A run is reused only when its marker, dispatch rows,
    reported job totals, and study fingerprint validate.
``metrics_per_run.csv`` and ``summary.csv``
    One row per run with jobs, dropped jobs, average turnaround time, average
    bounded slowdown, average run time, and average speedup.
``summary_aggregate.csv`` and ``summary.md``
    Mean and population standard deviation over the ten traces for each case.
``summary.png`` and ``summary.pdf``
    Four-panel aggregate metric visualization.

The generated baseline tables can also be passed directly to
``plot_relative_performance.py`` as ``--prediction``, with the generated
``input_tables/ground_truth.csv`` supplied as ``--ground-truth``.
"""

import argparse
import csv
import glob
import hashlib
import math
import pathlib
import sys

import build_prediction_baselines as baselines
import run_prediction_study as study


IDENTITY = ("App", "Args", "Ranks")
DEFAULT_CASES = (
    "ideal",
    "fully_trained",
    "knowledge_transfer_1_percent",
    "knowledge_transfer_3_percent",
    "knowledge_transfer_5_percent",
    "app_average_per_machine",
    "sys_bench",
)
SUPPORTED_CASES = (*DEFAULT_CASES, "app_average_5_percent")


def configured_modes(path):
    """Return execution-mode columns implied by a systems CSV."""
    modes = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames:
            reader.fieldnames[0] = reader.fieldnames[0].lstrip("#")
        required = {"machine", "GPU"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} must contain machine and GPU columns")
        for row_number, row in enumerate(reader, start=2):
            machine = row["machine"].strip()
            machine_type = row["GPU"].strip()
            if not machine:
                raise ValueError(f"{path}:{row_number}: empty machine")
            if machine_type == "CPU-only":
                modes.append(machine)
            elif machine_type == "GPU-enabled":
                modes.extend((f"{machine}-cpu", f"{machine}-gpu"))
            else:
                raise ValueError(
                    f"{path}:{row_number}: unsupported machine type {machine_type!r}"
                )
    if len(modes) != len(set(modes)):
        raise ValueError(f"{path} defines duplicate execution modes")
    return modes


def identity_fields(fieldnames, path):
    """Resolve case-insensitive workload identity columns."""
    by_lower = {field.lower(): field for field in fieldnames or ()}
    missing = [field for field in ("app", "args", "ranks") if field not in by_lower]
    if missing:
        raise ValueError(f"{path} is missing identity columns: {', '.join(missing)}")
    return tuple(by_lower[field] for field in ("app", "args", "ranks"))


def workload_key(row, fields, path, row_number):
    """Return a normalized workload key and validate the rank count."""
    app, arguments, ranks = (row[field].strip() for field in fields)
    try:
        ranks = str(int(ranks))
    except ValueError as error:
        raise ValueError(f"{path}:{row_number}: invalid ranks {ranks!r}") from error
    return app.lower(), "".join(arguments.split()).lower(), ranks


def positive_or_blank(text, context, reject_nonpositive=False):
    """Validate a performance cell, optionally turning non-positive values blank."""
    text = text.strip()
    if not text:
        return "", False
    try:
        value = float(text)
    except ValueError as error:
        raise ValueError(f"{context}: invalid performance value {text!r}") from error
    if not math.isfinite(value) or value <= 0:
        if reject_nonpositive:
            return "", True
        raise ValueError(f"{context}: performance must be positive and finite")
    return text, False


def load_ground_truth(path, modes):
    """Load and normalize ground truth to the simulator table schema."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        identities = identity_fields(reader.fieldnames, path)
        missing = [mode for mode in modes if mode not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path} is missing modes: {', '.join(missing)}")
        rows = []
        keys = set()
        for row_number, row in enumerate(reader, start=2):
            key = workload_key(row, identities, path, row_number)
            if key in keys:
                raise ValueError(f"{path}:{row_number}: duplicate workload {key!r}")
            keys.add(key)
            output = {
                "App": row[identities[0]].strip(),
                "Args": row[identities[1]].strip(),
                "Ranks": key[2],
            }
            for mode in modes:
                output[mode], _ = positive_or_blank(
                    row[mode], f"{path}:{row_number}:{mode}"
                )
            rows.append(output)
    if not rows:
        raise ValueError(f"{path} contains no workloads")
    return rows


def load_prediction(path, truth_rows, modes, ignore_extra=False):
    """Align one prediction matrix to ground truth and omit invalid predictions."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        identities = identity_fields(reader.fieldnames, path)
        missing = [mode for mode in modes if mode not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path} is missing modes: {', '.join(missing)}")
        indexed = {}
        row_numbers = {}
        for row_number, row in enumerate(reader, start=2):
            key = workload_key(row, identities, path, row_number)
            if key in indexed:
                raise ValueError(f"{path}:{row_number}: duplicate workload {key!r}")
            indexed[key] = row
            row_numbers[key] = row_number

    truth_keys = {
        (row["App"].lower(), "".join(row["Args"].split()).lower(), row["Ranks"])
        for row in truth_rows
    }
    prediction_keys = set(indexed)
    extra_keys = prediction_keys - truth_keys
    if extra_keys and not ignore_extra:
        raise ValueError(
            f"{path}: prediction contains {len(extra_keys)} workload keys "
            "absent from ground truth"
        )

    output_rows = []
    rejected = []
    for truth in truth_rows:
        key = (
            truth["App"].lower(),
            "".join(truth["Args"].split()).lower(),
            truth["Ranks"],
        )
        source = indexed.get(key)
        output = {field: truth[field] for field in IDENTITY}
        for mode in modes:
            if source is None:
                output[mode], was_rejected = "", False
            else:
                output[mode], was_rejected = positive_or_blank(
                    source[mode], f"{path}:{row_numbers[key]}:{mode}", True
                )
            if was_rejected:
                rejected.append(
                    {
                        "prediction": path.name,
                        "row": row_numbers[key],
                        **{field: truth[field] for field in IDENTITY},
                        "mode": mode,
                        "value": source[mode].strip(),
                    }
                )
        output_rows.append(output)
    return output_rows, rejected


def write_table(path, modes, rows):
    """Write a simulator-compatible performance table."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=[*IDENTITY, *modes], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def prepare_tables(args):
    """Create normalized tables for all requested prediction cases."""
    selected = set(args.case or DEFAULT_CASES)

    def requested(case):
        return case in selected

    modes = configured_modes(args.systems)
    truth_rows = load_ground_truth(args.ground_truth_source, modes)
    input_dir = args.output_dir / "input_tables"
    input_dir.mkdir(parents=True, exist_ok=True)
    ground_truth = input_dir / "ground_truth.csv"
    write_table(ground_truth, modes, truth_rows)

    case_paths = [("ideal", ground_truth)] if requested("ideal") else []
    rejected = []
    for case, source in (
        ("fully_trained", args.fully_trained),
        ("knowledge_transfer_1_percent", args.knowledge_transfer_1),
        ("knowledge_transfer_3_percent", args.knowledge_transfer_3),
        ("knowledge_transfer_5_percent", args.knowledge_transfer_5),
    ):
        if not requested(case):
            continue
        if source is None:
            raise ValueError(f"--{case.replace('_', '-')} is required for {case}")
        rows, invalid = load_prediction(
            source,
            truth_rows,
            modes,
            getattr(args, "ignore_extra_predictions", False),
        )
        destination = input_dir / f"{case}.csv"
        write_table(destination, modes, rows)
        case_paths.append((case, destination))
        rejected.extend(invalid)

    if requested("app_average_per_machine"):
        app_average = input_dir / "app_average_per_machine.csv"
        write_table(
            app_average,
            modes,
            baselines.application_average_rows(truth_rows, modes),
        )
        case_paths.append(("app_average_per_machine", app_average))

    if requested("app_average_5_percent"):
        if args.app_average_5_percent is None:
            raise ValueError(
                "--app-average-5-percent is required for app_average_5_percent"
            )
        rows, invalid = load_prediction(
            args.app_average_5_percent, truth_rows, modes
        )
        destination = input_dir / "app_average_5_percent.csv"
        write_table(destination, modes, rows)
        case_paths.append(("app_average_5_percent", destination))
        rejected.extend(invalid)

    if requested("sys_bench"):
        if args.machine_rep is None:
            raise ValueError("--machine-rep is required for sys_bench")
        sys_bench = input_dir / "sys_bench.csv"
        speedups = baselines.rajaperf_speedups(args.machine_rep, modes, "borax")
        write_table(
            sys_bench, modes, baselines.rajaperf_rows(truth_rows, modes, speedups)
        )
        case_paths.append(("sys_bench", sys_bench))

    rejected_path = input_dir / "rejected_predictions.csv"
    rejected_fields = ["prediction", "row", *IDENTITY, "mode", "value"]
    with rejected_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=rejected_fields, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rejected)
    return ground_truth, case_paths, rejected


def study_fingerprint(args, ground_truth, cases):
    """Hash inputs and run settings recorded in completion markers."""
    digest = hashlib.sha256()
    for path in (ground_truth, args.applications, args.systems, *(p for _, p in cases)):
        digest.update(path.read_bytes())
    digest.update(
        repr(
            (
                args.launcher,
                args.ranks,
                args.seed,
                args.max_time_limit,
                args.jobs_per_trace,
            )
        ).encode()
    )
    return f"study_fingerprint={digest.hexdigest()}\n"


def cases_for_dispatch_policy(dispatch_policy, cases):
    """Return prediction cases needed by one dispatch policy."""
    if dispatch_policy == "WaitTimeOnly":
        return [("wait_time_only", cases[0][1])]
    return cases


def validate_run_inputs(args, cases):
    """Validate the executable and ten consistently sized job traces."""
    required = [args.executable, args.applications, args.systems, *(p for _, p in cases)]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(missing))
    traces = [pathlib.Path(name).resolve() for name in sorted(glob.glob(args.jobs_glob))]
    if len(traces) != 10:
        raise ValueError(f"expected exactly 10 job traces, found {len(traces)}")
    for trace in traces:
        with trace.open(encoding="utf-8") as stream:
            jobs = sum(1 for _ in stream) - 1
        if jobs != args.jobs_per_trace:
            raise ValueError(
                f"{trace} contains {jobs} jobs, expected {args.jobs_per_trace}"
            )
    return traces


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--executable", type=pathlib.Path)
    parser.add_argument(
        "--ground-truth",
        dest="ground_truth_source",
        required=True,
        type=pathlib.Path,
    )
    parser.add_argument("--fully-trained", type=pathlib.Path)
    parser.add_argument("--knowledge-transfer-1", type=pathlib.Path)
    parser.add_argument("--knowledge-transfer-3", type=pathlib.Path)
    parser.add_argument("--knowledge-transfer-5", type=pathlib.Path)
    parser.add_argument(
        "--app-average-5-percent",
        type=pathlib.Path,
        help=(
            "five-percent-training application-average table; used only with "
            "--case app_average_5_percent"
        ),
    )
    parser.add_argument(
        "--ignore-extra-predictions",
        action="store_true",
        help=(
            "ignore prediction workloads absent from ground truth instead of "
            "rejecting the input"
        ),
    )
    parser.add_argument("--machine-rep", type=pathlib.Path)
    parser.add_argument(
        "--applications", required=True, type=pathlib.Path
    )
    parser.add_argument("--systems", required=True, type=pathlib.Path)
    parser.add_argument(
        "--jobs-glob",
        required=True,
        help="quoted glob matching exactly ten job-trace CSVs",
    )
    parser.add_argument(
        "--jobs-per-trace",
        type=int,
        default=study.JOBS_PER_TRACE,
        help="required number of jobs in each trace (default: 100000)",
    )
    parser.add_argument(
        "--output-dir", required=True, type=pathlib.Path
    )
    parser.add_argument(
        "--working-directory", type=pathlib.Path, default=pathlib.Path.cwd()
    )
    parser.add_argument("--launcher", nargs="+", default=["srun"])
    parser.add_argument("--ranks", type=int, default=6)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-time-limit", type=float, default=43200.0)
    parser.add_argument(
        "--dispatch-policy",
        action="append",
        choices=study.SUPPORTED_DISPATCH_POLICIES,
    )
    parser.add_argument(
        "--wall-time-policy", action="append", choices=study.WALL_TIME_POLICIES
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=SUPPORTED_CASES,
        help=(
            "prediction case to run; repeat as needed (default: the seven "
            "standard cases; app_average_5_percent is opt-in)"
        ),
    )
    parser.add_argument(
        "--prepare-only", action="store_true", help="prepare inputs without simulations"
    )
    parser.add_argument(
        "--aggregate-only", action="store_true", help="regenerate metrics from completed runs"
    )
    args = parser.parse_args()
    if args.prepare_only and args.aggregate_only:
        parser.error("--prepare-only and --aggregate-only are mutually exclusive")
    if not math.isfinite(args.max_time_limit) or args.max_time_limit <= 0:
        parser.error("--max-time-limit must be finite and positive")
    if args.jobs_per_trace <= 0:
        parser.error("--jobs-per-trace must be greater than zero")

    for name in (
        "ground_truth_source",
        "applications",
        "systems",
        "output_dir",
        "working_directory",
    ):
        setattr(args, name, getattr(args, name).resolve())
    for name in (
        "fully_trained",
        "knowledge_transfer_1",
        "knowledge_transfer_3",
        "knowledge_transfer_5",
        "app_average_5_percent",
        "machine_rep",
    ):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ground_truth, cases, rejected = prepare_tables(args)
    if args.case:
        selected = set(args.case)
        available = {case for case, _ in cases}
        unavailable = selected - available
        if unavailable:
            parser.error(
                "selected cases have no input table: " + ", ".join(sorted(unavailable))
            )
        cases = [entry for entry in cases if entry[0] in selected]
    print(f"prepared {len(cases)} prediction cases in {args.output_dir / 'input_tables'}")
    if rejected:
        print(
            f"excluded {len(rejected)} non-positive predictions; see "
            f"{args.output_dir / 'input_tables/rejected_predictions.csv'}",
            file=sys.stderr,
        )
    if args.prepare_only:
        return

    dispatch_policies = args.dispatch_policy or list(study.DISPATCH_POLICIES)
    wall_time_policies = args.wall_time_policy or list(study.WALL_TIME_POLICIES)
    args.ground_truth = ground_truth
    args.marker_metadata = study_fingerprint(args, ground_truth, cases)

    if args.aggregate_only:
        records = []
        for dispatch_policy in dispatch_policies:
            records.extend(
                study.load_completed(
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
            parser.error("--executable is required unless --prepare-only or --aggregate-only is used")
        args.executable = args.executable.resolve()
        traces = validate_run_inputs(args, cases)
        records = []
        for dispatch_policy in dispatch_policies:
            for wall_time_policy in wall_time_policies:
                for case, prediction in cases_for_dispatch_policy(
                    dispatch_policy, cases
                ):
                    for run_number, trace in enumerate(traces, start=1):
                        record = study.run_case(
                            args,
                            args.working_directory,
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

    summary = study.write_results(args.output_dir, records, missing_model_note=False)
    study.plot_results(args.output_dir, summary)
    print(f"wrote metrics and plots to {args.output_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
