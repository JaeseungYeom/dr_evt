#!/usr/bin/env python3
"""Generate a synthetic scheduling trace from a historical job trace.

The input must contain these columns:

    submit_time,num_nodes,duration,time_limit,exit_status

The output contains:

    submit_time,num_nodes,time_limit,duration

The four output values are assembled in three deliberately separate sampling
steps.  See ``generate_jobs`` below for the detailed process and rationale.
"""

import argparse
import csv
import random
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path


REQUIRED_COLUMNS = {
    "submit_time",
    "num_nodes",
    "duration",
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


def duration_bucket(duration):
    """Map (0, 1] to bucket 1, (1, 2] to bucket 2, and so forth."""
    if duration <= 0:
        raise ValueError(f"duration must be greater than zero, got {duration}")
    return int(duration.to_integral_value(rounding=ROUND_CEILING))


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
        if missing:
            raise ValueError("missing columns: " + ", ".join(sorted(missing)))

        for line_number, row in enumerate(reader, 2):
            duration = decimal_value(row["duration"], "duration", line_number)
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

            # A synthetic job cannot run beyond its effective time limit.
            # Normalize the duration before applying the duration filter and
            # before assigning the job to a duration bucket.
            if duration > time_limit:
                duration = time_limit
                duration_text = time_limit_text
            else:
                duration_text = row["duration"]

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


def generate_jobs(eligible, count, rng, with_replacement=False):
    """Construct synthetic jobs using the requested independent sampling.

    1. Choose one uniformly random starting index and copy ``count``
       consecutive submit times from the time-sorted eligible trace.  Keeping
       a contiguous window preserves the historical arrival pattern and
       interarrival gaps.

    2. Independently sample ``count`` historical jobs uniformly, and retain
       each selected job's (num_nodes, duration) pair.  The pair stays intact
       so the observed relationship between job size and runtime is preserved.

    3. Put every eligible historical job into ceil(duration) one-second
       buckets: (0,1] is bucket 1, (1,2] is bucket 2, etc.  For each sampled
       duration, independently choose a time_limit uniformly from historical
       jobs in the same bucket.  Sampling records rather than distinct limit
       values preserves the empirical frequency of repeated time limits.
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
    submit_times = [job["submit_time"] for job in eligible[start : start + count]]

    # Stage 2: sampling is without replacement by default because the request
    # is to pick N eligible historical jobs.  --with-replacement enables
    # bootstrap-style resampling when repeated pairs are desirable.
    if with_replacement:
        pair_samples = [rng.choice(eligible) for _ in range(count)]
    else:
        pair_samples = rng.sample(eligible, count)

    # Build buckets from all eligible jobs, not merely from Stage 2's sample.
    limits_by_bucket = defaultdict(list)
    for job in eligible:
        bucket = duration_bucket(job["duration_decimal"])
        limits_by_bucket[bucket].append(job["time_limit"])

    synthetic = []
    for submit_time, pair in zip(submit_times, pair_samples):
        bucket = duration_bucket(pair["duration_decimal"])
        time_limit_text = rng.choice(limits_by_bucket[bucket])
        time_limit = Decimal(time_limit_text)
        if pair["duration_decimal"] > time_limit:
            duration_text = time_limit_text
        else:
            duration_text = pair["duration"]
        synthetic.append(
            {
                "submit_time": submit_time,
                "num_nodes": pair["num_nodes"],
                "time_limit": time_limit_text,
                "duration": duration_text,
            }
        )
    return synthetic, start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", help="historical scheduling trace CSV")
    parser.add_argument("output_csv", help="synthetic trace to create")
    parser.add_argument("num_jobs", type=int, help="number of synthetic jobs")
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
        help="allow repeated (num_nodes, duration) samples",
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
        synthetic, window_start = generate_jobs(
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
        writer.writerows(synthetic)

    seed_text = "system randomness" if args.seed is None else str(args.seed)
    print(
        f"wrote {len(synthetic)} jobs to {args.output_csv}; "
        f"eligible={len(eligible)}, submit-window-start={window_start}, seed={seed_text}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
