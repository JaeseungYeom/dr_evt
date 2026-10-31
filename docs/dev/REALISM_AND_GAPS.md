# Realism and Known Gaps

This page records how closely DR_EVT's scheduling model follows production
batch schedulers, where it deliberately departs from them, and which way each
departure biases results. It compares the simulator with Slurm, Flux
(Fluxion), and IBM Spectrum LSF on the points that matter most for
backfilling studies: when scheduling decisions are evaluated, how the
backfill test window is formed, how many jobs hold reservations, and what the
scheduler knows about run times. The policies themselves are defined in
[Scheduling Policies](../BACKFILLING_ALGORITHMS.md); the execution pipeline in
[Simulation Pipeline and Job Lifecycle](JOB_LIFECYCLE.md).

The node release delay (nodes staying unavailable after a job ends) closes
one of these gaps; it is off by default and documented under
[`--node_release_delay`](../user-guide/command-line.md#--node_release_delay-seconds).

## What DR_EVT models

The statements below describe the code, not an idealization of it.

- **Event-driven evaluation.** A scheduling pass runs after every state
  change: each batch of job completions, each batch of arrivals, and each
  capacity change. All events sharing a timestamp are applied first, then the
  pass repeats until no further job can start. There is no scheduling
  interval and no polling.
- **Exact time.** The clock is a floating-point second count. Reservations
  and completion tests use exact times; nothing is rounded to a grid.
- **Configurable queue depth; unlimited horizon.** Built-in hybrid and
  conservative backfill scan every eligible waiting job by default; set
  `--backfill_queue_depth` to cap that prefix. Reservations may lie
  arbitrarily far in the future. `num_max_candidates` instead limits feasible
  jobs offered to the experimental external EASY selector.
- **Backfill test window.** Under EASY the head's reservation is the earliest
  time enough nodes become free, projected from running jobs' time limits.
  A candidate starts only if it fits the free nodes now and its projected
  completion (start plus time limit) is strictly earlier than that
  reservation. Conservative uses the earliest reservation among all earlier
  waiting jobs; hybrid uses the earliest among the first `reservation_depth`
  of them. Jobs started earlier in the same pass are included when later
  candidates are tested.
- **Run-time knowledge.** The scheduler plans with the requested time limit.
  The observed run time comes from the trace or from a sampled distribution
  and never exceeds the limit, which is equivalent to killing a job at its
  limit. An early completion triggers a new pass but does not revise earlier
  decisions.
- **Priority.** FCFS by submission time, or SJF/LJF by estimated run time.
  There is no multifactor priority (age, fair share, QOS, partition
  priority), no preemption, no job dependencies or arrays, and no
  per-user or per-account limits.
- **Resources.** A single pool of identical nodes, optionally varied over
  time by a capacity schedule. No node topology, no placement constraints,
  no node failures or draining, and no partitions.

## How production schedulers differ

### Slurm

Slurm has two schedulers ([`slurm.conf(5)`](https://slurm.schedmd.com/slurm.conf.html),
`SchedulerType` and `SchedulerParameters`; [Scheduling Configuration
Guide](https://slurm.schedmd.com/sched_config.html)). The main backfill
difference is trigger timing: DR_EVT backfills after each relevant event,
whereas Slurm backfills on its periodic `bf_interval`. Slurm's main scheduler
"runs in a limited fashion every time that any event happens which could
enable a job to start (e.g. job submit, job terminate, etc.)", rate-limited
by `sched_min_interval`, and additionally runs a full pass every
`sched_interval`. In those event-triggered passes it considers
`default_queue_depth` jobs in strict priority order, and "once any job or job
array task in a partition is left pending, no other jobs in that partition
will be scheduled". It does not backfill. The backfill scheduler
(`SchedulerType=sched/backfill`, the default) runs periodically:

| Parameter | Default | Meaning (from `slurm.conf(5)`) |
|---|---|---|
| `bf_interval` | 30 s | "The number of seconds between backfill iterations." |
| `bf_window` | 1440 min | "The number of minutes into the future to look when considering jobs to schedule." A job that would start later is not planned. |
| `bf_resolution` | 60 s | "The number of seconds in the resolution of data maintained about when jobs begin and end." |
| `bf_max_job_test` | 500 (100 in older releases) | "The maximum number of jobs to attempt backfill scheduling for (i.e. the queue depth)." |
| `bf_max_time` | `bf_interval` | "The maximum time in seconds the backfill scheduler can spend before discontinuing." |
| `bf_max_job_start` | 0 (no limit) | Jobs started per backfill iteration. |
| `bf_job_part_count_reserve` | 0 | "The backfill scheduling logic will reserve resources for the specified count of highest priority jobs in each partition." With 0, every tested job that cannot start is planned, which is conservative behavior within the tested set. |
| `default_queue_depth` | 100 | "The default number of jobs to attempt scheduling when a running job completes or other routine actions occur." |
| `sched_interval` | 60 s | "How frequently, in seconds, the main scheduling loop will execute and test all pending jobs." |

Thus Slurm's backfill pass is periodic and bounded by its queue, horizon, and
time settings; between passes, its main scheduler does not backfill.

### Flux (Fluxion)

Fluxion is event-driven like DR_EVT: a scheduling-relevant event triggers a
fresh reservation plan. The relevant differences, visible in
`queue_policy_bf_base_impl.hpp`
([flux-sched at 3b49d19](https://github.com/flux-framework/flux-sched/blob/3b49d1998ed53836618e54188f45b52bc1aac811/qmanager/policies/queue_policy_bf_base_impl.hpp)):

- Fluxion examines at most `queue-depth` jobs per cycle and yields after six
  jobs; DR_EVT processes its configured depth in one pass.
- Fluxion plans in integer seconds and places jobs on a resource graph;
  DR_EVT uses exact times and a homogeneous node pool.

Policies and parameters at that commit (`queue_system_defaults.hpp`, the
policy constructors, and the `flux-config-sched-fluxion-qmanager(5)` page):

| Setting | Value | Meaning |
|---|---|---|
| `queue-policy` | `fcfs` (default), `easy`, `conservative`, `hybrid` | Reservation depth 0, 1, all, or N respectively. |
| `queue-params.queue-depth` | 32 (max `max-queue-depth`, 1,000,000) | Pending jobs considered per scheduling cycle. |
| `policy-params.reservation-depth` | 64 under `hybrid` | Higher-priority pending jobs that receive reservations per cycle; `easy` fixes it at 1, `conservative` uses the queue depth. |
| `policy-params.max-reservation-depth` | 100,000 | Upper bound on the above. |

DR_EVT's `--backfill_policy easy|hybrid|conservative` with
`--reservation_depth N` maps directly onto these semantics (1, N, all).
`--backfill_queue_depth` reproduces Fluxion's per-cycle queue-depth cap. A
reservation depth above queue depth is inactive for that pass; Fluxion's
default 64 is effectively capped by its default queue depth of 32.

### IBM Spectrum LSF (Lassen, Sierra)

LSF schedules in sessions run by the `mbschd` daemon, and the sessions are
periodic. In LSF 10.1 the default is `JOB_SCHEDULING_INTERVAL=5 45` in
`lsb.params`: the first number is the minimum scheduling cycle time, so a
session that finishes early makes `mbschd` sleep until five seconds have
elapsed, and the second is a threshold on the cycle length. Only with a
minimum of 0 does the next session start immediately after the current one.
Each session is a full pass over the pending queue rather than a reaction to
one event. Backfilling requires a run limit on the candidate and works
against slot reservations held for the highest-priority pending jobs
(`SLOT_RESERVE`, `MAX_RESERVE_TIME`), which bound how far ahead a reservation
extends. The point at which a finishing job's slots become reusable depends
on post-execution processing. The interval default is taken from the IBM
documentation; the reservation and post-execution statements are summarized
from it and should be checked against the installed version.

## Gap table

| Aspect | DR_EVT | Slurm | Flux (Fluxion) | LSF | Direction of bias in DR_EVT |
|---|---|---|---|---|---|
| Evaluation trigger | Every event, repeated until nothing starts | Main scheduler on events; backfill every `bf_interval` (30 s) | Every event, from the reactor check phase; resumes in chunks of six jobs | Sessions at least `JOB_SCHEDULING_INTERVAL` (5 s) apart | Backfill reacts sooner than in Slurm or LSF |
| Time resolution | Exact | `bf_resolution` (60 s) grid | 1 s | 1 s class | Slightly more backfill opportunities than grid-based planners |
| Planning horizon | Unlimited | `bf_window` (1 day) | Unlimited | Bounded by reservation settings | Long reservations are honored that Slurm would not create |
| Queue depth examined | All eligible by default; `backfill_queue_depth` caps hybrid/conservative passes | `bf_max_job_test` (500), `default_queue_depth` (100) | `queue-depth` (32 per cycle) | All pending jobs per session | More candidates per decision, more backfill |
| Reservation depth | 1 (EASY), N (hybrid), all (conservative) | All tested jobs, or `bf_job_part_count_reserve` | 1 (`easy`), `reservation-depth` (64 default, `hybrid`), queue depth (`conservative`); all reservations re-planned every cycle | Top pending jobs | Comparable when depths are matched |
| Completion test | Strict: projected end before reservation | Grid-rounded comparison | Second-resolution comparison | Run limit must fit | Marginal cases differ at the resolution boundary |
| Node reuse after completion | Immediate by default; `--node_release_delay` models cleanup, constant or growing with job size | After epilog; nodes stay COMPLETING | After epilog; `t_inactive` after `t_cleanup` | After post-execution | Optimistic by the cleanup time per job unless the delay is enabled |
| Priority | FCFS, SJF, LJF | Multifactor | Priority plugins, FCFS default | Fair share, queues | Different job order; small effect on utilization, larger on per-job wait |
| Run-time knowledge | Time limit; actual run time at most the limit | Same | Same | Same | None |
| Resources | Homogeneous pool, capacity schedule | Partitions, topology, features, failures | Resource graph | Hosts, slots, resource requirements | Fragmentation and placement losses are absent |

The first four rows compound in the same direction: DR_EVT's backfilling is an
upper bound on what a production scheduler can extract from the same
workload. Measured against Slurm and LSF, whose backfill decisions wait for
the next periodic pass, simulated wait times for backfillable jobs are
optimistic. With matching queue depth, the remaining Flux gaps are its
one-second planner and resource graph. The node-reuse row adds a second
optimistic term, removed when `--node_release_delay` is calibrated.

## Validation that bounds the gaps

- The 34 scheduler-correctness workloads match a from-scratch Python
  reference exactly in schedule and resource trace, so the EASY and
  conservative rules are implemented as specified; this is a consistency
  check between two implementations of the same idealized model, not a
  comparison with a production scheduler.
- The comparison with the RAPS simulator (`scripts/compare_with_raps.py`)
  shows two of the effects above in miniature: RAPS advances on a one-second
  tick and frees nodes one tick after completion, which adds a one-second lag
  per completion a job waits behind, and it computes the head reservation from
  running jobs sorted by time limit rather than by projected end, which lets
  it backfill jobs that delay the head. Both differences are visible on the
  CTC SP2 archive trace from roughly the 200th job on.

## Closing the gaps

Each of the following is a contained change if a study needs it:

- **Periodic backfill mode.** Run the backfill pass only every `bf_interval`
  seconds, letting completions and arrivals in between start only the queue
  head, to reproduce Slurm's cadence.
- **Planning resolution and window.** Round projected ends up to a grid and
  drop reservations beyond a horizon.
- **Priority hooks.** The experimental external selector already lets a
  caller rank backfill candidates by an arbitrary cost; extending the same
  callback to queue ordering would allow multifactor priority experiments
  without changing the built-in policies.
- **Node release delay.** Available: `--node_release_delay` with the
  `constant`, `exponential`, and `uniform` per-node models. Calibration needs
  a measurement of the real cleanup time; Flux records it per job as
  `t_inactive - t_cleanup`, and a replay trace bounds it from above by the gap
  before free nodes are next needed.

## Sources

- Slurm: `slurm.conf(5)` (SchedulerType, SchedulerParameters) and the
  Scheduling Configuration Guide, read from the `master` branch of the
  SchedMD repository on 2026-10-30.
- Flux: flux-sched at commit
  [3b49d19](https://github.com/flux-framework/flux-sched/tree/3b49d1998ed53836618e54188f45b52bc1aac811),
  `qmanager/policies/queue_policy_bf_base_impl.hpp`, the `easy`, `hybrid`,
  and `conservative` policy constructors, `qmanager/config/queue_system_defaults.hpp`,
  `qmanager/modules/qmanager_callbacks.cpp`, and
  `doc/man5/flux-config-sched-fluxion-qmanager.rst`.
- LSF: IBM Spectrum LSF 10.1 documentation, `lsb.params`
  `JOB_SCHEDULING_INTERVAL` (read 2026-10-30) for the session interval;
  the slot-reservation and post-execution statements are summarized from the
  `lsb.queues` reference and should be verified against the installed
  version before being relied on.
- DR_EVT: `src/sim/sim.cpp` (`advance_to_impl`), `src/sim/scheduler_*.cpp`,
  and `src/sim/scheduler_base.cpp` on this branch.
