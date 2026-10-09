#!/usr/bin/env python3
"""Tests for the trace-scale comparison plot inputs."""

import argparse
import csv
import pathlib
import sys
import tempfile
import unittest

# Production scripts live one directory above this test suite.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import plot_trace_scale_comparison as comparison


class TraceScaleComparisonTests(unittest.TestCase):
    def test_campaign_inputs_are_explicit(self):
        label, paths = comparison.parse_campaign_spec(
            "25K=/results/main.csv,/results/wait.csv"
        )
        self.assertEqual(label, "25K")
        self.assertEqual(
            paths,
            (pathlib.Path("/results/main.csv"), pathlib.Path("/results/wait.csv")),
        )
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "LABEL=CSV"):
            comparison.parse_campaign_spec("missing-paths")

    def test_load_campaign_selects_common_policies(self):
        fields = [
            "dispatch_policy",
            "wall_time_policy",
            "case",
            "runs",
            "average_turnaround_time_mean",
            "average_turnaround_time_stddev",
            "average_bounded_slowdown_mean",
            "average_bounded_slowdown_stddev",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "summary_aggregate.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                for case, _ in comparison.CASES:
                    for policy, _, _ in comparison.POLICIES:
                        writer.writerow(
                            {
                                "dispatch_policy": policy,
                                "wall_time_policy": "adapted-limit",
                                "case": case,
                                "runs": 10,
                                "average_turnaround_time_mean": 2,
                                "average_turnaround_time_stddev": 1,
                                "average_bounded_slowdown_mean": 4,
                                "average_bounded_slowdown_stddev": 3,
                            }
                        )
                writer.writerow(
                    {
                        "dispatch_policy": "WaitTimeOnly",
                        "wall_time_policy": "adapted-limit",
                        "case": "wait_time_only",
                        "runs": 10,
                        "average_turnaround_time_mean": 3,
                        "average_turnaround_time_stddev": 1,
                        "average_bounded_slowdown_mean": 5,
                        "average_bounded_slowdown_stddev": 2,
                    }
                )

            records = comparison.load_campaign([path])

        self.assertEqual(len(records["cases"]), 6)
        self.assertEqual(
            [row["dispatch_policy"] for row in records["cases"][0]],
            ["turnaround", "RelPerfOnly"],
        )
        self.assertEqual(records["wait"]["dispatch_policy"], "WaitTimeOnly")

        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            comparison.write_plot_data([("fixture", records)], output)
            with (output / "trace-scale-comparison-data.csv").open(
                newline="", encoding="utf-8"
            ) as stream:
                exported = list(csv.DictReader(stream))

        self.assertEqual(len(exported), 13)
        self.assertEqual(exported[-1]["case"], "wait_time_only")


if __name__ == "__main__":
    unittest.main()
