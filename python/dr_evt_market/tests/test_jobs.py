################################################################################
#         Copyright 2023 Lawrence Livermore National Security, LLC             #
#         See the top-level LICENSE file for details.                          #
#                                                                              #
#         SPDX-License-Identifier: MIT                                         #
################################################################################

"""Tests for the job stream and trace preparation."""

import tempfile
import unittest
from pathlib import Path

from dr_evt_market import Job, prepare, read_jobs, write_jobs


_DATA = Path(__file__).with_name("data")


class JobTests(unittest.TestCase):
    """Exercise job input and merged trace preparation."""

    def test_read_jobs_fixture(self) -> None:
        """The fixture preserves order, tags and oversize demand."""
        jobs = read_jobs(_DATA / "jobs.csv")
        self.assertEqual(len(jobs), 20)
        self.assertEqual(jobs[0].job_id, "j000001")
        self.assertEqual(jobs[3].requires, frozenset({"gpu", "nvidia"}))
        self.assertEqual(jobs[13].num_nodes, 120)
        self.assertFalse(hasattr(jobs[0], "source"))

    def test_job_freezes_requirements_and_duplicate_ids_fail(self) -> None:
        """Job freezes tags, and the reader rejects duplicate identifiers."""
        job = Job("j1", -1, 0, 0, -1.0, {"gpu"}, 0)
        self.assertEqual(job.requires, frozenset({"gpu"}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.csv"
            path.write_text(
                "job_id,job_submit_time,num_nodes,time_limit,bid\n"
                "same,0,1,1,1\n"
                "same,1,1,1,1\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_jobs(path)

    def test_prepare_lc_interval_and_summary(self) -> None:
        """LC preparation filters one interval and counts each drop reason."""
        jobs, summary, rows = prepare(
            {"tioga": _DATA / "trace.csv"}, start=1000, hours=0.05, seed=3
        )
        self.assertEqual(summary, {
            "read": 12, "kept": 5, "no_nodes": 1, "no_limit": 1,
            "no_start": 2, "bad_runtime": 1, "outside_interval": 2,
            "kept:tioga": 5,
        })
        self.assertEqual([job.submit_s for job in jobs], [0, 35, 85, 115, 174])
        self.assertEqual(jobs[0].limit_s, 120)
        self.assertEqual(rows[0]["source"], "tioga")
        self.assertTrue(all(job.requires == {"gpu"} for job in jobs))

    def test_sources_merge_on_one_origin(self) -> None:
        """Sources share one clock and contribute to persona identity."""
        traces = {"beta": _DATA / "trace.csv", "alpha": _DATA / "trace.csv"}
        jobs, summary, rows = prepare(traces, start=1000, hours=0.05, seed=0)
        self.assertEqual(summary["kept:alpha"], 5)
        self.assertEqual(summary["kept:beta"], 5)
        self.assertEqual([job.submit_s for job in jobs[:2]], [0, 0])
        self.assertEqual([row["source"] for row in rows[:2]], ["alpha", "beta"])
        self.assertEqual([row["user"] for row in rows[:2]], ["2", "2"])
        self.assertEqual([row["persona"] for row in rows[:2]], ["value", "tier"])

    def test_prepare_is_deterministic_and_round_trips(self) -> None:
        """A seed fixes output bytes, and written rows read back as jobs."""
        traces = {"tioga": _DATA / "trace.csv"}
        jobs, _, rows = prepare(traces, start=1000, hours=0.05, seed=4)
        _, _, same_rows = prepare(traces, start=1000, hours=0.05, seed=4)
        _, _, other_rows = prepare(traces, start=1000, hours=0.05, seed=5)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, other = root / "a.csv", root / "b.csv", root / "c.csv"
            write_jobs(rows, first)
            write_jobs(same_rows, second)
            write_jobs(other_rows, other)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertNotEqual(first.read_bytes(), other.read_bytes())
            self.assertEqual(read_jobs(first), jobs)

    def test_persona_and_multiplier_are_hash_seeded(self) -> None:
        """A fixed seed, source, user and job ID give a pinned multiplier."""
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "simple.csv"
            trace.write_text(
                "job_submit_time,num_nodes,time_limit,user\n"
                "100,2,60,fixed-user\n",
                encoding="utf-8",
            )
            jobs, _, rows = prepare(
                {"tioga": trace}, trace_format="simple", seed=8
            )
        self.assertEqual(rows[0]["persona"], "value")
        self.assertEqual(jobs[0].bid, 4.6178)

    def test_simple_times_are_floored_sorted_and_shifted(self) -> None:
        """Simple fractional times use the earliest floored submit as zero."""
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "simple.csv"
            trace.write_text(
                "job_submit_time,num_nodes,time_limit,actual_run_time\n"
                "12.9,2,5.9,3.9\n"
                "10.8,1,4.2,2.7\n",
                encoding="utf-8",
            )
            jobs, summary, _ = prepare(
                {"simple": trace}, trace_format="simple"
            )
        self.assertEqual([job.submit_s for job in jobs], [0, 2])
        self.assertEqual([job.limit_s for job in jobs], [4, 5])
        self.assertEqual([job.runtime_s for job in jobs], [2, 3])
        self.assertEqual(summary["kept:simple"], 2)
        self.assertEqual(sum(summary[key] for key in (
            "no_nodes", "no_limit", "no_start", "bad_runtime",
            "outside_interval",
        )), 0)


if __name__ == "__main__":
    unittest.main()
