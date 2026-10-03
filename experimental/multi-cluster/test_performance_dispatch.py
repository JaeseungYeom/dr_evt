#!/usr/bin/env python3
"""Unit tests for the Python/gRPC multi-cluster dispatcher policy."""

import pathlib
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import grpc_performance_dispatch as dispatch
from grpc_performance_dispatch import (
    choose_system,
    estimate_release_wait,
    estimate_wait,
    read_applications,
    read_arrivals,
    read_systems,
    read_workloads,
    sample_workload,
)


_FIXTURES = pathlib.Path(__file__).resolve().parent


def window(now, available, releases=(), shadow=-1):
    return SimpleNamespace(
        current_time=now,
        available_nodes=available,
        shadow_time=shadow,
        releases=[
            SimpleNamespace(time=time, nodes_released=nodes)
            for time, nodes in releases
        ],
    )


class FakeMessage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeMessages:
    ClientMessage = FakeMessage
    InitRequest = FakeMessage
    AdvanceToRequest = FakeMessage
    GetBackfillWindowRequest = FakeMessage
    GetPredictionHorizonRequest = FakeMessage
    JobAppendData = FakeMessage
    AppendJobsRequest = FakeMessage
    FinishSimulationRequest = FakeMessage


class FakeSession:
    instances = []

    def __init__(self, address, grpc, messages, service):
        del grpc, service
        self.address = address
        self.messages = messages
        self.capacity = 0
        self.current_time = 0
        self.submitted = []
        self.instances.append(self)

    def call(self, request):
        if hasattr(request, "init"):
            self.capacity = request.init.total_nodes
            self.run_time_mode = request.init.run_time_mode
            return SimpleNamespace()
        if hasattr(request, "advance_to"):
            self.current_time = request.advance_to.target_time
            return SimpleNamespace()
        if hasattr(request, "get_backfill_window"):
            return SimpleNamespace(
                get_backfill_window=window(self.current_time, self.capacity)
            )
        if hasattr(request, "get_prediction_horizon"):
            return SimpleNamespace(
                get_prediction_horizon=SimpleNamespace(horizon=0)
            )
        if hasattr(request, "append_jobs"):
            job = request.append_jobs.requests[0]
            self.submitted.append(job)
            return SimpleNamespace(
                append_jobs=SimpleNamespace(job_idx=[len(self.submitted) - 1])
            )
        if hasattr(request, "finish_simulation"):
            count = len(self.submitted)
            return SimpleNamespace(
                finish_simulation=SimpleNamespace(
                    statistics=SimpleNamespace(
                        jobs_submitted=count, jobs_completed=count, makespan=0
                    )
                )
            )
        raise AssertionError(f"unexpected request: {request.__dict__}")

    def close(self):
        pass


