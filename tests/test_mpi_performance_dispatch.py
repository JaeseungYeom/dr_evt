#!/usr/bin/env python3
"""End-to-end regression test for the native MPI multi-cluster dispatcher."""

import csv
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run_dispatch(command, output, expected_jobs, expected_dropped=0):
    result = subprocess.run(
        command + ["--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OMPI_MCA_rmaps_base_oversubscribe": "1",
            "OMPI_ALLOW_RUN_AS_ROOT": "1",
            "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    summaries = [
        line for line in result.stderr.splitlines() if "submitted=" in line
    ]
    assert len(summaries) == 5, result.stderr
    total = 0
    for line in summaries:
        match = re.search(r"submitted=(\d+) completed=(\d+)", line)
        assert match, line
        submitted, completed = map(int, match.groups())
        assert submitted == completed
        total += submitted
    assert total == expected_jobs
    overall_lines = [
        line for line in result.stderr.splitlines() if line.startswith("overall:")
    ]
    assert len(overall_lines) == 1, result.stderr
    metrics = {
        name: float(value)
        for name, value in re.findall(r"([a-z_]+)=([0-9.eE+-]+)", overall_lines[0])
    }
    assert int(metrics["jobs"]) == expected_jobs
    assert int(metrics["dropped_jobs"]) == expected_dropped
    truncation_warnings = [
        line
        for line in result.stderr.splitlines()
        if line.startswith("warning:") and "reason=exceeds_largest_system" in line
    ]
    assert len(truncation_warnings) == 7, result.stderr
    assert all(
        "requested_nodes=300 effective_nodes=256" in line
        for line in truncation_warnings
    )
    if expected_jobs:
        assert metrics["average_turnaround_time"] >= metrics["average_run_time"]
        assert metrics["average_bounded_slowdown"] >= 1.0
    return output.read_bytes(), metrics


def main():
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: test_mpi_performance_dispatch.py "
            "MPIEXEC NUMPROC_FLAG EXECUTABLE SOURCE_DIR"
        )

    mpiexec, numproc_flag, executable, source_dir = sys.argv[1:]
    if Path(mpiexec).name == "srun" and "SLURM_JOB_ID" not in os.environ:
        print("SKIP: srun requires an active Slurm allocation")
        return 77
    fixture_dir = Path(source_dir) / "experimental" / "multi-cluster" / "testdata"
    requirements = {
        "cpu-solver": "CPU-only",
        "gpu-trainer": "GPU-only",
        "portable-md": "GPU-portable",
    }
    system_sizes = {
        "dane": 256,
        "mammoth": 64,
        "matrix": 26,
        "tioga": 30,
        "tuolumne": 256,
    }
    gpu_systems = {"matrix", "tioga", "tuolumne"}

    with tempfile.TemporaryDirectory(prefix="dr-evt-multi-cluster-") as temp:
        temp_dir = Path(temp)
        jobs = temp_dir / "jobs.csv"
        with jobs.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "job_id",
                    "submit_time",
                    "num_nodes",
                    "time_limit",
                    "duration",
                )
            )
            node_counts = (1, 16, 26, 27, 30, 31, 64, 65, 300)
            for index in range(63):
                nodes = node_counts[index % len(node_counts)]
                writer.writerow((f"job-{index}", index * 10, nodes, 120, 80))

        command = [
            mpiexec,
            numproc_flag,
            "6",
            executable,
            "--jobs",
            str(jobs),
            "--ground-truth",
            str(fixture_dir / "ground_truth.csv"),
            "--prediction",
            str(fixture_dir / "prediction.csv"),
            "--applications",
            str(fixture_dir / "applications.csv"),
            "--systems",
            str(fixture_dir / "machines.csv"),
            "--seed",
            "19",
        ]
        first = temp_dir / "first.csv"
        second = temp_dir / "second.csv"
        expected_jobs = 63
        first_output, metrics = run_dispatch(command, first, expected_jobs)
        second_output, second_metrics = run_dispatch(command, second, expected_jobs)
        assert first_output == second_output
        assert metrics == second_metrics

        ipdps24_output = temp_dir / "ipdps24.csv"
        run_dispatch(
            command + ["--dispatch-policy", "IPDPS24"],
            ipdps24_output,
            expected_jobs,
        )

        actual_duration_output = temp_dir / "actual-duration.csv"
        run_dispatch(
            command + ["--wall-time-policy", "actual-duration"],
            actual_duration_output,
            expected_jobs,
        )
        with actual_duration_output.open(newline="") as stream:
            for row in csv.DictReader(stream):
                assert float(row["submitted_time_limit"]) == math.ceil(
                    float(row["actual_duration"])
                )
                assert int(row["time_limit_doublings"]) == 0

        with first.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == expected_jobs
        assert math.isclose(
            metrics["average_run_time"],
            sum(float(row["actual_duration"]) for row in rows) / expected_jobs,
            rel_tol=1e-7,
        )
        assert math.isclose(
            metrics["average_speedup"],
            sum(
                float(row["ground_truth_relative_performance"]) for row in rows
            )
            / expected_jobs,
            rel_tol=1e-7,
        )
        assert {row["App"] for row in rows} == set(requirements)
        for row in rows:
            original_nodes = int(row["num_nodes"])
            effective_nodes = int(row["effective_nodes"])
            assert effective_nodes == min(original_nodes, 256)
            assert system_sizes[row["system_id"]] >= effective_nodes
            requirement = requirements[row["App"]]
            gpu_system = row["system_id"] in gpu_systems
            mode = row["execution_mode"]
            if requirement == "GPU-only":
                assert gpu_system and mode == "GPU"
            elif not gpu_system:
                assert mode == "CPU"
            ground_truth = float(row["ground_truth_relative_performance"])
            predicted = float(row["predicted_relative_performance"])
            duration = float(row["duration"])
            limit = float(row["time_limit"])
            estimated = float(row["estimated_duration"])
            actual = float(row["actual_duration"])
            predicted_limit = float(row["predicted_time_limit"])
            actual_limit = float(row["actual_time_limit"])
            submitted_limit = float(row["submitted_time_limit"])
            turnaround = float(row["predicted_turnaround"])
            wait = float(row["estimated_wait"])
            assert abs(estimated - duration / predicted) < 1e-9
            assert abs(actual - duration / ground_truth) < 1e-9
            assert abs(predicted_limit - limit / predicted) < 1e-9
            assert abs(actual_limit - limit / ground_truth) < 1e-9
            assert submitted_limit >= actual
            assert int(row["time_limit_doublings"]) >= 0
            assert abs(turnaround - (wait + estimated)) < 1e-9

        allowed_applications = temp_dir / "allowed-applications.csv"
        with allowed_applications.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("app", "sys_requirement"))
            for app, requirement in requirements.items():
                if app != "gpu-trainer":
                    writer.writerow((app, requirement))
        allowlist_command = list(command)
        allowlist_command[allowlist_command.index("--applications") + 1] = str(
            allowed_applications
        )
        allowlist_output = temp_dir / "allowlist.csv"
        run_dispatch(allowlist_command, allowlist_output, expected_jobs)
        with allowlist_output.open(newline="") as stream:
            allowlist_rows = list(csv.DictReader(stream))
        assert {row["App"] for row in allowlist_rows} == {
            "cpu-solver",
            "portable-md",
        }

        dropped_output = temp_dir / "dropped.csv"
        dropped_command = command + ["--max-time-limit", "1"]
        dropped_bytes, dropped_metrics = run_dispatch(
            dropped_command, dropped_output, 0, expected_jobs
        )
        assert dropped_bytes.count(b"\n") == 1
        assert dropped_metrics["average_run_time"] == 0


if __name__ == "__main__":
    raise SystemExit(main())
