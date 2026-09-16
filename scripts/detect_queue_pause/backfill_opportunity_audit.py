#!/usr/bin/env python3
"""Replay observed traces and find conservative unused EASY opportunities.

This adapts the reservation and backfill tests from the DR_EVT Python reference
scheduler.  It does not reschedule the trace.  At each sampled timestamp it
reconstructs the jobs actually running and waiting, then records jobs that the
reference EASY decision would have started but that remained waiting beyond a
configurable grace period.
"""

import argparse
import bisect
import csv
import heapq
import math
import os
import sys
from collections import Counter

from discover_maintenance import TimestampFormatter, expand_paths, filename_month_window


class Job:
    __slots__ = (
        "idx", "submit", "begin", "end", "nodes", "limit", "family",
    )

    def __init__(self, idx, submit, begin, end, nodes, limit_seconds, family):
        self.idx = idx
        self.submit = submit
        self.begin = begin
        self.end = end
        self.nodes = nodes
        self.limit = limit_seconds
        self.family = family


class FenwickTree:
    """Dynamic prefix sums over compressed pessimistic release times."""

    def __init__(self, size):
        self.values = [0] * (size + 1)

    def add(self, index, amount):
        index += 1
        while index < len(self.values):
            self.values[index] += amount
            index += index & -index

    def prefix(self, end):
        total = 0
        while end:
            total += self.values[end]
            end -= end & -end
        return total

    def lower_bound(self, target):
        """Return the first zero-based index whose prefix reaches target."""
        if target <= 0:
            return 0
        index = 0
        step = 1 << (len(self.values).bit_length() - 1)
        while step:
            candidate = index + step
            if candidate < len(self.values) and self.values[candidate] < target:
                index = candidate
                target -= self.values[candidate]
            step >>= 1
        return index if index < len(self.values) - 1 else None


def family_key(row, names, nodes, limit_seconds):
    """Build a conservative workload-family fingerprint.

    Power per node helps avoid treating all jobs with the same common resource
    shape as one recurring workload.  It is used only as a retrospective
    fingerprint, never for backfill feasibility.
    """
    power_bucket = None
    power_index = names.get("avgpcon")
    if power_index is not None and power_index < len(row):
        try:
            power_per_node = float(row[power_index]) / nodes
            power_bucket = int(round(power_per_node / 5.0) * 5)
        except (ValueError, ZeroDivisionError):
            pass
    return nodes, limit_seconds, power_bucket


def recurring_families(jobs, minimum_occurrences, maximum_gap_cv):
    """Find regularly submitted families using inter-arrival consistency."""
    submissions = {}
    for job in jobs:
        submissions.setdefault(job.family, []).append(job.submit)
    recurring = set()
    for family, times in submissions.items():
        if len(times) < minimum_occurrences:
            continue
        times.sort()
        gaps = [right - left for left, right in zip(times, times[1:]) if right > left]
        if len(gaps) < minimum_occurrences - 1:
            continue
        mean = sum(gaps) / float(len(gaps))
        if mean < 1800:
            continue
        variance = sum((gap - mean) ** 2 for gap in gaps) / len(gaps)
        coefficient = math.sqrt(variance) / mean
        if coefficient <= maximum_gap_cv:
            recurring.add(family)
    return recurring


