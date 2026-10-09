#!/usr/bin/env python3
"""Unit tests for completed KT campaign analysis."""

import csv
import pathlib
import sys
import tempfile
import unittest

# Production scripts live one directory above this test suite.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import analyze_kt_scheduling_campaign as analysis


class CampaignAnalysisTests(unittest.TestCase):
    def test_parse_stem_uses_canonical_policy_names(self):
        marker = pathlib.Path(
            "RelPerfOnly.actual-duration.knowledge_transfer_5_percent.run_09.complete"
        )
        self.assertEqual(
            analysis.parse_stem(marker),
            (
                "RelPerfOnly",
                "actual-duration",
                "knowledge_transfer_5_percent",
                9,
            ),
        )

    def test_parse_stem_rejects_retired_policy_name(self):
        marker = pathlib.Path("IPDPS" + "24.adapted-limit.ideal.run_01.complete")
        with self.assertRaisesRegex(ValueError, "unrecognized completion marker"):
            analysis.parse_stem(marker)

    def test_read_replay_validates_and_returns_systems(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run.replay.log"
            path.write_text(
                "alpha: jobs=2 average_wait=3600 average_turnaround=3700\n"
                "beta: jobs=3 average_wait=1800 average_turnaround=2000\n"
                "overall: jobs=5 average_wait=2520 average_turnaround=2680\n",
                encoding="utf-8",
            )
            systems, overall = analysis.read_replay(path, {"alpha": 4, "beta": 8})
            self.assertEqual(systems["alpha"]["average_wait"], 3600)
            self.assertEqual(overall["jobs"], 5)

    def test_format_duration_includes_seconds_and_hours(self):
        self.assertEqual(analysis.format_duration(27027), "27,027 s (7.51 h)")

    def test_read_plot_baseline(self):
        fields = [
            "dispatch_policy",
            "wall_time_policy",
            "case",
            "runs",
            "dropped_jobs_mean",
            "dropped_jobs_stddev",
            *(
                f"{metric}_{suffix}"
                for metric in analysis.study.METRICS
                for suffix in ("mean", "stddev")
            ),
        ]
        values = {
            "dispatch_policy": "WaitTimeOnly",
            "wall_time_policy": "adapted-limit",
            "case": "wait_time_only",
            "runs": "10",
            **{field: "1.5" for field in fields[4:]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "summary_aggregate.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(values)
            rows = analysis.read_plot_baselines([path])

        self.assertEqual(rows[0]["runs"], 10)
        self.assertEqual(rows[0]["average_turnaround_time_mean"], 1.5)

    def test_read_plot_baseline_rejects_other_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "summary_aggregate.csv"
            path.write_text(
                "dispatch_policy,wall_time_policy,case\n"
                "turnaround,adapted-limit,ideal\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unexpected plot baseline"):
                analysis.read_plot_baselines([path])


if __name__ == "__main__":
    unittest.main()
