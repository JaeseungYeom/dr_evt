#!/usr/bin/env python3
"""Dispatch an arrival stream to independent DR_EVT systems by turnaround.

The script is intended to be used as the rank-0 controller of
``grpc_mpi_launcher.py``.  Each non-root MPI rank owns one DR_EVT server.  For
every arrival, the controller finds the nearest performance-table profile,
queries every server's projected resource releases, and minimizes

    predicted turnaround = predicted wait + time_limit / relative_performance

The selected server receives the job through AppendJobs and all servers stay
at the common arrival-time watermark through AdvanceTo.
"""

import argparse
import csv
import math
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

# This experiment reuses the repository's general gRPC client helpers without
# making the experimental controller part of the Python package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "python"))

from grpc_multi_server import (DEFAULT_QUEUE_INPUT, QUEUE_FIELD, ServerSession,
                               load_stubs)


REQUIRED_JOB_FIELDS = {"job_submit_time", "num_nodes", "time_limit"}
PROFILE_FIELDS = {"profile_id", "num_nodes", "time_limit"}


def read_arrivals(path):
    """Read and validate a chronologically ordered simple-format trace."""
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED_JOB_FIELDS.issubset(reader.fieldnames):
            raise ValueError(
                f"{path} must have columns: {', '.join(sorted(REQUIRED_JOB_FIELDS))}"
            )
        jobs = []
        for index, row in enumerate(reader):
            job = {
                "job_id": (row.get("job_id") or str(index)).strip(),
                "submit_time": float(row["job_submit_time"]),
                "num_nodes": int(row["num_nodes"]),
                "queue": (row.get(QUEUE_FIELD) or DEFAULT_QUEUE_INPUT).strip(),
                "limit_time": float(row["time_limit"]),
            }
            if job["num_nodes"] <= 0 or job["limit_time"] <= 0:
                raise ValueError(f"{path}: job {job['job_id']} has non-positive size")
            jobs.append(job)
    if any(a["submit_time"] > b["submit_time"] for a, b in zip(jobs, jobs[1:])):
        raise ValueError(f"{path}: jobs must be sorted by job_submit_time")
    return jobs


def read_performance_table(path, system_ids):
    """Read wide profiles whose system columns contain positive speedups."""
    required = PROFILE_FIELDS | set(system_ids)
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} must have columns: {', '.join(sorted(required))}")
        profiles = []
        for row in reader:
            profile = {
                "profile_id": row["profile_id"].strip(),
                "num_nodes": float(row["num_nodes"]),
                "time_limit": float(row["time_limit"]),
                "performance": {name: float(row[name]) for name in system_ids},
            }
            if not profile["profile_id"]:
                raise ValueError(f"{path}: profile_id must not be empty")
            if profile["num_nodes"] <= 0 or profile["time_limit"] <= 0:
                raise ValueError(f"{path}: profile {profile['profile_id']} has non-positive size")
            if any(value <= 0 or not math.isfinite(value)
                   for value in profile["performance"].values()):
                raise ValueError(
                    f"{path}: profile {profile['profile_id']} has invalid performance"
                )
            profiles.append(profile)
    if not profiles:
        raise ValueError(f"{path}: performance table is empty")
    return profiles


def nearest_profile(job, profiles):
    """Return nearest profile using range-normalized Euclidean distance."""
    node_values = [profile["num_nodes"] for profile in profiles]
    time_values = [profile["time_limit"] for profile in profiles]
    node_scale = max(node_values) - min(node_values) or 1.0
    time_scale = max(time_values) - min(time_values) or 1.0

    def distance(profile):
        node_delta = (job["num_nodes"] - profile["num_nodes"]) / node_scale
        time_delta = (job["limit_time"] - profile["time_limit"]) / time_scale
        return node_delta * node_delta + time_delta * time_delta

    return min(profiles, key=distance)


