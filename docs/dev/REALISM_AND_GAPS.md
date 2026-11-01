# Realism and Known Gaps

This page records how closely DR_EVT's scheduling model follows production
batch schedulers, where it deliberately departs from them, and which way each
departure biases results. It compares the simulator with Slurm, Flux
(Fluxion), and IBM Spectrum LSF on the points that matter most for
backfilling studies: when scheduling decisions are evaluated, how the
backfill test window is formed, how many jobs hold reservations, and what the
scheduler knows about run times. The policies themselves are defined in
[Scheduling Policies](../BACKFILLING_ALGORITHMS.md); the execution pipeline in
[Simulation Pipeline and Job Lifecycle](JOB_LIFECYCLE.md). For the complete
option reference, see [Command-Line Options](../user-guide/command-line.md),
especially its [Scheduling Policies](../user-guide/command-line.md#scheduling-policies)
and [Simulation Mode Options](../user-guide/command-line.md#simulation-mode-options).

The node release delay (nodes staying unavailable after a job ends) closes
one of these gaps; it is off by default and documented under
[`--node_release_delay`](../user-guide/command-line.md#--node_release_delay-seconds).

## DR_EVT model and relevant options

The statements below describe the code, not an idealization of it.

- **Evaluation trigger.** By default, a scheduling pass runs after every
  state change: each batch of job completions, each batch of arrivals, and
  each capacity change. All events sharing a timestamp are applied first,
  then the pass repeats until no further job can start. With
  [`--backfill_interval SECONDS`](../user-guide/command-line.md#--backfill_interval-seconds),
  FCFS dispatch remains event-driven while backfill evaluations run only at
  periodic ticks; `0` selects the default fully event-driven behavior.
- **Time and planning resolution.** The clock and actual job execution use
  floating-point seconds. By default, reservations and backfill completion
  tests use exact planning times.
  [`--planning_time_resolution SECONDS`](../user-guide/command-line.md#--planning_time_resolution-seconds)
  rounds each projected release timestamp up to that grid after a job has a
  known or proposed start time. It affects projected releases, reservations,
  and backfill tests, but does not round the clock or change actual execution.
- **Queue depth.** Built-in hybrid and conservative backfill scan every
  eligible waiting job by default;
  [`--backfill_queue_depth`](../user-guide/command-line.md#--backfill_queue_depth-n)
  caps how many queue entries are inspected in one pass. It is a job-count
  limit, not a time limit.
  [`--num_max_candidates`](../user-guide/command-line.md#--num_max_candidates-count)
  instead limits feasible jobs offered to the experimental external EASY
  selector.
- **Backfill test window.** Under EASY the head's reservation is the earliest
  time enough nodes become free, projected from running jobs' time limits.
  A candidate starts only if it fits the free nodes now and its projected
  completion (start plus time limit) is strictly earlier than that
  reservation. [`--backfill_policy`](../user-guide/command-line.md#-b---backfill_policy-policy)
  selects EASY, conservative, or hybrid behavior; conservative protects all
  relevant earlier jobs, while hybrid uses the earliest reservation among the
  first
  [`--reservation_depth`](../user-guide/command-line.md#--reservation_depth-n)
  waiting jobs. This is a per-candidate deadline that protects an existing
  reservation, not a bound on how far ahead the scheduler plans. Jobs started
  earlier in the same pass are included when later candidates are tested.
- **Planning horizon.** A planning horizon is the furthest future timestamp
  considered while building reservations. During periodic scheduling,
  [`--planning_horizon SECONDS`](../user-guide/command-line.md#--planning_horizon-seconds)
  applies that cutoff; when omitted, DR_EVT uses the largest `tdiff_t` value
  as an unlimited sentinel. Event-driven schedulers do not apply a horizon;
  an event-driven planning horizon remains future work.
  This corresponds to Slurm's `bf_window` and is independent of queue depth
  and of a candidate's backfill test window. A job reached by DR_EVT's
  hybrid/conservative queue scan still consumes its
  [`--backfill_queue_depth`](../user-guide/command-line.md#--backfill_queue_depth-n)
  slot when its reservation is outside the horizon, but it receives no
  reservation and does not consume a `--reservation_depth` slot. This mirrors
  Slurm's `bf_max_job_test` / `bf_job_part_count_reserve` distinction.
- **Run-time knowledge.** The scheduler plans with the requested time limit.
  The observed run time comes from the trace or from a sampled distribution
  and never exceeds the limit, which is equivalent to killing a job at its
  limit. An early completion triggers a new pass but does not revise earlier
  decisions. See the [simulation-mode options](../user-guide/command-line.md#simulation-mode-options)
  and [trace time fields](../user-guide/trace-formats.md#simple-format-columns).
- **Priority.** FCFS by submission time, or SJF/LJF by estimated run time.
  Configure the available choices with
  [`--priority_policy`](../user-guide/command-line.md#-p---priority_policy-policy).
  DR_EVT currently does not model multifactor priority (age, fair share,
  QOS, or partition priority), preemption, job dependencies or arrays, or
  per-user and per-account limits. Whether a production deployment enables
  or relies on those policies is an institutional configuration decision.
- **Resources.** The stock simulator uses one pool of identical nodes, set
  with [`--total_nodes`](../user-guide/command-line.md#-n---total_nodes-count)
  and optionally varied with
  [`--capacity_schedule`](../user-guide/command-line.md#--capacity_schedule-filename).
  The experimental
  [`multi_system_backfill_sim`](../user-guide/multi-cluster-backfill.md)
  instead has one shared FCFS queue across multiple homogeneous systems (or
  resource partitions) of unequal capacity. Neither model represents node
  topology, placement constraints, failures, draining, or Slurm's complete
  partition and policy model.

## Production scheduler comparisons

### Slurm

Slurm's controller combines its main priority scheduler with an optional
backfill scheduling plugin; they share the same cluster state rather than
running as separate services ([`slurm.conf(5)`](https://slurm.schedmd.com/slurm.conf.html),
`SchedulerType` and `SchedulerParameters`; [Scheduling Configuration
Guide](https://slurm.schedmd.com/sched_config.html)). The main backfill
difference is trigger timing: by default, DR_EVT backfills after each relevant
event, while `--backfill_interval` provides periodic backfill evaluations to
approximate Slurm's `bf_interval`. Slurm's main scheduler
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

Thus Slurm's backfill pass is periodic and independently bounded by its queue
scan, reservation horizon, and time-resolution settings; between passes, its
main scheduler does not backfill.

DR_EVT offers `--backfill_interval` for circular EASY, conservative, or hybrid
backfill. Events still dispatch FCFS heads, while periodic ticks run the
configured backfill pass. For the periodic model, map Slurm controls
independently: `bf_interval` to `--backfill_interval`, `bf_window` to
`--planning_horizon`, and `bf_resolution` approximately to
`--planning_time_resolution`,
`bf_max_job_test` to `--backfill_queue_depth`, and
`bf_job_part_count_reserve` to `--reservation_depth`. The planning horizon
does not change the planning-time grid; it only decides whether a projected
reservation is retained.

### Flux (Fluxion)

Fluxion is event-driven like DR_EVT: a scheduling-relevant event triggers a
fresh reservation plan. The relevant differences, visible in
`queue_policy_bf_base_impl.hpp`
([flux-sched at 3b49d19](https://github.com/flux-framework/flux-sched/blob/3b49d1998ed53836618e54188f45b52bc1aac811/qmanager/policies/queue_policy_bf_base_impl.hpp)):

- Fluxion examines at most `queue-depth` jobs per cycle and yields after six
  jobs; DR_EVT processes its configured depth in one pass.
- Fluxion plans in integer seconds and places jobs on a resource graph;
  DR_EVT uses exact times and a homogeneous node pool.

Defaults and policy-fixed behavior at that commit (`queue_system_defaults.hpp`,
the policy constructors, and the
`flux-config-sched-fluxion-qmanager(5)` page):

| Setting | Default or policy-fixed behavior | Meaning |
|---|---|---|
| `queue-policy` | `fcfs` (default), `easy`, `conservative`, `hybrid` | Reservation depth 0, 1, all, or N respectively. |
| `queue-params.queue-depth` | 32 (max `max-queue-depth`, 1,000,000) | Pending jobs considered per scheduling cycle. |
| `policy-params.reservation-depth` | 64 under `hybrid` | Hybrid-only setting for the number of higher-priority pending jobs that receive reservations per cycle. `easy` always reserves 1; `conservative` always uses the queue depth. |
| `policy-params.max-reservation-depth` | 100,000 | Upper bound on the above. |

DR_EVT's `--backfill_policy easy|hybrid|conservative` with
`--reservation_depth N` maps directly onto these semantics (1, N, all).
`--backfill_queue_depth` reproduces Fluxion's per-cycle queue-depth cap. A
reservation depth above queue depth is inactive for that pass; Fluxion's
default 64 is effectively capped by its default queue depth of 32.
DR_EVT has no separate equivalent of Fluxion's
`policy-params.max-reservation-depth`: its hybrid `--reservation_depth` is
accepted up to the implementation's `size_t` limit, and the current eligible
queue (or `--backfill_queue_depth`, when set) bounds the effective depth of a
pass.

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

## Configuration mappings and remaining differences

These settings reproduce trigger and reservation behavior in the stock,
single-pool FCFS simulator; they do not add Slurm's full partition and policy
model or Flux's resource graph. The experimental
[`multi_system_backfill_sim`](../user-guide/multi-cluster-backfill.md) adds a
shared-queue, homogeneous multi-system/partition model, but is not a Slurm
partition emulation.

| Target | Backfill policy | Interval | Reservation depth | Queue depth | Planning resolution |
|---|---|---:|---:|---:|---:|
| Slurm, reserve all tested jobs | `conservative` | `bf_interval` (30 s default) | N/A | `bf_max_job_test` (500 default) | 60 s (`bf_resolution`) |
| Slurm, reserve a fixed prefix | `hybrid` | `bf_interval` | `bf_job_part_count_reserve` | `bf_max_job_test` | 60 s |
| Slurm EASY-like head reservation | `hybrid` | `bf_interval` | `1` | `bf_max_job_test` | 60 s |
| Fluxion hybrid | `hybrid` | `0` (event-driven) | Flux `reservation-depth` | Flux `queue-depth` (32 default) | 1 s |

For a Slurm comparison, the relationship between DR_EVT's reservation options
is as follows: `--backfill_policy easy` protects only the FCFS head, which is
equivalent to `--backfill_policy hybrid --reservation_depth 1`. The
combination of `--backfill_policy hybrid` and `--reservation_depth N`
approximates protecting a fixed prefix of `N` higher-priority jobs, analogous
to Slurm's `bf_job_part_count_reserve=N`. `--backfill_policy conservative`
protects all eligible jobs considered in the pass, analogous to Slurm's
unrestricted reserve behavior (`bf_job_part_count_reserve=0`) within its
tested queue. This is a comparison of reservation count only: DR_EVT has one
homogeneous pool and one queue, while Slurm applies its reservation settings
per partition and also limits its tested queue with `bf_max_job_test`.

Use `--backfill_interval 0` for DR_EVT's ordinary event-driven backfill. A
positive interval keeps event-driven FCFS dispatch but runs backfill only at
periodic ticks.

Use `--planning_time_resolution SECONDS` to apply the last column without
changing simulated execution. It rounds a projected release timestamp after
its start time is known; `0` retains exact planning. It is
applied under every policy, but affects FCFS only where projected releases are
used—immediate FCFS dispatch remains based on currently free nodes. Slurm's
`bf_resolution` likewise grids maintained planned begin/end timestamps.
DR_EVT does not quantize its clock or actual job execution.

### Comparison table and configurable mitigations

This table includes both remaining model gaps and differences that can be
narrowed through DR_EVT configuration. A row remains when a setting can match
one aspect of another scheduler but cannot reproduce its full model; the
DR_EVT controls relevant to the comparison are:

- [Priority policy](../user-guide/command-line.md#-p---priority_policy-policy)
  (`--priority_policy`)
- [Backfill policy](../user-guide/command-line.md#-b---backfill_policy-policy),
  [`--reservation_depth`](../user-guide/command-line.md#--reservation_depth-n),
  and [`--backfill_queue_depth`](../user-guide/command-line.md#--backfill_queue_depth-n)
- [`--backfill_interval`](../user-guide/command-line.md#--backfill_interval-seconds)
  and
  [`--planning_time_resolution`](../user-guide/command-line.md#--planning_time_resolution-seconds)
- [`--node_release_delay`](../user-guide/command-line.md#--node_release_delay-seconds)
  and its
  [per-node delay model](../user-guide/command-line.md#--node_release_delay_model-model)
- [Run-time mode and related options](../user-guide/command-line.md#simulation-mode-options),
  plus [trace time fields](../user-guide/trace-formats.md#simple-format-columns)
- [`--total_nodes`](../user-guide/command-line.md#-n---total_nodes-count),
  [`--capacity_schedule`](../user-guide/command-line.md#--capacity_schedule-filename),
  and the experimental [multi-system scheduler](../user-guide/multi-cluster-backfill.md)

| Aspect | DR_EVT | Production-scheduler comparison |
|---|---|---|
| Evaluation trigger | Event-driven by default; periodic backfill is optional | Slurm and LSF run periodic backfill; Fluxion is event-driven. Default DR_EVT backfill reacts sooner than Slurm or LSF. |
| Time resolution | Exact planning by default; optional projected-timestamp grid | Slurm `bf_resolution` grids planned begin/end timestamps; Fluxion plans in integer seconds. |
| Planning horizon | Unlimited by default; `--planning_horizon` bounds periodic reservations | Slurm uses `bf_window`; Fluxion is unlimited; LSF is bounded by reservation settings. |
| Queue and reservation depth | Unlimited scan by default; EASY 1, hybrid N, conservative all | Slurm and Fluxion cap queue/reservation scope; matching depths improves comparability. |
| Completion and node reuse | Strict completion test; immediate reuse by default | Production systems use their own resolution and cleanup behavior. Calibrate the release delay to reduce optimism. |
| Priority and resources | FCFS/SJF/LJF; stock homogeneous pool | Production systems add policy plugins, partitions, and resource topology. The experimental scheduler adds homogeneous multi-system placement. |
| Run-time knowledge | Requested limit; observed time does not exceed it | Same high-level limit model. |

With DR_EVT's default settings, the first four rows compound in the same
direction: its backfilling is an upper bound on what a production scheduler
can extract from the same workload. Measured against Slurm and LSF, whose
backfill decisions wait for the next periodic pass, simulated wait times for
backfillable jobs are optimistic. Matching interval, planning horizon,
planning resolution, and queue depth narrows those differences but does not
add Slurm's partition-level policy or priority behavior. The experimental
multi-system scheduler represents homogeneous partitions but not those Slurm
semantics. With matching queue depth, the remaining Flux gaps are its resource
graph and scheduling-cycle behavior. The node-reuse difference is removed only
to the degree that
`--node_release_delay` is calibrated to the target system.

## Validation evidence

- The 34 scheduler-correctness workloads match a from-scratch Python
  reference exactly in schedule and resource trace, so the EASY and
  conservative rules are implemented as specified; this is a consistency
  check between two implementations of the same idealized model, not a
  comparison with a production scheduler.
- The [RAPS comparison helper, lines 21–35](../../scripts/compare_with_raps.py#L21-L35)
  reproduces two effects in miniature: RAPS advances on a one-second tick and
  frees nodes on the tick after completion, which adds a one-second lag per
  completion a job waits behind. In RAPS's
  [`find_backfill_job` (RAPS permalink, lines 167–188)](https://github.com/ExaDigiT/RAPS/blob/3f21cd35b7bf6a8f5decc41050c8d871809608ad/raps/schedulers/default.py#L167-L188),
  running jobs are sorted by `time_limit` (line 167), then the shadow time is
  assigned from the selected job's `start_time + time_limit` (lines 172–179),
  rather than choosing the earliest projected end. This can select backfill
  jobs that delay the blocked head. Therefore, the RAPS comparison
  demonstrates behavioral differences; it is not a correctness oracle for
  DR_EVT's reservation-preserving backfill policy. Both differences are
  visible on the CTC SP2 archive trace from roughly the 200th job on.

## Remaining extensions and calibration

Each of the following is a contained change if a study needs it:

- **Reservation-horizon calibration.**
  [`--planning_horizon`](../user-guide/command-line.md#--planning_horizon-seconds)
  supplies a periodic reservation cutoff. Calibrate it against the target
  scheduler's configured planning window (for example Slurm `bf_window`). An
  equivalent event-driven cutoff remains future work.
- **Priority hooks.** The experimental external selector already lets a
  caller rank backfill candidates by an arbitrary cost; extending the same
  callback to queue ordering would allow multifactor priority experiments
  without changing the built-in policies.
- **Node release delay.** Available:
  [`--node_release_delay`](../user-guide/command-line.md#--node_release_delay-seconds)
  with [the `constant`, `exponential`, and `uniform` per-node
  models](../user-guide/command-line.md#--node_release_delay_model-model).
  Calibration needs a measurement of the real cleanup time; Flux records it
  per job as `t_inactive - t_cleanup`, and a replay trace bounds it from above
  by the gap before free nodes are next needed.

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
- RAPS: [ExaDigiT/RAPS at commit
  `3f21cd35`](https://github.com/ExaDigiT/RAPS/tree/3f21cd35b7bf6a8f5decc41050c8d871809608ad),
  `raps/schedulers/default.py`, `find_backfill_job`, lines 167–188.
- DR_EVT: `src/sim/sim.cpp` (`advance_to_impl`), `src/sim/scheduler_*.cpp`,
  and `src/sim/scheduler_base.cpp` on this branch.
