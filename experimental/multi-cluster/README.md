# Online multi-cluster workload dispatch

`mpi_performance_dispatch` models one dispatcher and multiple cluster systems,
with one independent DR_EVT simulation per system. For each arriving workload,
the dispatcher selects a compatible system using the workload's predicted
relative performance and the systems' current simulated queue states.
Ground-truth relative performance determines how long the workload actually
runs on the selected system. Dispatch is online so that each decision accounts
for the latest estimated queue wait; the policy minimizes predicted turnaround,
which combines that wait with predicted run time.

The native implementation uses MPI, with Ser20 serialization for message
packing; it does not require gRPC or Python. Rank 0 owns the input tables,
random-number generator, and dispatch policy. Each other rank owns one
`Simulation`, and worker-rank order matches systems-table row order. The
corresponding Python implementation uses gRPC for dispatcher-worker
communication, while MPI starts the worker processes that host the gRPC
servers.

## Input model

The experiment has five CSV inputs. Column names are case-sensitive.

### Job stream

```text
submit_time,num_nodes,time_limit,duration
0,2,14400,3618
0,1,43200,42015
1,4,7200,252
```

| Column | Meaning |
|---|---|
| `submit_time` | Arrival time in simulation seconds. Rows must be in nondecreasing order. The legacy alias `job_submit_time` is accepted. |
| `num_nodes` | Positive node request. |
| `time_limit` | Positive whole-number wall-time limit in seconds; fractional values are rejected. |
| `duration` | Positive reference duration, no greater than `time_limit`. The legacy alias `actual_run_time` is accepted. |

`job_id` is optional and defaults to the zero-based row number. The queue is
also optional and defaults to Queue1; use `q_id` normally or `queue` with
`DR_EVT_LEGACY_QUEUE_INPUT=ON`.

### Application requirements

```text
#app,sys_requirement
amg,CPU-only
xsbench,GPU-portable
laghos,GPU-portable
minife.x,GPU-portable
minivite,CPU-only
testdfft,CPU-only
```

The accepted values are `CPU-only`, `GPU-only`, and `GPU-portable`. Every
application in the performance tables must occur exactly once. The requirement is
application-specific, not job-stream-specific. `experimental/multi-cluster/apps.csv` is a
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

`experimental/multi-cluster/machines.csv` is a runtime input. Its columns are machine name,
physical node count, and machine type (`CPU-only` or `GPU-enabled`); the `#`
prefix on the first header is accepted. The size is the worker simulation's
schedulable capacity. Because background jobs are not modeled, this represents
an otherwise idle machine and therefore overstates real-world availability.

A job is considered for every compatible machine whose configured size can
host it. With the table above, requests of 27 through 30 nodes can also run on
Tioga, and requests through 64 nodes can run on Mammoth. Only requests of 65
through 256 nodes are limited by capacity to Dane and Tuolumne. A request
larger than the largest configured machine is truncated to that largest size
so it remains runnable. The dispatcher writes a warning to standard error for
each truncated request. Both the original and effective node counts are
recorded in the decision output.

Performance-column names are inferred from the machine rows. A CPU-only
machine named `dane` uses `dane`. A GPU-enabled machine named `matrix` uses
`matrix-cpu` and `matrix-gpu`.

### Ground-truth and prediction tables

Ground truth and predictions are separate, caller-selected CSV inputs. Both
contain the same workload identities and execution-mode columns:

```text
App,Args,Ranks,dane,mammoth,matrix-cpu,matrix-gpu,tioga-cpu,tioga-gpu,tuolumne-cpu,tuolumne-gpu
amg,-problem1-p442-n12812864,32,0.9821,1.0446,1.0943,,1.2496,,2.2508,
```

`--ground-truth` supplies measured speedups and `--prediction` supplies values
available to the dispatch policy. Values are positive, dimensionless speedups
relative to the job stream's `duration`: a speedup of `2.0` means half that
duration. The two files must have identical `(App, Args, Ranks)` identities,
but their missing-value patterns may differ. A mode is dispatchable only when
both its ground-truth and prediction cells are present. They need no named
Quartz column, but must use the same duration reference.