def estimate_release_wait(window, required_nodes):
    """Return the first projected release with sufficient capacity."""
    available = window.available_nodes
    if available >= required_nodes:
        return 0.0
    for release in window.releases:
        available += release.nodes_released
        if available >= required_nodes:
            return max(0.0, release.time - window.current_time)
    return math.inf


def estimate_wait(window, required_nodes, runtime, prediction_horizon):
    """Estimate wait from immediate EASY fit or queued resource-time."""
    has_waiting_head = window.shadow_time > window.current_time
    can_start_now = window.available_nodes >= required_nodes
    can_backfill_now = can_start_now and (
        not has_waiting_head
        or window.current_time + runtime < window.shadow_time
    )
    if can_backfill_now:
        return 0.0
    if not has_waiting_head:
        return estimate_release_wait(window, required_nodes)
    return window.shadow_time - window.current_time + prediction_horizon


def choose_system(job, profile, system_ids, capacities, windows, horizons):
    """Choose the feasible system with minimum predicted turnaround."""
    candidates = []
    for index, (system_id, capacity, window, horizon) in enumerate(
            zip(system_ids, capacities, windows, horizons)):
        performance = profile["performance"][system_id]
        runtime = job["limit_time"] / performance
        wait = estimate_wait(window, job["num_nodes"], runtime, horizon)
        if job["num_nodes"] > capacity:
            wait = math.inf
        candidates.append({
            "index": index,
            "system_id": system_id,
            "relative_performance": performance,
            "estimated_wait": wait,
            "estimated_runtime": runtime,
            "predicted_turnaround": wait + runtime,
        })
    feasible = [candidate for candidate in candidates
                if math.isfinite(candidate["predicted_turnaround"])]
    if not feasible:
        raise ValueError(
            f"job {job['job_id']} ({job['num_nodes']} nodes) cannot fit any system"
        )
    return min(feasible, key=lambda item: (
        item["predicted_turnaround"], item["estimated_wait"], item["index"]))


def call_all(executor, sessions, make_request):
    futures = [executor.submit(session.call, make_request(session.messages))
               for session in sessions]
    return [future.result() for future in futures]


def run_experiment(args, grpc, pb, service):
    system_ids = args.system_id or [f"system-{index + 1}"
                                    for index in range(len(args.server))]
    if len(system_ids) != len(args.server) or len(set(system_ids)) != len(system_ids):
        raise ValueError("--system-id must be unique and repeated once per --server")
    capacities = args.system_nodes or [args.total_nodes] * len(args.server)
    if len(capacities) != len(args.server) or any(value <= 0 for value in capacities):
        raise ValueError("--system-nodes must be positive and repeated once per --server")

    jobs = read_arrivals(args.jobs)
    profiles = read_performance_table(args.performance_table, system_ids)
    sessions = [ServerSession(address, grpc, pb, service) for address in args.server]
    decisions = []
    try:
        with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
            init_futures = []
            for index, (session, capacity) in enumerate(zip(sessions, capacities)):
                request = pb.ClientMessage(init=pb.InitRequest(
                    total_nodes=capacity,
                    trace_format="simple",
                    timestamp_format="epoch",
                    backfill_policy=args.backfill_policy,
                    priority_policy=args.priority_policy,
                    run_time_mode="limit",
                    infile=str(args.server_infile),
                    queue_impl=args.queue_impl,
                    session_name=f"{args.session_name}-{system_ids[index]}",
                ))
                init_futures.append(executor.submit(session.call, request))
            for future in init_futures:
                future.result()

            for job in jobs:
                call_all(executor, sessions, lambda messages: messages.ClientMessage(
                    advance_to=messages.AdvanceToRequest(target_time=job["submit_time"])))
                responses = call_all(
                    executor, sessions, lambda messages: messages.ClientMessage(
                        get_backfill_window=messages.GetBackfillWindowRequest()))
                windows = [response.get_backfill_window for response in responses]
                horizon_responses = call_all(
                    executor, sessions, lambda messages: messages.ClientMessage(
                        get_prediction_horizon=messages.GetPredictionHorizonRequest(
                            utilization=args.prediction_utilization)))
                horizons = [response.get_prediction_horizon.horizon
                            for response in horizon_responses]
                profile = nearest_profile(job, profiles)
                choice = choose_system(
                    job, profile, system_ids, capacities, windows, horizons)
                adjusted_runtime = choice["estimated_runtime"]
                append = pb.AppendJobsRequest(requests=[pb.JobAppendData(
                    submit_time=job["submit_time"], num_nodes=job["num_nodes"],
                    queue=job["queue"], limit_time=adjusted_runtime)])
                response = sessions[choice["index"]].call(
                    pb.ClientMessage(append_jobs=append))
                sessions[choice["index"]].call(pb.ClientMessage(
                    advance_to=pb.AdvanceToRequest(target_time=job["submit_time"])))
                decisions.append({
                    "job_id": job["job_id"],
                    "submit_time": job["submit_time"],
                    "profile_id": profile["profile_id"],
                    **{key: choice[key] for key in (
                        "system_id", "relative_performance", "estimated_wait",
                        "estimated_runtime", "predicted_turnaround")},
                    "job_idx": response.append_jobs.job_idx[0],
                })

            finish_responses = call_all(
                executor, sessions, lambda messages: messages.ClientMessage(
                    finish_simulation=messages.FinishSimulationRequest()))
        return decisions, [response.finish_simulation.statistics
                           for response in finish_responses], system_ids
    finally:
        for session in sessions:
            session.close()


