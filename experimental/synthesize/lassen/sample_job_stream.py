#!/usr/bin/env python3
"""Sample a contiguous scheduling workload from a historical job trace.

The input must contain these columns:

    submit_time,num_nodes,time_limit,exit_status

Runtime may be supplied as ``duration`` or derived from ``begin_time`` and
``end_time``.

The output contains:

    submit_time,num_nodes,time_limit,duration

By default, the four output values remain together in a sampled contiguous
historical window. See ``sample_jobs`` for the rationale.
"""

import argparse
import csv
import random
import sys
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path


REQUIRED_COLUMNS = {
    "submit_time",
    "num_nodes",
    "time_limit",
}
OUTPUT_COLUMNS = ("submit_time", "num_nodes", "time_limit", "duration")


def decimal_value(text, field, line_number):
    """Parse a finite numeric field without losing fractional precision."""
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"line {line_number}: invalid {field}: {text!r}") from exc
    if not value.is_finite():
        raise ValueError(f"line {line_number}: non-finite {field}: {text!r}")
    return value


def read_eligible_jobs(
    path, minimum_duration, successful_only, maximum_time_limit=None
):
    """Read, normalize/filter jobs, and sort them by submit time."""
    eligible = []
    with open(path, newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError("input CSV has no header")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if successful_only and "exit_status" not in reader.fieldnames:
            missing.add("exit_status")
        has_duration = "duration" in reader.fieldnames
        if not has_duration:
            missing.update({"begin_time", "end_time"} - set(reader.fieldnames))
        if missing:
            raise ValueError("missing columns: " + ", ".join(sorted(missing)))

        for line_number, row in enumerate(reader, 2):
            if has_duration:
                duration_text = row["duration"]
                duration = decimal_value(duration_text, "duration", line_number)
            else:
                begin_time = decimal_value(
                    row["begin_time"], "begin_time", line_number
                )
                end_time = decimal_value(row["end_time"], "end_time", line_number)
                duration = end_time - begin_time
                duration_text = str(duration)
            if duration <= 0:
                raise ValueError(
                    f"line {line_number}: duration must be greater than zero"
                )
            if successful_only and row["exit_status"].strip() != "0":
                continue

            time_limit = decimal_value(
                row["time_limit"], "time_limit", line_number
            )
            if time_limit <= 0:
                raise ValueError(
                    f"line {line_number}: time_limit must be greater than zero"
                )
            time_limit_text = row["time_limit"]
            if maximum_time_limit is not None and time_limit > maximum_time_limit:
                time_limit = maximum_time_limit
                time_limit_text = str(maximum_time_limit)

            # Apply a platform cap to both fields first. Otherwise preserve
            # observed runtime and extend an insufficient submitted limit to
            # ceil(duration), matching convert_completed_trace_to_simulation.sh.
            if maximum_time_limit is not None and duration > maximum_time_limit:
                duration = maximum_time_limit
                duration_text = str(maximum_time_limit)
            if duration > time_limit:
                time_limit = duration.to_integral_value(rounding=ROUND_CEILING)
                time_limit_text = str(time_limit)

            # "Ignore jobs shorter than X" means a job of exactly X seconds
            # remains eligible. Use the normalized duration here so every
            # emitted job still satisfies the requested minimum.
            if duration < minimum_duration:
                continue

            eligible.append(
                {
                    "submit_time": row["submit_time"],
                    "submit_time_decimal": decimal_value(
                        row["submit_time"], "submit_time", line_number
                    ),
                    "num_nodes": row["num_nodes"],
                    "duration": duration_text,
                    "duration_decimal": duration,
                    "time_limit": time_limit_text,
                }
            )
    # A general trace need not already be ordered.  Sorting makes a consecutive
    # slice below represent a real interval of the historical arrival stream.
    # Python's stable sort retains source order for simultaneous submissions.
    eligible.sort(key=lambda job: job["submit_time_decimal"])
    return eligible


def sample_jobs(eligible, count, rng, with_replacement=False):
    """Select a historical workload window without mixing workload regimes.

    1. Choose one uniformly random window of ``count`` consecutive eligible
       jobs from the time-sorted trace.

    2. Retain each job's submit time, node count, duration, and time limit
       together. These values evolve together over a trace; sampling any one
       of them globally can combine a high-rate interval with an unrelated
       workload regime and create an offered load that never occurred.

    With replacement, complete job records are resampled within the selected
    window and assigned to its submit-time sequence. The default leaves the
    selected historical sequence intact.
    """
    if count <= 0:
        raise ValueError("number of jobs must be greater than zero")
    if count > len(eligible):
        raise ValueError(
            f"requested {count} jobs, but only {len(eligible)} are eligible"
        )

    # Stage 1: the window contains consecutive *eligible* jobs in submit-time
    # order. If filtering removes rows, those gaps are simply skipped.
    start = rng.randrange(len(eligible) - count + 1)
    window = eligible[start : start + count]
    if with_replacement:
        records = [rng.choice(window) for _ in range(count)]
    else:
        records = window

    sampled = []
    for source, record in zip(window, records):
        sampled.append(
            {
                "submit_time": source["submit_time"],
                "num_nodes": record["num_nodes"],
                "time_limit": record["time_limit"],
                "duration": record["duration"],
            }
        )
    return sampled, start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", help="historical scheduling trace CSV")
    parser.add_argument("output_csv", help="sampled trace to create")
    parser.add_argument("num_jobs", type=int, help="number of jobs to sample")
    parser.add_argument(
        "--min-duration",
        default="0",
        metavar="SECONDS",
        help="ignore jobs shorter than this duration (default: 0)",
    )
    parser.add_argument(
        "--max-time-limit",
        metavar="SECONDS",
        help=(
            "platform maximum time limit; cap larger limits and any longer "
            "durations to this value (default: disabled)"
        ),
    )
    parser.add_argument(
        "--successful-only",
        action="store_true",
        help="use only jobs whose exit_status is 0",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="random seed for exactly reproducible output",
    )
    parser.add_argument(
        "--with-replacement",
        action="store_true",
        help="resample complete job records within the selected window",
    )
    args = parser.parse_args()

    try:
        minimum_duration = Decimal(args.min_duration)
    except InvalidOperation as exc:
        parser.error(f"invalid --min-duration: {args.min_duration!r}")
    if not minimum_duration.is_finite() or minimum_duration < 0:
        parser.error("--min-duration must be a finite, nonnegative number")
    maximum_time_limit = None
    if args.max_time_limit is not None:
        try:
            maximum_time_limit = Decimal(args.max_time_limit)
        except InvalidOperation:
            parser.error(f"invalid --max-time-limit: {args.max_time_limit!r}")
        if not maximum_time_limit.is_finite() or maximum_time_limit <= 0:
            parser.error("--max-time-limit must be a finite, positive number")
    if args.num_jobs <= 0:
        parser.error("num_jobs must be greater than zero")

    input_path = Path(args.input_csv).resolve()
    output_path = Path(args.output_csv).resolve()
    if input_path == output_path:
        parser.error("output_csv must not overwrite input_csv")

    try:
        eligible = read_eligible_jobs(
            args.input_csv,
            minimum_duration,
            args.successful_only,
            maximum_time_limit,
        )
        sampled, window_start = sample_jobs(
            eligible,
            args.num_jobs,
            random.Random(args.seed),
            args.with_replacement,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    with open(args.output_csv, "w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=OUTPUT_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(sampled)

    seed_text = "system randomness" if args.seed is None else str(args.seed)
    print(
        f"wrote {len(sampled)} jobs to {args.output_csv}; "
        f"eligible={len(eligible)}, submit-window-start={window_start}, seed={seed_text}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
