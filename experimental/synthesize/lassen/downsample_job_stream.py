#!/usr/bin/env python3
"""Retain one job from each fixed-size block of job-trace rows.

Each input may use any CSV schema: its header and retained rows are copied
unchanged to a same-named file beneath ``--output-dir``.  Consequently,
downsampling reduces the offered load over the original time interval instead
of compressing the remaining jobs into a shorter interval.

Example::

    python downsample_job_stream.py traces/100000/*.csv \
      --output-dir traces/25000 --stride 4
"""

import argparse
import csv
import itertools
import sys
from pathlib import Path


def downsample(source, destination, stride=4, offset=0):
    """Write rows whose zero-based input index modulo ``stride`` is ``offset``."""
    if stride <= 0:
        raise ValueError("stride must be greater than zero")
    if offset < 0 or offset >= stride:
        raise ValueError("offset must be at least zero and less than stride")

    input_rows = 0
    output_rows = 0
    with source.open(newline="", encoding="utf-8") as input_stream:
        reader = csv.reader(input_stream)
        header = next(reader, None)
        if not header:
            raise ValueError(f"{source}: input CSV has no header")
        first_row = next(reader, None)
        if first_row is None:
            raise ValueError(f"{source}: input CSV has no job rows")

        with destination.open("w", newline="", encoding="utf-8") as output_stream:
            writer = csv.writer(output_stream, lineterminator="\n")
            writer.writerow(header)
            for index, row in enumerate(itertools.chain((first_row,), reader)):
                input_rows += 1
                if index % stride == offset:
                    writer.writerow(row)
                    output_rows += 1

    return input_rows, output_rows


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="input trace CSVs")
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="directory for sampled traces"
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=4,
        help="retain one row from each block of this size (default: 4)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="zero-based row to retain within each block (default: 0)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing output traces"
    )
    args = parser.parse_args()

    if args.stride <= 0:
        parser.error("--stride must be greater than zero")
    if args.offset < 0 or args.offset >= args.stride:
        parser.error("--offset must be at least zero and less than --stride")

    inputs = [path.resolve() for path in args.inputs]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        parser.error("missing input files: " + ", ".join(missing))
    names = [path.name for path in inputs]
    if len(names) != len(set(names)):
        parser.error("input filenames must be unique")

    output_dir = args.output_dir.resolve()
    destinations = [output_dir / name for name in names]
    if any(source == destination for source, destination in zip(inputs, destinations)):
        parser.error("--output-dir must not overwrite an input trace")
    existing = [str(path) for path in destinations if path.exists()]
    if existing and not args.overwrite:
        parser.error(
            "output files already exist (pass --overwrite to replace them): "
            + ", ".join(existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for source, destination in zip(inputs, destinations):
            input_rows, output_rows = downsample(
                source, destination, args.stride, args.offset
            )
            print(
                f"{source.name}: retained {output_rows} of {input_rows} jobs "
                f"in {destination}",
                file=sys.stderr,
            )
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
