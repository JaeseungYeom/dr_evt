#!/usr/bin/env python3
"""Regression coverage for the Python conservative scheduler CLI options."""

import csv
import subprocess
import sys
import tempfile
from pathlib import Path


def run_reference(source_dir, trace, outdir, *options):
    subprocess.run(
        [sys.executable, source_dir / "scripts" / "python_conservative_scheduler.py",
         source_dir / trace, "--nodes", "10", "--outdir", outdir, *options],
        check=True,
        capture_output=True,
        text=True,
    )
    output = outdir / (Path(trace).stem + "_conservative_reference.csv")
    with output.open(newline="") as stream:
        return list(csv.DictReader(stream))


def main():
    source_dir = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="dr-evt-python-options-") as temp:
        outdir = Path(temp)
        horizon = run_reference(
            source_dir, "tests/test_traces/feature/planning_horizon.csv", outdir,
            "--backfill-interval", "5", "--planning-horizon", "10",
        )
        assert [(row["start_time"], row["end_time"]) for row in horizon] == [
            ("0.0", "100.0"), ("104.0", "114.0"), ("5.0", "104.0")]

        resolution = run_reference(
            source_dir,
            "tests/test_traces/feature/planning_time_resolution.csv",
            outdir,
            "--planning-time-resolution",
            "60",
        )
        assert resolution[2]["duration"] == "59.0"
        assert resolution[2]["start_time"] == "130.0"
        assert float(resolution[2]["end_time"]) - float(
            resolution[2]["start_time"]
        ) == 59.0


if __name__ == "__main__":
    main()