def write_results(stream, decisions):
    fields = ("job_id", "submit_time", "profile_id", "system_id",
              "relative_performance", "estimated_wait", "estimated_runtime",
              "predicted_turnaround", "job_idx")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(decisions)


def write_summary(stream, statistics, system_ids):
    for system_id, stats in zip(system_ids, statistics):
        print(f"{system_id}: submitted={stats.jobs_submitted} "
              f"completed={stats.jobs_completed} makespan={stats.makespan:.6g}",
              file=stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="append", required=True)
    parser.add_argument("--system-id", action="append",
                        help="performance-table column; repeat in --server order")
    parser.add_argument("--system-nodes", action="append", type=int,
                        help="node capacity; repeat in --server order")
    parser.add_argument("--jobs", required=True, type=pathlib.Path)
    parser.add_argument("--performance-table", required=True, type=pathlib.Path)
    parser.add_argument("--server-infile", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path,
                        help="decision CSV (default: stdout)")
    parser.add_argument("--total-nodes", type=int, default=100)
    parser.add_argument("--backfill-policy", default="easy")
    parser.add_argument("--priority-policy", default="fcfs")
    parser.add_argument("--queue-impl", default="circular")
    parser.add_argument("--prediction-utilization", type=float, default=1.0,
                        help="usable-capacity factor for queue horizon (0..1)")
    parser.add_argument("--session-name", default="performance-dispatch")
    args = parser.parse_args()
    if not 0.0 <= args.prediction_utilization <= 1.0:
        parser.error("--prediction-utilization must be in [0, 1]")
    if args.backfill_policy.lower() != "easy":
        parser.error("performance dispatch requires --backfill-policy easy")
    if args.priority_policy.lower() not in {"fcfs", "fcfs_alt"}:
        parser.error("performance dispatch requires an FCFS priority policy")
    args.server_infile = args.server_infile or args.jobs

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    grpc, pb, service, generated_dir = load_stubs(repo_root)
    try:
        decisions, statistics, system_ids = run_experiment(
            args, grpc, pb, service)
        if args.output:
            with args.output.open("w", newline="") as stream:
                write_results(stream, decisions)
        else:
            write_results(sys.stdout, decisions)
        write_summary(sys.stderr, statistics, system_ids)
    finally:
        generated_dir.cleanup()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
