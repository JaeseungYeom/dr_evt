#!/usr/bin/env python3
"""Unit tests for the multi-system performance dispatcher."""

import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from grpc_performance_dispatch import (choose_system, estimate_release_wait,
                                       estimate_wait, nearest_profile,
                                       read_arrivals, read_performance_table)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SAMPLE_TRACE = _REPO_ROOT / "python/examples/sample_trace.csv"
_PERFORMANCE_TABLE = pathlib.Path(__file__).with_name("performance_table.csv")


def window(now, available, releases=(), shadow=-1):
    return SimpleNamespace(
        current_time=now,
        available_nodes=available,
        shadow_time=shadow,
        releases=[SimpleNamespace(time=time, nodes_released=nodes)
                  for time, nodes in releases],
    )


class PerformanceDispatchTests(unittest.TestCase):
    def test_normalized_nearest_profile(self):
        profiles = [
            {"profile_id": "small", "num_nodes": 10, "time_limit": 100},
            {"profile_id": "large", "num_nodes": 90, "time_limit": 900},
        ]
        job = {"num_nodes": 80, "limit_time": 800}
        self.assertEqual(nearest_profile(job, profiles)["profile_id"], "large")

    def test_wait_uses_cumulative_releases(self):
        snapshot = window(10, 2, ((15, 3), (21, 4)))
        self.assertEqual(estimate_release_wait(snapshot, 5), 5)
        self.assertEqual(estimate_release_wait(snapshot, 8), 11)
        self.assertTrue(estimate_release_wait(snapshot, 10) == float("inf"))

    def test_wait_uses_shadow_and_queue_horizon(self):
        snapshot = window(10, 2, shadow=30)
        self.assertEqual(estimate_wait(snapshot, 8, 5, 40), 60)
        immediately_backfillable = window(10, 8, shadow=30)
        self.assertEqual(estimate_wait(immediately_backfillable, 8, 5, 40), 0)

    def test_turnaround_balances_wait_and_speed(self):
        job = {"job_id": "j", "num_nodes": 8, "limit_time": 100}
        profile = {"performance": {"fast": 4.0, "ready": 1.0}}
        choice = choose_system(
            job, profile, ["fast", "ready"], [10, 10],
            [window(0, 2, ((80, 8),), shadow=80), window(0, 10)],
            [0, 0])
        self.assertEqual(choice["system_id"], "ready")
        self.assertEqual(choice["predicted_turnaround"], 100)

    def test_sample_plus_large_job_uses_all_three_systems(self):
        """A large-profile job extends the sample across all three systems."""
        system_ids = ["system-1", "system-2", "system-3"]
        capacities = [100, 200, 50]
        profiles = read_performance_table(_PERFORMANCE_TABLE, system_ids)
        jobs = read_arrivals(_SAMPLE_TRACE)
        jobs.append({
            "job_id": "system-3-job",
            "submit_time": 100.0,
            "num_nodes": 45,
            "queue": "1",
            "limit_time": 900.0,
        })

        choices = []
        for job in jobs:
            profile = nearest_profile(job, profiles)
            choice = choose_system(
                job, profile, system_ids, capacities,
                [window(job["submit_time"], capacity)
                 for capacity in capacities],
                [0.0, 0.0, 0.0])
            choices.append(choice["system_id"])

        self.assertNotIn("system-3", choices[:-1])
        self.assertEqual(choices[-1], "system-3")
        self.assertEqual(set(choices), set(system_ids))

    def test_performance_table_requires_positive_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "performance.csv"
            path.write_text(
                "profile_id,num_nodes,time_limit,a,b\n"
                "cpu,4,20,2.0,0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid performance"):
                read_performance_table(path, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
