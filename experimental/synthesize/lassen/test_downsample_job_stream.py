#!/usr/bin/env python3
"""Tests for deterministic job-stream downsampling."""

import csv
import pathlib
import sys
import tempfile
import unittest


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from downsample_job_stream import downsample  # noqa: E402


class DownsampleJobStreamTests(unittest.TestCase):
    def test_retains_selected_row_from_each_block_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "input.csv"
            destination = root / "output.csv"
            source.write_text(
                "submit_time,num_nodes,label\n"
                + "".join(
                    f'{index},{index + 1},"job,{index}"\n' for index in range(10)
                ),
                encoding="utf-8",
            )

            input_rows, output_rows = downsample(source, destination, 4, 1)

            with destination.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual((input_rows, output_rows), (10, 3))
        self.assertEqual([row["submit_time"] for row in rows], ["1", "5", "9"])
        self.assertEqual([row["label"] for row in rows], ["job,1", "job,5", "job,9"])

    def test_rejects_invalid_stride_and_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "input.csv"
            destination = root / "output.csv"
            source.write_text("submit_time\n1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stride"):
                downsample(source, destination, 0, 0)
            with self.assertRaisesRegex(ValueError, "offset"):
                downsample(source, destination, 4, 4)

    def test_rejects_header_only_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "input.csv"
            destination = root / "output.csv"
            source.write_text("submit_time,num_nodes\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "no job rows"):
                downsample(source, destination)


if __name__ == "__main__":
    unittest.main()