class PerformanceDispatchTests(unittest.TestCase):
    def setUp(self):
        self.systems = read_systems(_FIXTURES / "machines.csv")
        requirements = read_applications(_FIXTURES / "applications.csv")
        self.workloads = read_workloads(
            _FIXTURES / "workload_table.csv", self.systems, requirements
        )

    def test_hash_prefixed_input_headers_and_inferred_columns(self):
        self.assertEqual(
            [system["system_id"] for system in self.systems],
            ["dane", "mammoth", "tioga", "tuolumne", "matrix"],
        )
        self.assertEqual(self.systems[0]["cpu_column"], "dane")
        self.assertIsNone(self.systems[0]["gpu_column"])
        self.assertEqual(self.systems[-1]["cpu_column"], "matrix-cpu")
        self.assertEqual(self.systems[-1]["gpu_column"], "matrix-gpu")
        self.assertEqual(
            set(self.workloads), {"cpu-solver", "gpu-trainer", "portable-md"}
        )

    def test_arrivals_require_actual_runtime(self):
        jobs = read_arrivals(_FIXTURES / "job_stream.csv")
        self.assertEqual(jobs[0]["duration"], 80)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "jobs.csv"
            path.write_text(
                "job_submit_time,num_nodes,time_limit\n0,1,10\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "actual_run_time or duration"):
                read_arrivals(path)

    def test_sampling_is_uniform_by_application_not_row_count(self):
        generator = random.Random(19)
        counts = {app: 0 for app in self.workloads}
        for _ in range(6000):
            counts[sample_workload(self.workloads, generator)["app"]] += 1
        self.assertTrue(all(1800 < count < 2200 for count in counts.values()))

    def test_wait_uses_cumulative_releases(self):
        snapshot = window(10, 2, ((15, 3), (21, 4)))
        self.assertEqual(estimate_release_wait(snapshot, 5), 5)
        self.assertEqual(estimate_release_wait(snapshot, 8), 11)
        self.assertEqual(estimate_release_wait(snapshot, 10), float("inf"))

    def test_wait_uses_shadow_and_queue_horizon(self):
        snapshot = window(10, 2, shadow=30)
        self.assertEqual(estimate_wait(snapshot, 8, 5, 40), 60)
        immediately_backfillable = window(10, 8, shadow=30)
        self.assertEqual(estimate_wait(immediately_backfillable, 8, 5, 40), 0)

    def test_gpu_only_uses_gpu_enabled_machine(self):
        workload = self.workloads["gpu-trainer"][0]
        job = {
            "job_id": "large-gpu",
            "num_nodes": 100,
            "duration": 80,
            "limit_time": 120,
        }
        windows = [window(0, system["capacity"]) for system in self.systems]
        choice = choose_system(job, workload, self.systems, windows, [0] * 5)
        self.assertEqual(choice["system_id"], "tuolumne")
        self.assertEqual(choice["execution_mode"], "GPU")

    def test_large_job_excludes_smaller_machines(self):
        workload = self.workloads["cpu-solver"][0]
        job = {
            "job_id": "large-cpu",
            "num_nodes": 100,
            "duration": 80,
            "limit_time": 120,
        }
        windows = [window(0, system["capacity"]) for system in self.systems]
        choice = choose_system(job, workload, self.systems, windows, [0] * 5)
        self.assertIn(choice["system_id"], {"dane", "tuolumne"})

    def test_gpu_portable_uses_faster_mode_on_gpu_system(self):
        workload = self.workloads["portable-md"][0]
        system = self.systems[-1]
        choice = choose_system(
            {
                "job_id": "portable",
                "num_nodes": 16,
                "duration": 80,
                "limit_time": 120,
            },
            {**workload, "performance": [workload["performance"][-1]]},
            [system],
            [window(0, system["capacity"])],
            [0],
        )
        self.assertEqual(choice["execution_mode"], "GPU")
        self.assertEqual(choice["relative_performance"], 1.3)
        self.assertAlmostEqual(choice["estimated_duration"], 80 / 1.3)
        self.assertAlmostEqual(choice["adjusted_time_limit"], 120 / 1.3)

    def test_controller_uses_same_inputs_and_submits_both_scaled_times(self):
        args = SimpleNamespace(
            server=[f"server-{index}" for index in range(5)],
            jobs=_FIXTURES / "job_stream.csv",
            workload_table=_FIXTURES / "workload_table.csv",
            applications=_FIXTURES / "applications.csv",
            systems=_FIXTURES / "machines.csv",
            seed=19,
            server_infile=_FIXTURES / "job_stream.csv",
            backfill_policy="easy",
            priority_policy="fcfs",
            queue_impl="circular",
            prediction_utilization=1.0,
            session_name="test",
        )
        FakeSession.instances = []
        with patch.object(dispatch, "ServerSession", FakeSession):
            decisions, statistics, system_ids = dispatch.run_experiment(
                args, None, FakeMessages, None
            )

        self.assertEqual(len(decisions), 4)
        self.assertEqual(sum(stat.jobs_submitted for stat in statistics), 4)
        self.assertEqual(
            system_ids, [system["system_id"] for system in self.systems]
        )
        capacities = dict(zip(system_ids, (256, 64, 30, 256, 26)))
        for decision in decisions:
            self.assertLessEqual(
                decision["effective_nodes"], capacities[decision["system_id"]]
            )
        submitted = [
            job for session in FakeSession.instances for job in session.submitted
        ]
        self.assertEqual(len(submitted), 4)
        self.assertTrue(all(job.actual_run_time > 0 for job in submitted))
        self.assertTrue(
            all(job.actual_run_time <= job.limit_time for job in submitted)
        )
        self.assertTrue(
            all(session.run_time_mode == "actual" for session in FakeSession.instances)
        )


if __name__ == "__main__":
    unittest.main()
