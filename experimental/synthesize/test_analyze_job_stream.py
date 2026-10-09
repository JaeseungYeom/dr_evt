#!/usr/bin/env python3
"""Regression tests for :mod:`analyze_job_stream`.

The analysis tool reads a completed scheduler trace and reports workload
statistics such as peak concurrent nodes, utilization, turnaround time, and
bounded slowdown. These tests create small temporary CSV traces with known
answers and verify capacity inference, an explicit capacity override, accepted
submission-time column names, and zero-duration job handling.

The tests use only temporary fixtures and do not read or modify production
trace files.
"""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_job_stream import calculate_metrics, read_jobs


class AnalyzeJobStreamTests(unittest.TestCase):
    def write_trace(self, directory):
        path = Path(directory) / "trace.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(
                ("submit_time", "num_nodes", "begin_time", "end_time")
            )
            writer.writerow((0, 2, 2, 12))
            writer.writerow((1, 3, 5, 25))
        return path

    def test_metrics_use_peak_concurrency_as_inferred_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = calculate_metrics(read_jobs(self.write_trace(directory)))

        self.assertEqual(metrics["largest_job_nodes"], 3)
        self.assertEqual(metrics["peak_concurrent_nodes"], 5)
        self.assertEqual(metrics["operational_nodes"], 5)
        self.assertAlmostEqual(metrics["utilization"], 80 / (5 * 25))
        self.assertAlmostEqual(metrics["average_turnaround_time_seconds"], 18)
        self.assertAlmostEqual(metrics["average_bounded_slowdown"], 1.2)
        self.assertAlmostEqual(metrics["average_duration_seconds"], 15)

    def test_known_capacity_overrides_inferred_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            metrics = calculate_metrics(
                read_jobs(self.write_trace(directory)), total_nodes=8
            )

        self.assertEqual(metrics["operational_nodes"], 8)
        self.assertEqual(metrics["capacity_source"], "provided")
        self.assertAlmostEqual(metrics["utilization"], 80 / (8 * 25))

    def test_accepts_simulator_submission_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_trace(directory)
            text = path.read_text(encoding="utf-8").replace(
                "submit_time", "job_submit_time", 1
            )
            path.write_text(text, encoding="utf-8")
            metrics = calculate_metrics(read_jobs(path))

        self.assertEqual(metrics["jobs"], 2)

    def test_accepts_zero_duration_from_integer_simulator_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.csv"
            path.write_text(
                "submit_time,num_nodes,begin_time,end_time\n0,1,2,2\n",
                encoding="utf-8",
            )
            metrics = calculate_metrics(read_jobs(path), total_nodes=1)

        self.assertEqual(metrics["jobs"], 1)
        self.assertEqual(metrics["average_duration_seconds"], 0)
        self.assertEqual(metrics["peak_concurrent_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
