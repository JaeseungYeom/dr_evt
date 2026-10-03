# Online multi-cluster workload dispatch

`mpi_performance_dispatch` models one controller and one independent DR_EVT
simulation per system. It combines a chronological job stream with sampled
application workloads and dispatches every arrival using current simulated
queue state and measured relative performance. The decision is online: no
routing plan is computed before the run.

The native implementation uses MPI and Ser20, but not gRPC or Python. Rank 0
owns the input tables, random-number generator, and dispatch policy. Each
other rank owns one `Simulation`; worker-rank order matches systems-table row
order.

## Input model

The experiment has four CSV inputs. Column names are case-sensitive.

### Job stream

```text
job_submit_time,num_nodes,time_limit,actual_run_time
0,2,14400,3618
0,1,43200,42015
1,4,7200,252
```

| Column | Meaning |
|---|---|
| `job_submit_time` | Arrival time in simulation seconds. Rows must be in nondecreasing order. |
| `num_nodes` | Positive node request. |
| `time_limit` | Positive requested wall-time limit. |
| `actual_run_time` | Positive measured duration, no greater than `time_limit`. The alias `duration` is accepted. |

`job_id` is optional and defaults to the zero-based row number. The queue is
also optional and defaults to Queue1; use `q_id` normally or `queue` with
`DR_EVT_LEGACY_QUEUE_INPUT=ON`.

### Application requirements

```text
#app,sys_requirement
amg,CPU-only
kripke.exe,GPU-portable
testdfft,CPU-only
```

The accepted values are `CPU-only`, `GPU-only`, and `GPU-portable`. Every
application in the workload table must occur exactly once. The requirement is
application-specific, not job-stream-specific. `multi-cluster/apps.csv` is a
runtime input; the `#` prefix on its first header is accepted.

### Systems

```text
#machine,size,GPU
dane,256,CPU-only
mammoth,64,CPU-only
tioga,30,GPU-enabled
tuolumne,256,GPU-enabled
matrix,26,GPU-enabled
```

`multi-cluster/machines.csv` is a runtime input. Its columns are machine name,
physical node count, and machine type (`CPU-only` or `GPU-enabled`); the `#`
prefix on the first header is accepted. The size is the worker simulation's
schedulable capacity. Because background jobs are not modeled, this represents
an otherwise idle machine and therefore overstates real-world availability.

A job is considered only for machines whose configured size can host it. With
the table above, a request of 27 through 256 nodes can run only on Dane or
Tuolumne. A request larger than the largest configured machine is truncated to
that largest size so it remains runnable. Both the original and effective node
counts are recorded in the decision output.

Performance-column names are inferred from the machine rows. A CPU-only
machine named `dane` uses `dane`. A GPU-enabled machine named `matrix` uses
`matrix-cpu` and `matrix-gpu`.

### Workload and relative-performance table

The measured table contains one logical workload per unique `(app, args,
ranks)` tuple:

```text
app,args,ranks,quartz,dane,mammoth,matrix-cpu,matrix-gpu,tioga-cpu,tioga-gpu,tuolumne-cpu,tuolumne-gpu
amg,-problem1-p442-n12812864,32,1.0,0.9821,1.0446,1.0943,,1.2496,,2.2508,
```

Each nonempty value is a positive, dimensionless speed relative to the row's
baseline. A value of `2.0` means twice the baseline performance and therefore
half the duration. An empty cell means that execution mode is unavailable or
unmeasured and is excluded from dispatch.

Quartz is the preferred baseline. The supplied raw table contains repeated
`(app, args, ranks)` rows normalized against different machines. The loader
collapses those rows to one sample, preferring a row whose Quartz value is
exactly `1.0`. If Quartz was not the baseline, it identifies the row's baseline
from the performance column whose value is exactly `1.0`. A curated table can
instead provide an explicit `baseline_system` column.

Rows without a usable measurement for any configured compatible system are
reported and omitted. Duplicate rows do not receive extra sampling weight.

## Sampling

For every arriving job, rank 0:

