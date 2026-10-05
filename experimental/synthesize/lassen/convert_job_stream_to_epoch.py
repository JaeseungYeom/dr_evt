#!/usr/bin/env python3
"""Extract pbatch jobs from CSM allocation history and use Unix timestamps.

The input is expected to be in approximately increasing begin_time order, as
final_csm_allocation_history_hashed.csv is.  That ordering lets this script
distinguish the two occurrences of 01:xx during the autumn DST transition.
"""

import argparse
import csv
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


OUTPUT_COLUMNS = (
    "submit_time",
    "num_nodes",
    "begin_time",
    "end_time",
    "time_limit",
    "exit_status",
)
SIMULATION_COLUMNS = (
    "submit_time",
    "num_nodes",
    "duration",
    "time_limit",
    "exit_status",
)
SOURCE_COLUMNS = (
    "job_submit_time",
    "num_nodes",
    "begin_time",
    "end_time",
    "time_limit",
    "exit_status",
    "queue",
)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def epoch_us(aware_datetime):
    delta = aware_datetime.astimezone(timezone.utc) - EPOCH
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def local_candidates(value, zone):
    """Return valid epoch-microsecond interpretations of a local timestamp."""
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        raise ValueError("timestamps must not contain a UTC offset")

    candidates = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        # The round trip rejects nonexistent times in the spring DST gap.
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(epoch_us(aware))
    if not candidates:
        raise ValueError(f"nonexistent local time: {value}")
    return sorted(candidates)


def format_epoch(microseconds):
    seconds, fraction = divmod(microseconds, 1_000_000)
    return str(seconds) if fraction == 0 else f"{seconds}.{fraction:06d}".rstrip("0")


def resolve_row(row, zone, previous_begin, order_slack_us):
    submit_options = local_candidates(row["submit_time"], zone)
    begin_options = local_candidates(row["begin_time"], zone)
    end_options = local_candidates(row["end_time"], zone)

    # During a repeated hour, prefer the earliest begin-time interpretation
    # that does not substantially move backward in this approximately ordered
    # stream.  A reset such as 01:59 -> 01:05 therefore selects fold=1.
    if previous_begin is not None:
        ordered = [b for b in begin_options if b >= previous_begin - order_slack_us]
        begin = min(ordered) if ordered else min(begin_options)
    else:
        begin = min(begin_options)

    # Pick interpretations adjacent to begin.  This minimizes queue/runtime
    # duration while enforcing the required strict ordering.
    choices = [
        (s, begin, e)
        for s, e in product(submit_options, end_options)
        if s < begin < e
    ]
    if not choices:
        raise ValueError(
            "cannot resolve submit_time < begin_time < end_time: "
            f"{row['submit_time']}, {row['begin_time']}, {row['end_time']}"
        )
    submit, begin, end = min(
        choices, key=lambda values: (values[1] - values[0], values[2] - values[1])
    )
    return submit, begin, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_csv",
        help="CSM allocation history CSV, e.g. final_csm_allocation_history_hashed.csv",
    )
    parser.add_argument("output_csv", help="six-column pbatch job-stream CSV")
    parser.add_argument(
        "--simulation-output",
        metavar="CSV",
        help="also write a scheduling-simulation CSV with duration instead of begin/end",
    )
    parser.add_argument("--timezone", default="America/Los_Angeles")
    parser.add_argument(
        "--order-slack-seconds",
        type=float,
        default=60.0,
        help="small begin-time regressions tolerated before detecting a DST reset (default: 60)",
    )
    args = parser.parse_args()

    try:
        zone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as exc:
        parser.error(str(exc))

    previous_begin = None
    rows_written = 0
    input_path = Path(args.input_csv).resolve()
    output_path = Path(args.output_csv).resolve()
    simulation_path = (
        Path(args.simulation_output).resolve() if args.simulation_output else None
    )
    if output_path == input_path or simulation_path == input_path:
        parser.error("output files must not overwrite the input CSV")
    if simulation_path == output_path:
        parser.error("output_csv and --simulation-output must be different files")

    with ExitStack() as stack:
        source = stack.enter_context(
            open(args.input_csv, newline="", encoding="utf-8")
        )
        destination = stack.enter_context(
            open(args.output_csv, "w", newline="", encoding="utf-8")
        )
        simulation_destination = None
        if args.simulation_output:
            simulation_destination = stack.enter_context(
                open(args.simulation_output, "w", newline="", encoding="utf-8")
            )

        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            parser.error("input CSV has no header")
        missing = set(SOURCE_COLUMNS) - set(reader.fieldnames)
        if missing:
            parser.error("missing columns: " + ", ".join(sorted(missing)))

        writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        simulation_writer = None
        if simulation_destination is not None:
            simulation_writer = csv.DictWriter(
                simulation_destination, fieldnames=SIMULATION_COLUMNS
            )
            simulation_writer.writeheader()
        for line_number, source_row in enumerate(reader, 2):
            # This is the CSV-aware equivalent of the original awk filter and
            # field selection.  csv.DictReader also removes surrounding quotes.
            if source_row["queue"] != "pbatch":
                continue
            row = {
                "submit_time": source_row["job_submit_time"],
                "num_nodes": source_row["num_nodes"],
                "begin_time": source_row["begin_time"],
                "end_time": source_row["end_time"],
                "time_limit": source_row["time_limit"],
                "exit_status": source_row["exit_status"],
            }
            try:
                submit, begin, end = resolve_row(
                    row,
                    zone,
                    previous_begin,
                    int(args.order_slack_seconds * 1_000_000),
                )
            except (KeyError, ValueError) as exc:
                raise SystemExit(f"{args.input_csv}:{line_number}: {exc}") from exc

            row["submit_time"] = format_epoch(submit)
            row["begin_time"] = format_epoch(begin)
            row["end_time"] = format_epoch(end)
            writer.writerow(row)
            if simulation_writer is not None:
                simulation_writer.writerow(
                    {
                        "submit_time": row["submit_time"],
                        "num_nodes": row["num_nodes"],
                        "duration": format_epoch(end - begin),
                        "time_limit": row["time_limit"],
                        "exit_status": row["exit_status"],
                    }
                )
            previous_begin = begin
            rows_written += 1

    print(f"wrote {rows_written} rows to {args.output_csv}", file=sys.stderr)
    if args.simulation_output:
        print(
            f"wrote {rows_written} rows to {args.simulation_output}", file=sys.stderr
        )


if __name__ == "__main__":
    main()
