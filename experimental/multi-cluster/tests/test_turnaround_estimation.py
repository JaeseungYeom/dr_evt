#!/usr/bin/env python3
"""Unit tests for per-job turnaround-estimation analysis."""

import csv
import os
import pathlib
import sys
import tempfile
import unittest

# Production scripts live one directory above this test suite.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import analyze_turnaround_estimation as analysis


class TurnaroundEstimationTests(unittest.TestCase):
    def test_calculate_errors(self):
        metrics = analysis.calculate_errors([(10.0, 8.0), (20.0, 40.0)])
        self.assertEqual(metrics["jobs"], 2)
        self.assertAlmostEqual(metrics["mean_predicted_turnaround_seconds"], 15.0)
        self.assertAlmostEqual(metrics["mean_replayed_turnaround_seconds"], 24.0)
        self.assertAlmostEqual(metrics["mape_percent"], 37.5)
        self.assertAlmostEqual(metrics["smape_percent"], 400.0 / 9.0)

    def test_load_pairs_matches_system_and_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            dispatch = root / "dispatch.csv"
            details = root / "details.csv"
            with dispatch.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("system_id", "job_id", "predicted_turnaround"),
                )
                writer.writeheader()
                writer.writerows(
                    (
                        {"system_id": "a", "job_id": "1", "predicted_turnaround": 12},
                        {"system_id": "b", "job_id": "1", "predicted_turnaround": 20},
                    )
                )
            with details.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("system_id", "job_id", "turnaround_time"),
                )
                writer.writeheader()
                writer.writerows(
                    (
                        {"system_id": "b", "job_id": "1", "turnaround_time": 18},
                        {"system_id": "a", "job_id": "1", "turnaround_time": 10},
                    )
                )
            self.assertEqual(
                analysis.load_pairs(dispatch, details), [(12.0, 10.0), (20.0, 18.0)]
            )

    def test_load_pairs_rejects_missing_actual_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            dispatch = root / "dispatch.csv"
            details = root / "details.csv"
            dispatch.write_text(
                "system_id,job_id,predicted_turnaround\na,1,12\n", encoding="utf-8"
            )
            details.write_text(
                "system_id,job_id,turnaround_time\na,2,10\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "missing job"):
                analysis.load_pairs(dispatch, details)

    def test_installed_module_requires_prefix(self):
        original = os.environ.pop("CMAKE_INSTALL_PREFIX", None)
        try:
            with self.assertRaisesRegex(ValueError, "CMAKE_INSTALL_PREFIX"):
                analysis.use_installed_python_module()
        finally:
            if original is not None:
                os.environ["CMAKE_INSTALL_PREFIX"] = original


if __name__ == "__main__":
    unittest.main()
