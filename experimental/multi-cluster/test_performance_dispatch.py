#!/usr/bin/env python3
"""Unit tests for the Python/gRPC multi-cluster dispatcher policy."""

import pathlib
import random
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import grpc_performance_dispatch as dispatch
import build_performance_tables as build_table
import build_prediction_baselines as baselines
import plot_relative_performance as performance_plot
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
            _FIXTURES / "ground_truth.csv",
            _FIXTURES / "prediction.csv",
            self.systems,
            requirements,
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
                "submit_time,num_nodes,time_limit\n0,1,10\n",
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

    def test_workload_requires_ground_truth_prediction_pairs(self):
        requirements = read_applications(_FIXTURES / "applications.csv")
        contents = (_FIXTURES / "prediction.csv").read_text(encoding="utf-8")
        contents = contents.replace("0.9,1.3", ",1.3", 1)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "prediction.csv"
            path.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete dane pair"):
                read_workloads(
                    _FIXTURES / "ground_truth.csv", path, self.systems, requirements
                )

    def test_workload_builder_normalizes_quoted_comma_arguments(self):
        identity = build_table.normalized_identity(
            {
                "app": "Kripke.exe",
                "args": "--niter 10 --procs 4, 4, 2",
                "rank": "32",
            },
            "rank",
        )
        self.assertEqual(
            identity, ("kripke.exe", "--niter10--procs4,4,2", 32)
        )

    def test_workload_builder_uses_one_reference_for_actual_and_prediction(self):
        identity = ("solver", "--size8", 4)
        predictions = {
            identity: [
                {
                    "app": "solver",
                    "args": "--size8",
                    "ranks": "4",
                    "borax": "1",
                    "dane": "2",
                    "mammoth": "4",
                }
            ]
        }
        measurements = {
            identity: {"borax": 50.0, "dane": 100.0, "mammoth": 25.0}
        }
        ground_truth, predicted, skipped = build_table.build_rows(
            predictions,
            measurements,
            {"solver": "CPU-only"},
            ["dane", "mammoth"],
            ["borax", "dane", "mammoth"],
        )
        self.assertEqual(skipped, 0)
        self.assertEqual(float(ground_truth[0]["dane"]), 0.5)
        self.assertEqual(float(predicted[0]["dane"]), 2.0)
        self.assertEqual(float(ground_truth[0]["mammoth"]), 2.0)
        self.assertEqual(float(predicted[0]["mammoth"]), 4.0)

    def test_plot_system_alias_and_point_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = pathlib.Path(directory) / "actual.csv"
            predicted = pathlib.Path(directory) / "predicted.csv"
            actual.write_text(
                "App,Args,Ranks,tuolumne-gpu\n"
                'solver,"--grid 4, 4",8,1.25\n',
                encoding="utf-8",
            )
            predicted.write_text(
                "App,Args,Ranks,tuolumne-gpu\n"
                'solver,"--grid 4, 4",8,1.5\n', encoding="utf-8"
            )
            points = performance_plot.load_points(actual, predicted, ["tuolumne"])
        self.assertEqual(points, {"tuolumne-gpu": [(1.25, 1.5, "solver")]})

    def test_application_average_baseline_groups_by_app_rank_and_mode(self):
        rows = [
            {"App": "a", "Args": "x", "Ranks": "4", "dane": "1"},
            {"App": "a", "Args": "y", "Ranks": "4", "dane": "3"},
            {"App": "a", "Args": "z", "Ranks": "8", "dane": "9"},
        ]
        predicted = baselines.application_average_rows(rows, ["dane"])
        self.assertEqual([float(row["dane"]) for row in predicted], [2, 2, 9])

    def test_rajaperf_baseline_preserves_duplicate_kernel_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "machine_rep.txt"
            path.write_text(
                "machine,kernel,kernel\nborax,4,12\ndane,2,3\n",
                encoding="utf-8",
            )
            values = baselines.rajaperf_speedups(path, ["dane"])
        self.assertEqual(values["dane"], 3.0)

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

    def test_dispatch_respects_each_machine_capacity_boundary(self):
        workload = {
            **self.workloads["cpu-solver"][0],
            "performance": [
                {"CPU": {"ground_truth": 1.0, "predicted": 1.0}, "GPU": None},
                {"CPU": {"ground_truth": 2.0, "predicted": 2.0}, "GPU": None},
                {"CPU": {"ground_truth": 3.0, "predicted": 3.0}, "GPU": None},
                {"CPU": {"ground_truth": 0.9, "predicted": 0.9}, "GPU": None},
                {"CPU": {"ground_truth": 0.8, "predicted": 0.8}, "GPU": None},
            ],
        }
        windows = [window(0, system["capacity"]) for system in self.systems]

        def selected_system(nodes):
            job = {
                "job_id": f"capacity-{nodes}",
                "num_nodes": nodes,
                "duration": 80,
                "limit_time": 1000,
            }
            return choose_system(
                job, workload, self.systems, windows, [0] * len(self.systems)
            )["system_id"]

        self.assertEqual(selected_system(30), "tioga")
        self.assertEqual(selected_system(31), "mammoth")
        self.assertEqual(selected_system(64), "mammoth")
        self.assertEqual(selected_system(65), "dane")

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
        self.assertEqual(choice["ground_truth_relative_performance"], 1.3)
        self.assertEqual(choice["predicted_relative_performance"], 1.5)
        self.assertAlmostEqual(choice["estimated_duration"], 80 / 1.5)
        self.assertAlmostEqual(choice["actual_duration"], 80 / 1.3)
        self.assertAlmostEqual(choice["predicted_time_limit"], 120 / 1.5)
        self.assertAlmostEqual(choice["actual_time_limit"], 120 / 1.3)

    def test_prediction_drives_choice_but_ground_truth_drives_runtime(self):
        systems = self.systems[:2]
        workload = {
            **self.workloads["cpu-solver"][0],
            "performance": [
                {"CPU": {"ground_truth": 2.0, "predicted": 1.0}, "GPU": None},
                {"CPU": {"ground_truth": 0.5, "predicted": 4.0}, "GPU": None},
            ],
        }
        choice = choose_system(
            {
                "job_id": "prediction",
                "num_nodes": 8,
                "duration": 80,
                "limit_time": 1000,
            },
            workload,
            systems,
            [window(0, system["capacity"]) for system in systems],
            [0, 0],
        )
        self.assertEqual(choice["system_id"], "mammoth")
        self.assertEqual(choice["estimated_duration"], 20)
        self.assertEqual(choice["actual_duration"], 160)

    def test_controller_uses_same_inputs_and_submits_both_scaled_times(self):
        args = SimpleNamespace(
            server=[f"server-{index}" for index in range(5)],
            jobs=_FIXTURES / "job_stream.csv",
            ground_truth=_FIXTURES / "ground_truth.csv",
            prediction=_FIXTURES / "prediction.csv",
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
        self.assertCountEqual(
            [job.actual_run_time for job in submitted],
            [decision["actual_duration"] for decision in decisions],
        )
        self.assertCountEqual(
            [job.limit_time for job in submitted],
            [decision["actual_time_limit"] for decision in decisions],
        )
        for decision in decisions:
            self.assertAlmostEqual(
                decision["actual_duration"],
                decision["duration"]
                / decision["ground_truth_relative_performance"],
            )
            self.assertAlmostEqual(
                decision["estimated_duration"],
                decision["duration"] / decision["predicted_relative_performance"],
            )
            self.assertAlmostEqual(
                decision["actual_time_limit"],
                decision["time_limit"]
                / decision["ground_truth_relative_performance"],
            )
        self.assertTrue(
            all(job.actual_run_time <= job.limit_time for job in submitted)
        )
        self.assertTrue(
            all(session.run_time_mode == "actual" for session in FakeSession.instances)
        )


if __name__ == "__main__":
    unittest.main()