Rows without a usable measurement for any configured compatible system are
reported and omitted. Duplicate `(app, args, ranks)` rows are rejected.
Applications absent from the application-to-requirement table are omitted,
allowing that table to act as an application sampling allowlist.

Generate the production table by joining the prediction matrix to measured
target runtimes:

```bash
source docs/venv/bin/activate
python3 experimental/multi-cluster/build_performance_tables.py \
  --predictions multi-cluster/relative_runtime_matrix_quartz_new.csv \
  --measurements multi-cluster/merged.txt \
  --applications experimental/multi-cluster/apps.csv \
  --systems experimental/multi-cluster/machines.csv \
  --ground-truth-output experimental/multi-cluster/ground_truth.csv \
  --prediction-output experimental/multi-cluster/prediction.csv
```

The join key is `(app, args, ranks)` after lowercasing the application and
removing whitespace and case differences from `args`; commas inside quoted
argument fields remain part of the argument. Ground truth retains every
compatible target-mode measurement and normalizes it to the first measured
non-dispatch reference in table order (normally Borax), falling back to a
compatible target mode only when needed. Model predictions are emitted only
when they can use that same reference, so missing model coverage never removes
ground-truth measurements. Quartz data is not required. In `merged.txt`, the
unsuffixed `matrix`, `tioga`, and `tuolumne` measurements represent their GPU
modes; their `-cpu` rows represent CPU modes.

Generate two interchangeable prediction baselines:

```bash
source docs/venv/bin/activate
python3 experimental/multi-cluster/build_prediction_baselines.py \
  --ground-truth experimental/multi-cluster/ground_truth.csv \
  --machine-rep multi-cluster/machine_rep.txt \
  --rajaperf-output experimental/multi-cluster/prediction.rajaperf.csv \
  --app-avg-output experimental/multi-cluster/prediction.app_avg.csv
```

The RAJAPerf baseline computes `borax_time / machine_time` for every shared
kernel position, averages those ratios per execution mode, and assigns that
machine-level value to every workload and system mode. Repeated kernel names
remain distinct positional samples. The application-average baseline assigns
the arithmetic mean ground-truth speedup for each `(App, Ranks, execution
mode)` group and retains the workload's measured-mode coverage.

#### Optional plotting

Plot actual (x-axis) against predicted (y-axis) relative performance for every
configured machine/execution-mode column (for example, `dane`, `mammoth`,
`matrix-cpu`, or `matrix-gpu`), with a consistent color for each application.
Here, execution mode identifies the CPU or GPU implementation on a machine; it
does not refer to the simulator's `run_time_mode` option:

```bash
source docs/venv/bin/activate
python3 experimental/multi-cluster/plot_relative_performance.py \
  --ground-truth experimental/multi-cluster/ground_truth.csv \
  --prediction experimental/multi-cluster/prediction.csv \
  --output experimental/multi-cluster/relative-performance.png
```

The default logarithmic axes make both sub-unit and large speedups visible.
Use `--linear` for linear axes or `--systems tuolumne tuolumne-cpu dane` to
select panels. Unsuffixed GPU-machine names resolve to their `-gpu` columns.
Cells lacking either an actual or predicted value are reported and omitted
because they cannot form an actual-versus-predicted point. When `--output`
does not already name a PDF, the plotter also writes a PDF with the same stem.

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
| `GPU-portable` | CPU measurement | Faster predicted CPU or GPU measurement |

A missing required ground-truth/predicted pair makes that mode unavailable. A
machine is also excluded when its configured size is smaller than the job's
effective node request. Dispatch fails clearly when an arrival has no
compatible execution mode with paired values.

## Online dispatch

At each arrival, the controller samples a workload, advances every worker to
the submission time, and queries current free nodes, release schedule,
EASY-backfill shadow time, and prediction horizon. For each compatible system:

```text
estimated_duration   = duration   / predicted_relative_performance
actual_duration      = duration   / ground_truth_relative_performance
predicted_time_limit = time_limit      / predicted_relative_performance
actual_time_limit    = time_limit      / ground_truth_relative_performance
predicted_turnaround = estimated_wait  + estimated_duration
```

The experiment assumes that the submitting user can correct an underestimated
wall-time request using the known ground-truth runtime before the successful
submission. Starting from `predicted_time_limit`, it doubles the request until
it is at least `actual_duration`, without simulating or charging resources for
the failed attempts. `--max-time-limit` caps the adapted request; the
prediction study defaults to Lassen's 43,200-second maximum. If
`actual_duration` exceeds that maximum, the experiment writes a `dropped:`
record to standard error and does not submit the job. Dropped jobs are excluded
from simulation statistics. This intentionally optimistic assumption isolates
placement quality from the cost of discovering a sufficient wall-time limit.

Candidate systems whose ground-truth runtime exceeds `--max-time-limit` are
discarded before placement, regardless of their waiting time. If no measured,
capacity-compatible system can finish within the maximum, the job is dropped.

`--dispatch-policy` selects one of two placement rules:

- `turnaround` chooses the smallest predicted turnaround, breaking ties by
  estimated wait and then systems-table order.
- `IPDPS24` implements Algorithm 2 from D. Nichols et al., "Predicting
  Cross-Architecture Performance of Parallel Programs," IEEE IPDPS'24. It
  chooses the highest predicted relative performance among feasible systems
  with enough nodes available immediately. If every feasible system is full,
  it chooses the highest predicted relative performance over all feasible
  systems and lets that system queue the job. Ties use systems-table order.

`--wall-time-policy adapted-limit` uses the doubling behavior described
above. `--wall-time-policy actual-duration` instead submits the smallest
whole-second time limit that covers the selected system's ground-truth runtime.
This second policy never changes or truncates the runtime; it changes only the
requested time limit.

The wait estimate uses the submitted time limit. Only the selected worker
receives the job, with that limit and the actual duration. Thus prediction
error affects placement, while the resulting queue state reflects the
workload's realized performance. The next arrival observes all earlier
decisions, so dispatch remains online even though inputs are validated before
the run.

## Build and run

```bash
cmake -S . -B build -DDR_EVT_WITH_SER20=ON
cmake --build build --target mpi_performance_dispatch-bin

mpirun -np 6 build/mpi_performance_dispatch \
  --jobs multi-cluster/job_stream.csv \
  --ground-truth experimental/multi-cluster/ground_truth.csv \
  --prediction experimental/multi-cluster/prediction.csv \
  --applications experimental/multi-cluster/apps.csv \
  --systems experimental/multi-cluster/machines.csv \
  --seed 7 \
  --max-time-limit 43200 \
  --dispatch-policy IPDPS24 \
  --wall-time-policy adapted-limit \
  --output dispatch-decisions.csv
```

Use `srun -n 6` instead of `mpirun -np 6` inside an appropriate Slurm
allocation. MPI and Ser20 are required; gRPC and Python are not.

### Prediction-study matrix

The prediction-study runner executes both dispatch policies (`turnaround` and
`IPDPS24`) with both wall-time policies (`adapted-limit` and
`actual-duration`) for the ideal, application-average, and RAJAPerf prediction
tables. Each of these 12 configurations runs on all ten synthetic traces, for
120 runs by default:

```bash
python experimental/multi-cluster/run_prediction_study.py \
  --executable "${CMAKE_INSTALL_PREFIX}/bin/mpi_performance_dispatch"
```

Repeat `--dispatch-policy` or `--wall-time-policy` to select a subset. For
example, the paper policy with oracle wall times is:

```bash
python experimental/multi-cluster/run_prediction_study.py \
  --executable "${CMAKE_INSTALL_PREFIX}/bin/mpi_performance_dispatch" \
  --dispatch-policy IPDPS24 \
  --wall-time-policy actual-duration
```

Output filenames and completion markers include the dispatch and wall-time
policy names. This prevents results from different configurations from being
mistaken for one another.