def load_jobs(path, window, release_delay_seconds=0):
    jobs = []
    with open(path, "r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        names = {name.strip(): index for index, name in enumerate(header)}
        required = ("job_submit_time", "begin_time", "end_time", "num_nodes", "time_limit")
        missing = [name for name in required if name not in names]
        if missing:
            raise ValueError("{}: missing columns: {}".format(path, ", ".join(missing)))
        for index, row in enumerate(reader):
            try:
                submit = int(float(row[names["job_submit_time"]]))
                begin = int(float(row[names["begin_time"]]))
                end = int(float(row[names["end_time"]]))
                nodes = int(float(row[names["num_nodes"]]))
                limit_seconds = int(float(row[names["time_limit"]]))
            except (ValueError, IndexError, OverflowError):
                continue
            if nodes <= 0 or limit_seconds <= 0 or begin < submit or end < begin:
                continue
            # Keep jobs that are pending or running at some point in this file's
            # ownership window. Completely external spillover is duplicated
            # coverage and must not affect the replay.
            if submit >= window[1] or end + release_delay_seconds <= window[0]:
                continue
            jobs.append(
                Job(
                    index, submit, begin, end, nodes, limit_seconds,
                    family_key(row, names, nodes, limit_seconds),
                )
            )
    jobs.sort(key=lambda job: (job.submit, job.idx))
    return jobs


def reservation_time(
    now, needed_nodes, free_nodes, release_tree, release_times, virtual_jobs,
    release_delay_seconds=0,
):
    required_release = needed_nodes - free_nodes
    if required_release <= 0:
        return now
    virtual_releases = sorted(
        (now + job.limit + release_delay_seconds, job.nodes)
        for job in virtual_jobs
    )
    virtual_freed = 0
    for release, nodes in virtual_releases:
        active_needed = required_release - virtual_freed
        active_index = release_tree.lower_bound(active_needed)
        if active_index is not None and release_times[active_index] <= release:
            return release_times[active_index]
        virtual_freed += nodes
        active_through_release = release_tree.prefix(
            bisect.bisect_right(release_times, release)
        )
        if active_through_release + virtual_freed >= required_release:
            return release
    active_index = release_tree.lower_bound(required_release - virtual_freed)
    return None if active_index is None else release_times[active_index]


def has_successful_size_control(job, successful_controls):
    """Return whether an equal/larger pending job starts within the grace window.

    Such a start demonstrates that at least the candidate's node count could be
    dispatched contemporaneously.  It does not prove equivalent queue or
    project eligibility because those fields are absent from the trace.
    """
    return any(control.nodes >= job.nodes for control in successful_controls)


def note_miss(
    job, now, grace_seconds, metrics, missed_records, kind, successful_controls,
):
    if job.begin <= now + grace_seconds:
        return
    if has_successful_size_control(job, successful_controls):
        metrics["size_controlled_{}_jobs".format(kind)] += 1
        metrics["size_controlled_opportunity_jobs"] += 1
        return
    metrics["missed_{}_jobs".format(kind)] += 1
    metrics["missed_opportunity_jobs"] += 1
    missed_records.append((job, kind))


def replay_file(
    path, window, target_times, capacity, grace_seconds,
    recurrence_minimum, recurrence_cv, habitual_fraction, scan_limit,
    release_delay_seconds=0,
):
    jobs = load_jobs(path, window, release_delay_seconds)
    recurring = recurring_families(jobs, recurrence_minimum, recurrence_cv)
    start_order = sorted(range(len(jobs)), key=lambda index: (jobs[index].begin, index))
    release_times = sorted(set(
        max(job.begin + job.limit, job.end) + release_delay_seconds for job in jobs
    ))
    release_indices = {value: index for index, value in enumerate(release_times)}
    release_tree = FenwickTree(len(release_times))
    unavailable = {}
    unavailable_end_heap = []
    running = {}
    running_end_heap = []
    start_cursor = 0
    submit_cursor = 0
    head_cursor = 0
    used_nodes = 0
    results = {}
    missed_by_time = {}
    opportunities_by_time = {}
    source_id = os.path.basename(path).replace("_scheduling_trace.csv", "")
    ordered_begin_times = [jobs[index].begin for index in start_order]

    for now in target_times:
        while start_cursor < len(start_order) and jobs[start_order[start_cursor]].begin <= now:
            job = jobs[start_order[start_cursor]]
            if job.end > now:
                running[job.idx] = job
                heapq.heappush(running_end_heap, (job.end, job.idx))
            if job.end + release_delay_seconds > now:
                unavailable[job.idx] = job
                heapq.heappush(
                    unavailable_end_heap,
                    (job.end + release_delay_seconds, job.idx),
                )
                release_tree.add(
                    release_indices[
                        max(job.begin + job.limit, job.end) + release_delay_seconds
                    ],
                    job.nodes,
                )
                used_nodes += job.nodes
            start_cursor += 1
        while running_end_heap and running_end_heap[0][0] <= now:
            _, index = heapq.heappop(running_end_heap)
            running.pop(index, None)
        while unavailable_end_heap and unavailable_end_heap[0][0] <= now:
            _, index = heapq.heappop(unavailable_end_heap)
            job = unavailable.pop(index, None)
            if job is not None:
                release_tree.add(
                    release_indices[
                        max(job.begin + job.limit, job.end) + release_delay_seconds
                    ],
                    -job.nodes,
                )
                used_nodes -= job.nodes
        while submit_cursor < len(jobs) and jobs[submit_cursor].submit <= now:
            submit_cursor += 1
        while head_cursor < submit_cursor and jobs[head_cursor].begin <= now:
            head_cursor += 1

        free_nodes = max(0, capacity - used_nodes)
        metrics = Counter()
        metrics["grace_seconds"] = grace_seconds
        metrics["release_delay_seconds"] = release_delay_seconds
        metrics["successful_size_control_enabled"] = 1
        observed_running_nodes = sum(job.nodes for job in running.values())
        metrics["observed_running_nodes"] = observed_running_nodes
        metrics["reclaiming_nodes"] = used_nodes - observed_running_nodes
        metrics["unavailable_nodes"] = used_nodes
        metrics["observed_free_nodes"] = free_nodes
        missed_records = []
        opportunity_records = []
        virtual_jobs = []
        cursor = head_cursor
        examined = 0
        control_begin = bisect.bisect_right(ordered_begin_times, now)
        control_end = bisect.bisect_right(
            ordered_begin_times, now + grace_seconds
        )
        successful_controls = [
            jobs[start_order[index]]
            for index in range(control_begin, control_end)
            if jobs[start_order[index]].submit <= now
        ]

        # Reference EASY step 1: start consecutive FCFS heads while they fit.
        blocked_cursor = None
        while cursor < submit_cursor:
            job = jobs[cursor]
            cursor += 1
            if job.begin <= now:
                continue
            examined += 1
            if job.nodes <= free_nodes:
                metrics["direct_opportunity_jobs"] += 1
                opportunity_records.append((job, "direct"))
                note_miss(
                    job, now, grace_seconds, metrics, missed_records, "direct",
                    successful_controls,
                )
                free_nodes -= job.nodes
                virtual_jobs.append(job)
            else:
                blocked_cursor = cursor - 1
                break

        # Reference EASY steps 2/3: reserve for the blocked head, then scan
        # later jobs for safe backfill candidates.
        reservation = None
        if blocked_cursor is not None:
            head = jobs[blocked_cursor]
            reservation = reservation_time(
                now, head.nodes, free_nodes, release_tree, release_times,
                virtual_jobs, release_delay_seconds,
            )
            cursor = blocked_cursor + 1
            while cursor < submit_cursor and examined < scan_limit:
                job = jobs[cursor]
                cursor += 1
                if job.begin <= now:
                    continue
                examined += 1
                if (
                    reservation is not None
                    and job.nodes <= free_nodes
                    and now + job.limit + release_delay_seconds < reservation
                ):
                    metrics["backfill_opportunity_jobs"] += 1
                    opportunity_records.append((job, "backfill"))
                    note_miss(
                        job, now, grace_seconds, metrics, missed_records, "backfill",
                        successful_controls,
                    )
                    free_nodes -= job.nodes

        metrics["queue_jobs_examined"] = examined
        metrics["queue_scan_truncated"] = int(examined >= scan_limit and cursor < submit_cursor)
        metrics["reservation_time"] = "" if reservation is None else reservation
        results[now] = metrics
        missed_by_time[now] = missed_records
        opportunities_by_time[now] = opportunity_records

    # A repeated family is treated as policy/hold-like only when distinct jobs
    # from that family habitually show the same missed-opportunity behavior.
    # This is deliberately learned after replay so mere use of a common job
    # shape does not by itself suppress evidence.
    family_totals = Counter(job.family for job in jobs)
    family_missed_jobs = {}
    for records in missed_by_time.values():
        for job, _ in records:
            family_missed_jobs.setdefault(job.family, set()).add(job.idx)
    habitual = set()
    for family, indices in family_missed_jobs.items():
        total = family_totals[family]
        if (
            total >= recurrence_minimum
            and len(indices) >= recurrence_minimum
            and len(indices) / float(total) >= habitual_fraction
        ):
            habitual.add(family)
    excluded_families = recurring | habitual

    for now, records in missed_by_time.items():
        metrics = results[now]
        families = set()
        families_by_kind = {"direct": set(), "backfill": set()}
        nonrecurring_nodes = []
        nonrecurring_backfill_nodes = []
        opportunity_ids = {"backfill": set()}
        missed_ids = {"backfill": set()}
        nonrecurring_missed_ids = {"backfill": set()}
        for job, kind in opportunities_by_time[now]:
            if kind == "backfill":
                opportunity_ids[kind].add("{}:{}".format(source_id, job.idx))
        for job, kind in records:
            identifier = "{}:{}".format(source_id, job.idx)
            if kind == "backfill":
                missed_ids[kind].add(identifier)
            if job.family in excluded_families:
                continue
            if kind == "backfill":
                nonrecurring_missed_ids[kind].add(identifier)
            metrics["missed_nonrecurring_{}_jobs".format(kind)] += 1
            metrics["missed_nonrecurring_jobs"] += 1
            families.add(job.family)
            families_by_kind[kind].add(job.family)
            nonrecurring_nodes.append(job.nodes)
            if kind == "backfill":
                nonrecurring_backfill_nodes.append(job.nodes)
        metrics["missed_nonrecurring_families"] = len(families)
        metrics["missed_nonrecurring_direct_families"] = len(
            families_by_kind["direct"]
        )
        metrics["missed_nonrecurring_backfill_families"] = len(
            families_by_kind["backfill"]
        )
        metrics["min_missed_nonrecurring_nodes"] = (
            min(nonrecurring_nodes) if nonrecurring_nodes else 0
        )
        metrics["min_missed_nonrecurring_backfill_nodes"] = (
            min(nonrecurring_backfill_nodes) if nonrecurring_backfill_nodes else 0
        )
        metrics["recurring_family_count"] = len(recurring)
        metrics["habitually_missed_family_count"] = len(habitual)
        metrics["backfill_opportunity_job_ids"] = ";".join(
            sorted(opportunity_ids["backfill"])
        )
        metrics["missed_backfill_job_ids"] = ";".join(
            sorted(missed_ids["backfill"])
        )
        metrics["missed_nonrecurring_backfill_job_ids"] = ";".join(
            sorted(nonrecurring_missed_ids["backfill"])
        )
    return results


def read_target_bins(path, capacity, threshold):
    targets = []
    with open(path, newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            running = float(row["running_nodes"])
            pending_jobs = float(row["pending_jobs"])
            pending_nodes = float(row["pending_nodes"])
            if (
                running < capacity * threshold
                and (pending_jobs >= 10 or pending_nodes >= capacity * 0.02)
            ):
                targets.append(int(row["start"]))
    return targets


def parse_args():
    parser = argparse.ArgumentParser(
        description="Replay observed scheduling state and audit unused EASY opportunities"
    )
    parser.add_argument("inputs", nargs="+", help="YY_MM scheduling trace paths/globs")
    parser.add_argument("--timeline", default="capacity_timeline.csv")
    parser.add_argument("--output", default="backfill_opportunities.csv")
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--candidate-fraction", type=float, default=0.85)
    parser.add_argument("--grace-minutes", type=int, default=60)
    parser.add_argument(
        "--release-delay-minutes", type=int, default=10,
        help="post-end node reclaim delay used by the replay (default: 10)",
    )
    parser.add_argument("--recurring-minimum", type=int, default=3)
    parser.add_argument("--recurring-gap-cv", type=float, default=0.20)
    parser.add_argument(
        "--habitual-miss-fraction", type=float, default=0.50,
        help="fraction of a repeated family's jobs required to discount it (default: 0.50)",
    )
    parser.add_argument(
        "--scan-limit", type=int, default=10000,
        help="maximum queued jobs examined per sampled hour (default: 10000)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.nodes <= 0 or args.scan_limit <= 0 or args.recurring_minimum < 2:
        raise SystemExit("--nodes and --scan-limit must be positive")
    if args.grace_minutes < 0 or args.release_delay_minutes < 0:
        raise SystemExit("--grace-minutes and --release-delay-minutes cannot be negative")
    if not 0 <= args.habitual_miss_fraction <= 1 or args.recurring_gap_cv < 0:
        raise SystemExit("recurrence fractions must be between zero and one")
    timestamps = TimestampFormatter("epoch", args.timezone)
    paths = expand_paths(args.inputs)
    targets = read_target_bins(args.timeline, args.nodes, args.candidate_fraction)
    target_set = set(targets)
    all_results = {}
    owned_targets = set()

    for path in paths:
        window = filename_month_window(path, timestamps)
        file_targets = sorted(
            value for value in target_set if window[0] <= value < window[1]
        )
        owned_targets.update(file_targets)
        if not file_targets:
            continue
        print(
            "Auditing {} ({} candidate bins)".format(os.path.basename(path), len(file_targets)),
            file=sys.stderr,
        )
        all_results.update(
            replay_file(
                path, window, file_targets, args.nodes, args.grace_minutes * 60,
                args.recurring_minimum, args.recurring_gap_cv,
                args.habitual_miss_fraction, args.scan_limit,
                args.release_delay_minutes * 60,
            )
        )

    fields = [
        "start", "observed_running_nodes", "observed_free_nodes",
        "direct_opportunity_jobs", "backfill_opportunity_jobs",
        "missed_direct_jobs", "missed_backfill_jobs", "missed_opportunity_jobs",
        "size_controlled_direct_jobs", "size_controlled_backfill_jobs",
        "size_controlled_opportunity_jobs",
        "missed_nonrecurring_direct_jobs", "missed_nonrecurring_backfill_jobs",
        "missed_nonrecurring_jobs", "missed_nonrecurring_families",
        "missed_nonrecurring_direct_families",
        "missed_nonrecurring_backfill_families",
        "min_missed_nonrecurring_nodes",
        "min_missed_nonrecurring_backfill_nodes",
        "recurring_family_count", "habitually_missed_family_count",
        "reclaiming_nodes", "unavailable_nodes",
        "grace_seconds", "release_delay_seconds",
        "successful_size_control_enabled",
        "queue_jobs_examined", "queue_scan_truncated",
        "reservation_time",
        "backfill_opportunity_job_ids", "missed_backfill_job_ids",
        "missed_nonrecurring_backfill_job_ids",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for start in sorted(owned_targets):
            values = dict(all_results.get(start, {}))
            values["start"] = start
            writer.writerow({field: values.get(field, 0) for field in fields})
    print("Wrote {} audited bins to {}".format(len(owned_targets), args.output), file=sys.stderr)


if __name__ == "__main__":
    main()
