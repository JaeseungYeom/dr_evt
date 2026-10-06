#!/usr/bin/env python3
"""Tests for historical sampling and statistical trace generation."""

import csv
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_synthetic_job_stream import (  # noqa: E402
    generate_jobs,
    submission_regime,
)
from sample_job_stream import read_eligible_jobs, sample_jobs  # noqa: E402


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

    def test_custom_limit_caps_time_limit_and_long_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = read_eligible_jobs(
                self.write_trace(directory),
                Decimal(0),
                successful_only=False,
                maximum_time_limit=Decimal(100),
            )

        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            [(job["time_limit"], job["duration"]) for job in jobs],
            [("100", "100"), ("100", "99")],
        )

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


class CompletedTraceInputTests(unittest.TestCase):
    def test_duration_is_derived_from_begin_and_end_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "completed.csv"
            with path.open("w", newline="", encoding="utf-8") as destination:
                writer = csv.DictWriter(
                    destination,
                    fieldnames=(
                        "submit_time",
                        "num_nodes",
                        "begin_time",
                        "end_time",
                        "time_limit",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "submit_time": "1",
                        "num_nodes": "2",
                        "begin_time": "4.25",
                        "end_time": "10.75",
                        "time_limit": "20",
                    }
                )
            jobs = read_eligible_jobs(path, Decimal(0), successful_only=False)

        self.assertEqual(jobs[0]["duration"], "6.50")
        self.assertEqual(jobs[0]["duration_decimal"], Decimal("6.50"))

    def test_overrun_matches_completed_trace_converter(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "completed.csv"
            converted_path = Path(directory) / "converted.csv"
            input_path.write_text(
                "submit_time,num_nodes,begin_time,end_time,time_limit\n"
                "1,2,10.10,20.35,5\n",
                encoding="utf-8",
            )
            converter = Path(__file__).resolve().parents[1] / (
                "convert_completed_trace_to_simulation.sh"
            )
            subprocess.run(
                [str(converter), str(input_path), str(converted_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            with converted_path.open(newline="", encoding="utf-8") as source:
                converted = next(csv.DictReader(source))
            generated = read_eligible_jobs(
                input_path, Decimal(0), successful_only=False
            )[0]

        self.assertEqual(
            Decimal(generated["duration"]), Decimal(converted["duration"])
        )
        self.assertEqual(
            Decimal(generated["time_limit"]), Decimal(converted["time_limit"])
        )


class WorkloadRegimeTests(unittest.TestCase):
    def test_selected_window_keeps_complete_job_sequence_intact(self):
        eligible = []
        for index in range(12):
            duration = Decimal(10 if index < 6 else 1000)
            eligible.append(
                {
                    "submit_time": str(index),
                    "submit_time_decimal": Decimal(index),
                    "num_nodes": str(1 if index < 6 else 100),
                    "duration": str(duration),
                    "duration_decimal": duration,
                    "time_limit": "2000",
                }
            )

        synthetic, start = sample_jobs(eligible, 4, random.Random(7))
        expected = [
            {
                "submit_time": job["submit_time"],
                "num_nodes": job["num_nodes"],
                "duration": job["duration"],
                "time_limit": job["time_limit"],
            }
            for job in eligible[start : start + 4]
        ]

        self.assertEqual(synthetic, expected)

    def test_with_replacement_resamples_complete_records(self):
        eligible = []
        for index in range(6):
            eligible.append(
                {
                    "submit_time": str(index),
                    "submit_time_decimal": Decimal(index),
                    "num_nodes": str(index + 1),
                    "duration": str(100 + index),
                    "duration_decimal": Decimal(100 + index),
                    "time_limit": str(200 + index),
                }
            )

        synthetic, start = sample_jobs(
            eligible, 4, random.Random(2), with_replacement=True
        )
        window = eligible[start : start + 4]
        complete_records = {
            (job["num_nodes"], job["duration"], job["time_limit"])
            for job in window
        }

        self.assertTrue(
            all(
                (job["num_nodes"], job["duration"], job["time_limit"])
                in complete_records
                for job in synthetic
            )
        )


class PredictableRandom:
    """Minimal deterministic random source for population-selection tests."""

    def randrange(self, stop):
        return 0

    def sample(self, population, count):
        return list(population)[-count:]

    def choice(self, population):
        return population[-1]


class SyntheticDistributionTests(unittest.TestCase):
    def make_jobs(self):
        jobs = []
        for index in range(6):
            duration = Decimal(100 + index)
            jobs.append(
                {
                    "submit_time": str(index),
                    "submit_time_decimal": Decimal(index),
                    "num_nodes": str(index + 1),
                    "duration": str(duration),
                    "duration_decimal": duration,
                    "time_limit": str(200 + index),
                }
            )
        return jobs

    def test_global_scope_samples_node_duration_pairs_globally(self):
        eligible = self.make_jobs()

        synthetic, start = generate_jobs(
            eligible, 2, PredictableRandom(), sampling_scope="global"
        )

        self.assertEqual(start, 0)
        self.assertEqual(
            [(job["submit_time"], job["num_nodes"]) for job in synthetic],
            [("0", "5"), ("1", "6")],
        )
        self.assertEqual([job["duration"] for job in synthetic], ["104", "105"])
        self.assertEqual([job["time_limit"] for job in synthetic], ["204", "205"])

    def test_local_scope_samples_node_duration_pairs_from_arrival_window(self):
        eligible = self.make_jobs()

        synthetic, start = generate_jobs(
            eligible, 2, PredictableRandom(), sampling_scope="local"
        )

        self.assertEqual(start, 0)
        self.assertEqual(
            [(job["submit_time"], job["num_nodes"]) for job in synthetic],
            [("0", "1"), ("1", "2")],
        )
        self.assertEqual([job["duration"] for job in synthetic], ["100", "101"])
        self.assertEqual([job["time_limit"] for job in synthetic], ["200", "201"])

    def test_submission_regime_uses_local_weekday_and_hour(self):
        pacific = ZoneInfo("America/Los_Angeles")

        def epoch(year, month, day, hour):
            return Decimal(
                datetime(
                    year, month, day, hour, tzinfo=timezone.utc
                ).timestamp()
            )

        self.assertEqual(
            submission_regime(epoch(2024, 1, 8, 17), pacific, 9, 17),
            "work_hours",
        )
        self.assertEqual(
            submission_regime(epoch(2024, 1, 9, 1), pacific, 9, 17),
            "off_hours",
        )
        self.assertEqual(
            submission_regime(epoch(2024, 1, 6, 18), pacific, 9, 17),
            "weekend",
        )

    def test_work_cycle_binning_samples_pairs_from_matching_regime(self):
        pacific = ZoneInfo("America/Los_Angeles")

        def job(index, year, month, day, hour):
            submit_time = datetime(
                year, month, day, hour, tzinfo=timezone.utc
            ).timestamp()
            duration = Decimal(100 + index)
            return {
                "submit_time": str(submit_time),
                "submit_time_decimal": Decimal(submit_time),
                "num_nodes": str(index + 1),
                "duration": str(duration),
                "duration_decimal": duration,
                "time_limit": str(200 + index),
            }

        eligible = [
            job(0, 2024, 1, 8, 17),
            job(1, 2024, 1, 8, 18),
            job(2, 2024, 1, 9, 2),
            job(3, 2024, 1, 9, 17),
            job(4, 2024, 1, 9, 18),
            job(5, 2024, 1, 13, 18),
        ]

        synthetic, _ = generate_jobs(
            eligible,
            2,
            PredictableRandom(),
            sampling_scope="global",
            time_binning="work-cycle",
            timezone=pacific,
        )

        self.assertEqual([job["num_nodes"] for job in synthetic], ["4", "5"])


if __name__ == "__main__":
    unittest.main()
