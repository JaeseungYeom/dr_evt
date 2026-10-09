#!/usr/bin/env python3
"""Generate a statistical scheduling trace from a historical job trace.

The generated trace preserves a consecutive historical submission-time
sequence. Node count and duration are sampled together, and time limits are
sampled conditionally on duration. The sampling population can be the whole
eligible trace (global) or the selected arrival window (local).
"""

import argparse
import csv
import random
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sample_job_stream import OUTPUT_COLUMNS, read_eligible_jobs


def duration_bucket(duration):
    """Map (0, 1] to bucket 1, (1, 2] to bucket 2, and so forth."""
    if duration <= 0:
        raise ValueError(f"duration must be greater than zero, got {duration}")
    return int(duration.to_integral_value(rounding=ROUND_CEILING))


def submission_regime(submit_time, timezone, work_hours_start, work_hours_end):
    """Classify an epoch submission as work hours, off hours, or weekend."""
    local_time = datetime.fromtimestamp(float(submit_time), timezone)
    if local_time.weekday() >= 5:
        return "weekend"
    if work_hours_start <= local_time.hour < work_hours_end:
        return "work_hours"
    return "off_hours"


def generate_jobs(
    eligible,
    count,
    rng,
    sampling_scope="global",
    with_replacement=False,
    time_binning="none",
    timezone=None,
    work_hours_start=9,
    work_hours_end=17,
):
    """Generate jobs from empirical arrival, duration, and limit distributions.

    A contiguous historical window supplies the submission-time sequence.
    ``(num_nodes, duration)`` pairs are sampled together from either all
    eligible jobs or that local window. With work-cycle binning enabled, pairs
    are additionally conditioned on whether submission occurs during weekday
    work hours, weekday off hours, or a weekend. Time limits are sampled from
    the same regime, conditional on the selected duration's one-second bucket.
    """
    if count <= 0:
        raise ValueError("number of jobs must be greater than zero")
    if count > len(eligible):
        raise ValueError(
            f"requested {count} jobs, but only {len(eligible)} are eligible"
        )
    if sampling_scope not in {"global", "local"}:
        raise ValueError(
            "sampling_scope must be either 'global' or 'local', got "
            f"{sampling_scope!r}"
        )
    if time_binning not in {"none", "work-cycle"}:
        raise ValueError(
            "time_binning must be either 'none' or 'work-cycle', got "
            f"{time_binning!r}"
        )
    if not 0 <= work_hours_start < work_hours_end <= 24:
        raise ValueError(
            "work-hour boundaries must satisfy "
            "0 <= work_hours_start < work_hours_end <= 24"
        )
    if timezone is None:
        timezone = ZoneInfo("America/Los_Angeles")

    start = rng.randrange(len(eligible) - count + 1)
    arrival_window = eligible[start : start + count]
    population = eligible if sampling_scope == "global" else arrival_window

    def regime(job):
        if time_binning == "none":
            return "all"
        return submission_regime(
            job["submit_time_decimal"],
            timezone,
            work_hours_start,
            work_hours_end,
        )

    pairs_by_regime = defaultdict(list)
    for job in population:
        pairs_by_regime[regime(job)].append(job)

    if with_replacement:
        pair_samples = [
            rng.choice(pairs_by_regime[regime(arrival)])
            for arrival in arrival_window
        ]
    else:
        requested_by_regime = defaultdict(int)
        for arrival in arrival_window:
            requested_by_regime[regime(arrival)] += 1
        sampled_by_regime = {
            key: iter(rng.sample(pairs_by_regime[key], requested))
            for key, requested in requested_by_regime.items()
        }
        pair_samples = [
            next(sampled_by_regime[regime(arrival)])
            for arrival in arrival_window
        ]

    limits_by_bucket = defaultdict(list)
    for job in population:
        key = (regime(job), duration_bucket(job["duration_decimal"]))
        limits_by_bucket[key].append(job["time_limit"])

    synthetic = []
    for arrival, pair_sample in zip(arrival_window, pair_samples):
        duration = pair_sample["duration_decimal"]
        limit_key = (regime(arrival), duration_bucket(duration))
        limit_text = rng.choice(limits_by_bucket[limit_key])
        limit = Decimal(limit_text)
        if duration > limit:
            limit = duration.to_integral_value(rounding=ROUND_CEILING)
            limit_text = str(limit)
        synthetic.append(
            {
                "submit_time": arrival["submit_time"],
                "num_nodes": pair_sample["num_nodes"],
                "time_limit": limit_text,
                "duration": pair_sample["duration"],
            }
        )

    return synthetic, start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", help="historical scheduling trace CSV")
    parser.add_argument("output_csv", help="synthetic trace to create")
    parser.add_argument("num_jobs", type=int, help="number of synthetic jobs")
    parser.add_argument(
        "--sampling-scope",
        choices=("global", "local"),
        default="global",
        help=(
            "population for node/duration-pair and conditional time-limit sampling "
            "(default: global)"
        ),
    )
    parser.add_argument(
        "--time-binning",
        choices=("none", "work-cycle"),
        default="none",
        help=(
            "optionally separate sampling into work-hours, off-hours, and "
            "weekend populations (default: none)"
        ),
    )
    parser.add_argument(
        "--timezone",
        default="America/Los_Angeles",
        help="timezone used by work-cycle binning (default: America/Los_Angeles)",
    )
    parser.add_argument(
        "--work-hours-start",
        type=int,
        default=9,
        metavar="HOUR",
        help="inclusive weekday work-hour start, 0-23 (default: 9)",
    )
    parser.add_argument(
        "--work-hours-end",
        type=int,
        default=17,
        metavar="HOUR",
        help="exclusive weekday work-hour end, 1-24 (default: 17)",
    )
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
    if not 0 <= args.work_hours_start < args.work_hours_end <= 24:
        parser.error(
            "work-hour boundaries must satisfy "
            "0 <= --work-hours-start < --work-hours-end <= 24"
        )
    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error(f"unknown --timezone: {args.timezone!r}")

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
            args.sampling_scope,
            args.with_replacement,
            args.time_binning,
            timezone,
            args.work_hours_start,
            args.work_hours_end,
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
        f"eligible={len(eligible)}, submit-window-start={window_start}, "
        f"sampling-scope={args.sampling_scope}, "
        f"time-binning={args.time_binning}, seed={seed_text}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
