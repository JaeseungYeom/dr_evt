#!/usr/bin/env python3
"""Tests for training-subset application-average predictions."""

import csv
import pathlib
import sys
import tempfile
import unittest

# Production scripts live one directory above this test suite.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import build_app_average_from_training as builder


class TrainingApplicationAverageTests(unittest.TestCase):
    def test_read_ground_truth_accepts_lowercase_identity_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "truth.csv"
            path.write_text("app,args,ranks,machine\na,x,1,2\n", encoding="utf-8")
            rows, modes = builder.read_ground_truth(path)
        self.assertEqual(modes, ["machine"])
        self.assertEqual(rows[0]["App"], "a")

    def test_normalizes_to_reference_and_weights_workloads_equally(self):
        fields = (
            "app",
            "args",
            "ranks",
            "source_machine",
            "target_machine",
            "true_relative_runtime",
        )
        records = (
            ("app", "a", "1", "s1", "borax", "2"),
            ("app", "a", "1", "s1", "machine", "6"),
            ("app", "a", "1", "s2", "borax", "4"),
            ("app", "a", "1", "s2", "machine", "12"),
            ("app", "b", "1", "s1", "borax", "5"),
            ("app", "b", "1", "s1", "machine", "10"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "training.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                writer.writerows(records)
            means = builder.training_means(path, ["machine"])
        self.assertEqual(means[("app", "1", "machine")], 2.5)

    def test_missing_training_group_produces_blank(self):
        truth = [{"App": "app", "Args": "x", "Ranks": "1", "machine": "2"}]
        rows = builder.build_rows(truth, ["machine"], {})
        self.assertEqual(rows[0]["machine"], "")


if __name__ == "__main__":
    unittest.main()
