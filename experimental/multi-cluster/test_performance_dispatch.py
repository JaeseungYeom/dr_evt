#!/usr/bin/env python3
"""Unit tests for the multi-system performance dispatcher."""

import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from grpc_performance_dispatch import (choose_system, estimate_release_wait,
                                       estimate_wait, nearest_profile,
                                       read_performance_table)


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
