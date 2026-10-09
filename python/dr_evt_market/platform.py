################################################################################
#         Copyright 2023 Lawrence Livermore National Security, LLC             #
#         See the top-level LICENSE file for details.                          #
#                                                                              #
#         SPDX-License-Identifier: MIT                                         #
################################################################################

"""One real machine exposing a share of itself as a DR_EVT simulation."""

from numbers import Real
from pathlib import Path


_STATISTIC_FIELDS = (
    "avg_turnaround_time", "avg_wait_time", "current_time", "jobs_completed",
    "jobs_running", "jobs_submitted", "jobs_waiting", "makespan",
    "nodes_available", "nodes_in_use", "resource_area", "total_nodes",
    "utilization",
)


class Platform:
    """One real machine, exposing a share of itself as a dr_evt simulation."""
    name: str
    total_nodes: int
    price_per_node_hour: float
    hardware: frozenset[str]

    def __init__(self, work_dir: str | Path, share: float = 1.0) -> None:
        """Create the platform's header file and streaming simulation."""
        if isinstance(share, bool) or not isinstance(share, Real) or not 0 < share <= 1:
            raise ValueError("share must be a number in (0, 1]")

        import dr_evt
        self.share = float(share)
        self.exposed_nodes = max(1, round(self.total_nodes * self.share))
        self._work_dir = Path(work_dir).resolve()
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._header_path = self._work_dir / f"{self.name}.csv"
        self._header_path.write_text(
            "job_submit_time,num_nodes,q_id,time_limit\n", encoding="utf-8"
        )

        params = dr_evt.SimParams()
        params.infile = str(self._header_path)
        params.total_nodes = self.exposed_nodes
        params.trace_format = "simple"
        params.timestamp_format = "epoch"
        params.run_time_mode = dr_evt.RunTimeMode.LIMIT
        params.backfill_policy = dr_evt.BackfillPolicy.EASY
        params.priority_policy = dr_evt.PriorityPolicy.FCFS
        self._params = params
        self._simulation = dr_evt.Simulation(params)
        self._dr_evt = dr_evt
        self._last_time_s = 0

    def fits(self, job) -> bool:
        """Return whether a job's hardware and node demand fit this platform."""
        return job.requires <= self.hardware and job.num_nodes <= self.exposed_nodes

    def cost(self, job) -> float:
        """Return the platform cost of running a job for its time limit."""
        return self.price_per_node_hour * job.num_nodes * job.limit_s / 3600

    def advance_to(self, time_s: int) -> None:
        """Advance the simulation to a nondecreasing integer time."""
        if isinstance(time_s, bool) or not isinstance(time_s, int):
            raise ValueError("advance time must be an integer")
        if time_s < self._last_time_s:
            raise ValueError("advance time cannot move backwards")
        self._simulation.advance_to(time_s)
        self._last_time_s = time_s

    def free_nodes(self) -> int:
        """Return the number of nodes currently available."""
        return int(self._simulation.get_available_nodes())

    def waiting(self) -> int:
        """Return the number of jobs waiting for scheduler placement."""
        return int(self._simulation.get_active_job_count())

    def submit(self, jobs, time_s: int) -> list[int]:
        """Submit jobs at one time in their given order and return their IDs."""
        requests = [
            self._dr_evt.JobAppendRequest(time_s, job.num_nodes, "1", job.limit_s)
            for job in jobs
        ]
        if not requests:
            return []
        return [int(job_id) for job_id in self._simulation.append_jobs(requests)]

    def statistics(self) -> dict[str, float]:
        """Return all simulation statistics as an ordered mapping of floats."""
        statistics = self._simulation.get_statistics()
        return {field: float(getattr(statistics, field)) for field in _STATISTIC_FIELDS}


class Corona(Platform):
    """The Corona AMD GPU platform profile."""
    name = "corona"
    total_nodes = 121
    price_per_node_hour = 2.0
    hardware = frozenset({"cpu", "gpu", "amd"})


class Dane(Platform):
    """The Dane CPU platform profile."""
    name = "dane"
    total_nodes = 1544
    price_per_node_hour = 1.0
    hardware = frozenset({"cpu"})


class Lassen(Platform):
    """The Lassen NVIDIA GPU platform profile."""
    name = "lassen"
    total_nodes = 795
    price_per_node_hour = 3.0
    hardware = frozenset({"cpu", "gpu", "nvidia"})


class Tioga(Platform):
    """The Tioga AMD GPU platform profile."""
    name = "tioga"
    total_nodes = 32
    price_per_node_hour = 6.0
    hardware = frozenset({"cpu", "gpu", "amd"})


class Tuolumne(Platform):
    """The Tuolumne AMD GPU platform profile."""
    name = "tuolumne"
    total_nodes = 1152
    price_per_node_hour = 8.0
    hardware = frozenset({"cpu", "gpu", "amd"})


PLATFORMS = (Corona, Dane, Lassen, Tioga, Tuolumne)
DEFAULT_FEDERATION = ("corona", "lassen", "tioga", "tuolumne")
_PROFILES = {profile.name: profile for profile in PLATFORMS}


def federation(work_dir, share=1.0, names=DEFAULT_FEDERATION) -> dict[str, Platform]:
    """Build the selected named platforms in the requested order."""
    root = Path(work_dir)
    try:
        return {name: _PROFILES[name](root / name, share) for name in names}
    except KeyError as error:
        raise ValueError(f"unknown platform {error.args[0]!r}") from None
