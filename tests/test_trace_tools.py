#!/usr/bin/env python3
"""Exact-output checks for the capacity-analysis and warm-start helpers."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def read_rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_warm_start(work):
    output = work / "warm.csv"
    result = run(
        ROOT / "scripts/prepare_warm_start_trace.py",
        ROOT / "tests/test_traces/tools/warm_start_history.csv",
        output,
        "--workload-trace",
        ROOT / "tests/test_traces/tools/warm_start_workload.csv",
        "--start-time",
        "10",
        "--total-nodes",
        "100",
    )
    assert "initialization_jobs=3\n" in result.stdout
    assert read_rows(output) == [
        {"job_submit_time": "10", "num_nodes": "3", "time_limit": "10",
         "actual_run_time": "10", "initialization": "1",
         "source_job_index": "1"},
        {"job_submit_time": "10", "num_nodes": "1", "time_limit": "1",
         "actual_run_time": "1", "initialization": "1",
         "source_job_index": "3"},
        # begin_time == start_time is deliberately included.
        {"job_submit_time": "10", "num_nodes": "5", "time_limit": "2",
         "actual_run_time": "2", "initialization": "1",
         "source_job_index": "4"},
        {"job_submit_time": "10", "num_nodes": "5", "time_limit": "2",
         "actual_run_time": "2", "initialization": "0",
         "source_job_index": "2"},
    ]


def test_capacity_detection(work):
    history = work / "history.csv"
    history.write_text(
        "job_submit_time,begin_time,end_time,num_nodes,q_id\n"
        "0,0,10,80,1\n"
        "0,20,30,10,1\n"
    )
    candidates = work / "candidates.csv"
    schedule = work / "capacity.csv"
    result = run(
        ROOT / "scripts/detect_capacity_periods.py",
        history,
        candidates,
        "--capacity-schedule",
        schedule,
        "--total-nodes",
        "100",
        "--utilization-threshold",
        "0.25",
        "--minimum-duration",
        "10",
        "--capacity-headroom",
        "1",
        "--queue-id",
        "1",
    )
    assert "candidate_periods=1\n" in result.stdout
    assert read_rows(candidates) == [{
        "start_time": "0", "end_time": "20", "duration_seconds": "20",
        "inferred_nodes": "80",
        "reason": "low_allocation_with_backlog+no_starts_with_backlog",
        "peak_allocated": "80", "peak_waiting_jobs": "1",
    }]
    assert read_rows(schedule) == [
        {"time": "0", "total_nodes": "80"},
        {"time": "20", "total_nodes": "100"},
    ]


def test_queue_pause_simulator_format(work):
    report = work / "maintenance.json"
    timeline = work / "timeline.csv"
    with_reduced = work / "with_reduced.csv"
    without_reduced = work / "without_reduced.csv"

    report.write_text(json.dumps({
        "normal_capacity": {"inferred_nodes": 100},
        "periods": [
            {"start": 3600, "end": 10800,
             "state": "queue_pause_or_maintenance"},
            {"start": 10800, "end": 14400, "state": "reduced_capacity",
             "potential_effective_capacity_lower_nodes": 60},
        ],
    }))
    timeline.write_text(
        "start,end\n"
        "0,3600\n"
        "3600,7200\n"
        "7200,10800\n"
        "10800,14400\n"
    )

    run(
        ROOT / "scripts/detect_queue_pause/build_resource_capacity_trace.py",
        "--report", report,
        "--timeline", timeline,
        "--capacity", "100",
        "--with-reduced-output", with_reduced,
        "--without-reduced-output", without_reduced,
        "--simulator-format",
    )

    assert read_rows(with_reduced) == [
        {"time": "0", "total_nodes": "100"},
        {"time": "3600", "total_nodes": "0"},
        {"time": "10800", "total_nodes": "60"},
        # The final inferred reduction must not persist past the timeline.
        {"time": "14400", "total_nodes": "100"},
    ]
    assert read_rows(without_reduced) == [
        {"time": "0", "total_nodes": "100"},
        {"time": "3600", "total_nodes": "0"},
        {"time": "10800", "total_nodes": "100"},
    ]

    # CTest supplies the built simulator so this verifies that the generated
    # file is accepted directly, without renaming columns or preprocessing.
    simulator = os.environ.get("DR_EVT_SIMULATOR")
    if simulator:
        jobs = work / "jobs.csv"
        jobs.write_text("job_submit_time,num_nodes,time_limit\n0,1,1\n")
        command = [
            simulator, str(jobs), "--total_nodes", "100",
            "--capacity_schedule", str(with_reduced),
            "--run_time_mode", "limit",
            "--outfile", str(work / "simulated.csv"),
            "--resource_trace", str(work / "resources.csv"),
        ]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix="dr_evt_trace_tools_") as tmp:
        work = Path(tmp)
        test_warm_start(work)
        test_capacity_detection(work)
        test_queue_pause_simulator_format(work)
    print("Trace tool tests passed")


if __name__ == "__main__":
    main()
