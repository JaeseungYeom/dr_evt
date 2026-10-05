#!/usr/bin/env python3
"""Tests for configurable synthetic-trace eligibility rules."""

import csv
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_synthetic_job_stream import read_eligible_jobs  # noqa: E402


class MaximumTimeLimitTests(unittest.TestCase):
    def write_trace(self, directory):
        path = Path(directory) / "input.csv"
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=(
                    "submit_time",
                    "num_nodes",
                    "duration",
                    "time_limit",
                ),
            )
            writer.writeheader()
            writer.writerows(
                (
                    {
                        "submit_time": "2",
                        "num_nodes": "1",
                        "duration": "99",
                        "time_limit": "150",
                    },
                    {
                        "submit_time": "1",
                        "num_nodes": "2",
                        "duration": "100",
                        "time_limit": "150",
                    },
                )
            )
        return path

    def test_limit_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = read_eligible_jobs(
                self.write_trace(directory), Decimal(0), successful_only=False
            )

        self.assertEqual([job["time_limit"] for job in jobs], ["150", "150"])

    def test_custom_limit_caps_short_jobs_and_removes_long_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = read_eligible_jobs(
                self.write_trace(directory),
                Decimal(0),
                successful_only=False,
                maximum_time_limit=Decimal(100),
            )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["time_limit"], "100")
        self.assertEqual(jobs[0]["duration"], "99")

    def test_cli_rejects_nonpositive_limit(self):
        script = Path(__file__).resolve().with_name(
            "generate_synthetic_job_stream.py"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "input.csv",
                "output.csv",
                "1",
                "--max-time-limit",
                "0",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("must be a finite, positive number", result.stderr)

    def test_cli_writes_unix_line_endings(self):
        script = Path(__file__).resolve().with_name(
            "generate_synthetic_job_stream.py"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.csv"
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(self.write_trace(directory)),
                    str(output),
                    "2",
                    "--seed",
                    "7",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(b"\r\n", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