After all configurations are available, `summary.csv` contains one row for
each of the 120 runs, while `summary_aggregate.csv` and `summary.md` contain
the 12 ten-trace mean/standard-deviation records. `summary.png` plots those
aggregate metrics in four panels, and `summary.pdf` contains the same figure
in vector form. `metrics_per_run.csv` is retained as a compatibility copy of
`summary.csv`.

## Output

Rank 0 writes one CSV row per successfully dispatched job; dropped jobs are
recorded in the log instead:

| Column | Meaning |
|---|---|
| `job_id` | Input identifier or generated zero-based row number. |
| `submit_time`, `num_nodes`, `duration`, `time_limit` | Original job-stream values. |
| `effective_nodes` | Submitted node count: `min(num_nodes, largest configured machine size)`. |
| `App`, `Args`, `Ranks` | Sampled workload identity. |
| `sys_requirement` | Application compatibility category. |
| `system_id` | Selected scheduler system. |
| `execution_mode` | Selected `CPU` or `GPU` implementation. |
| `ground_truth_relative_performance` | Selected mode's ground-truth speedup. |
| `predicted_relative_performance` | Selected mode's predicted speedup used for dispatch. |
| `estimated_wait` | Wait predicted from current worker state. |
| `estimated_duration` | Duration predicted for the dispatch decision. |
| `actual_duration` | Ground-truth duration submitted to DR_EVT. |
| `predicted_time_limit` | Wall-time limit used to estimate the dispatch candidate. |
| `actual_time_limit` | Ground-truth-scaled wall-time limit retained for comparison. |
| `submitted_time_limit` | Whole-second limit submitted to DR_EVT: the upward-rounded adapted predicted limit or ground-truth runtime, according to `--wall-time-policy`. |
| `time_limit_doublings` | Number of pre-submission doublings needed by the adapted predicted-limit policy; zero for `actual-duration`. |
| `predicted_turnaround` | `estimated_wait + estimated_duration`. |
| `job_idx` | Worker-local DR_EVT job identifier. |

After draining all workers, rank 0 prints submitted/completed counts and
makespan per system to standard error, followed by an `overall` line containing
these evaluation metrics:

| Metric | Definition |
|---|---|
| `average_turnaround_time` | Sum of actual submit-to-completion times divided by completed jobs. |
| `average_bounded_slowdown` | Mean of `max(1, turnaround / max(actual run time, 10 seconds))`. |
| `average_run_time` | Sum of ground-truth-scaled run times divided by dispatched jobs. |
| `average_speedup` | Sum of selected ground-truth workload speedups divided by dispatched jobs. |

Turnaround and bounded slowdown are weighted by each system's completed-job
count when combined. A mismatch between the completed and dispatched counts is
reported as an error rather than producing partial metrics. The Python/gRPC
implementation prints the same summary schema. The summary reports
`dropped_jobs` separately, and standard error contains one detailed record for
each dropped job.

## Python/gRPC implementation

`grpc_performance_dispatch.py` implements the same input model and online
dispatch policy through independent gRPC servers. The MPI launcher only starts
the controller and servers; the client script owns sampling and dispatch:

```bash
python3 python/grpc_mpi_launcher.py --mpi-ranks 6 \
  --server-binary build/dr_evt_server \
  --client-script experimental/multi-cluster/grpc_performance_dispatch.py -- \
  --jobs multi-cluster/job_stream.csv \
  --ground-truth experimental/multi-cluster/ground_truth.csv \
  --prediction experimental/multi-cluster/prediction.csv \
  --applications experimental/multi-cluster/apps.csv \
  --systems experimental/multi-cluster/machines.csv \
  --seed 7 \
  --max-time-limit 43200 \
  --dispatch-policy IPDPS24 \
  --wall-time-policy adapted-limit \
  --output grpc-dispatch-decisions.csv
```

The same seed makes each implementation reproducible, but does not produce the
same sampled sequence across implementations because Python and C++ use
different random-number engines. Both implement uniform application sampling
followed by uniform workload sampling.

Run the Python policy tests directly:

```bash
source docs/venv/bin/activate
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
