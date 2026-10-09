#!/usr/bin/env python3
"""Compare Python-reference and C++ simulator schedule CSV files."""

import argparse
import csv
import sys


def load_schedule(filename):
    """Return ordered ``(job_id, start_time, end_time)`` schedule rows."""
    jobs = []
    with open(filename, "r") as stream:
        reader = csv.DictReader(stream)
        for row_index, row in enumerate(reader):
            job_id_text = row.get("job_idx", row.get("job_id", row.get("idx", "")))
            job_id = int(job_id_text) if job_id_text else row_index
            start_text = row.get("start_time", row.get("begin_time", ""))
            end_text = row.get("end_time", "")
            if not start_text or not end_text:
                raise ValueError(
                    "job {} in {} has no complete schedule".format(
                        job_id, filename
                    )
                )
            jobs.append((job_id, float(start_text), float(end_text)))
    if not jobs:
        raise ValueError("{} contains no scheduled jobs".format(filename))
    return jobs


def main():
    parser = argparse.ArgumentParser(
        description="Compare Python and C++ scheduler output"
    )
    parser.add_argument("python_output")
    parser.add_argument("cpp_output")
    parser.add_argument("--tolerance", type=float, default=0.001)
    args = parser.parse_args()

    try:
        python_jobs = load_schedule(args.python_output)
        cpp_jobs = load_schedule(args.cpp_output)
    except (OSError, ValueError, csv.Error) as error:
        print("ERROR: {}".format(error))
        return 1

    if len(python_jobs) != len(cpp_jobs):
        print(
            "ERROR: job count mismatch - Python: {}, C++: {}".format(
                len(python_jobs), len(cpp_jobs)
            )
        )
        return 1

    mismatches = []
    for row_index, (python_job, cpp_job) in enumerate(
        zip(python_jobs, cpp_jobs)
    ):
        python_id, python_start, python_end = python_job
        cpp_id, cpp_start, cpp_end = cpp_job
        if python_id != cpp_id:
            mismatches.append(
                "row {}: ID mismatch - Python: {}, C++: {}".format(
                    row_index, python_id, cpp_id
                )
            )
        elif (
            abs(python_start - cpp_start) > args.tolerance
            or abs(python_end - cpp_end) > args.tolerance
        ):
            mismatches.append(
                "job {}: Python=({:.3f}, {:.3f}), C++=({:.3f}, {:.3f})".format(
                    python_id, python_start, python_end, cpp_start, cpp_end
                )
            )

    if mismatches:
        print("MISMATCHES FOUND:")
        for mismatch in mismatches[:20]:
            print("  {}".format(mismatch))
        if len(mismatches) > 20:
            print("  ... and {} more".format(len(mismatches) - 20))
        return 1

    print(
        "MATCH: {} jobs; start/end times agree within {}".format(
            len(python_jobs), args.tolerance
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
