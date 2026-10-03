#!/usr/bin/env python3
"""End-to-end regression test for the native MPI multi-cluster dispatcher."""

import csv
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run_dispatch(command, output):
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
    assert total == 60
    return output.read_bytes()


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
    fixture_dir = Path(source_dir) / "experimental" / "multi-cluster"
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
                    "job_submit_time",
                    "num_nodes",
                    "time_limit",
                    "actual_run_time",
                )
            )
            for index in range(60):
                nodes = (1, 16, 26, 27, 64, 300)[index % 6]
                writer.writerow((f"job-{index}", index * 10, nodes, 120, 80))

        command = [
            mpiexec,
            numproc_flag,
            "6",
            executable,
            "--jobs",
            str(jobs),
            "--workload-table",
            str(fixture_dir / "workload_table.csv"),
            "--applications",
            str(fixture_dir / "applications.csv"),
            "--systems",
            str(fixture_dir / "machines.csv"),
            "--seed",
            "19",
        ]
        first = temp_dir / "first.csv"
        second = temp_dir / "second.csv"
        assert run_dispatch(command, first) == run_dispatch(command, second)

        with first.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 60
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
            performance = float(row["relative_performance"])
            duration = float(row["duration"])
            limit = float(row["time_limit"])
            estimated = float(row["estimated_duration"])
            adjusted_limit = float(row["adjusted_time_limit"])
            turnaround = float(row["predicted_turnaround"])
            wait = float(row["estimated_wait"])
            assert abs(estimated - duration / performance) < 1e-9
            assert abs(adjusted_limit - limit / performance) < 1e-9
            assert abs(turnaround - (wait + estimated)) < 1e-9


if __name__ == "__main__":
    raise SystemExit(main())