1. Selects one application uniformly from applications with usable workloads.
2. Selects one workload uniformly from that application's usable `(app, args,
   ranks)` rows.

Applications therefore have equal probability even when their sample counts
differ. `--seed` controls the controller RNG and defaults to `0`, making runs
reproducible. Sampling is controller state; it adds no workload-catalog
bookkeeping to the DR_EVT simulations.

## Compatibility and execution mode

| Requirement | CPU-only system | GPU-enabled system |
|---|---|---|
| `CPU-only` | CPU measurement | CPU measurement |
| `GPU-only` | Incompatible | GPU measurement |
| `GPU-portable` | CPU measurement | Faster available CPU or GPU measurement |

An empty required measurement makes that mode unavailable. A machine is also
excluded when its configured size is smaller than the job's effective node
request. Dispatch fails clearly when an arrival has no measured compatible
execution mode.

## Online dispatch

At each arrival, the controller samples a workload, advances every worker to
the submission time, and queries current free nodes, release schedule,
EASY-backfill shadow time, and prediction horizon. For each compatible system:

```text
adjusted_duration    = actual_run_time / relative_performance
adjusted_time_limit  = time_limit      / relative_performance
predicted_turnaround = estimated_wait  + adjusted_duration
```

The smallest predicted turnaround wins; ties use estimated wait and then
systems-table order. Only the selected worker receives the job. DR_EVT plans
with the adjusted time limit and completes it after the adjusted actual
duration. The next arrival observes all earlier decisions, so dispatch remains
online even though inputs are validated before the run.

## Build and run

```bash
cmake -S . -B build -DDR_EVT_WITH_SER20=ON
cmake --build build --target mpi_performance_dispatch-bin

mpirun -np 6 build/mpi_performance_dispatch \
  --jobs multi-cluster/synthetic_job_stream.csv \
  --workload-table multi-cluster/relative_runtime_matrix_quartz_new.csv \
  --applications multi-cluster/apps.csv \
  --systems multi-cluster/machines.csv \
  --seed 7 \
  --output dispatch-decisions.csv
```

Use `srun -n 6` instead of `mpirun -np 6` inside an appropriate Slurm
allocation. MPI and Ser20 are required; gRPC and Python are not.

## Output

Rank 0 writes one CSV row per job-stream row:

| Column | Meaning |
|---|---|
| `job_id` | Input identifier or generated zero-based row number. |
| `submit_time`, `num_nodes`, `duration`, `time_limit` | Original job-stream values. |
| `effective_nodes` | Submitted node count: `min(num_nodes, largest configured machine size)`. |
| `App`, `Args`, `Ranks` | Sampled workload identity. |
| `baseline_system` | Reference system for the relative-performance row. |
| `sys_requirement` | Application compatibility category. |
| `system_id` | Selected scheduler system. |
| `execution_mode` | Selected `CPU` or `GPU` implementation. |
| `relative_performance` | Performance factor used for scaling. |
| `estimated_wait` | Wait predicted from current worker state. |
| `estimated_duration` | Scaled actual execution duration. |
| `adjusted_time_limit` | Scaled scheduler wall-time limit. |
| `predicted_turnaround` | `estimated_wait + estimated_duration`. |
| `job_idx` | Worker-local DR_EVT job identifier. |

After draining all workers, rank 0 prints submitted/completed counts and
makespan per system to standard error.

## Python/gRPC implementation

`grpc_performance_dispatch.py` implements the same input model and online
dispatch policy through independent gRPC servers. The MPI launcher only starts
the controller and servers; the client script owns sampling and dispatch:

```bash
python3 python/grpc_mpi_launcher.py --mpi-ranks 6 \
  --server-binary build/dr_evt_server \
  --client-script experimental/multi-cluster/grpc_performance_dispatch.py -- \
  --jobs multi-cluster/synthetic_job_stream.csv \
  --workload-table multi-cluster/relative_runtime_matrix_quartz_new.csv \
  --applications multi-cluster/apps.csv \
  --systems multi-cluster/machines.csv \
  --seed 7 \
  --output grpc-dispatch-decisions.csv
```

The same seed makes each implementation reproducible, but does not produce the
same sampled sequence across implementations because Python and C++ use
different random-number engines. Both implement uniform application sampling
followed by uniform workload sampling.

Run the Python policy tests directly:

```bash
python3 experimental/multi-cluster/test_performance_dispatch.py
```

The native dispatcher's end-to-end regression is registered separately with
CTest as `test_mpi_performance_dispatch`.

## Legacy platform-ranking baseline

`grpc_baseline_dispatch.py` is a separate older policy based on platform ranks
and wait tolerance rather than workload-specific performance measurements. Its
focused tests are unrelated to either profile-based implementation:

```bash
python3 experimental/multi-cluster/test_baseline_dispatch.py
```
