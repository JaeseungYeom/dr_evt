# Performance-aware multi-cluster dispatch experiment

This study routes an arrival stream among independent DR_EVT systems by
predicted turnaround time.

## Job trace formats

Both dispatch policies read CSV jobs in nondecreasing `job_submit_time` order.
The common required columns are:

- `job_submit_time`: numeric arrival time in simulation seconds.
- `num_nodes`: positive integer node request.
- `time_limit`: positive numeric runtime limit in seconds.

`job_id` is optional and defaults to the zero-based row number. The queue
column is also optional and defaults to Queue1: its spelling is `q_id` in the
default build and `queue` in a build configured with
`DR_EVT_LEGACY_QUEUE_INPUT=ON`.

The platform-ranking baseline additionally requires `application_type`. For a
compact trace, use these numeric codes:

- `0` (`cpu-only`): may run only on a `cpu-only` system.
- `1` (`gpu-only`): may run only on a `gpu-enabled` system.
- `2` (`gpu-portable`): may run on `gpu-enabled` or `cpu-only` systems; prefers a
  GPU system within the configured wait-tolerance band.

The names in parentheses remain accepted for readability and compatibility.
Dispatch-decision output always uses those descriptive names.

For example:

```csv
job_id,job_submit_time,num_nodes,time_limit,application_type
job-1,0,20,200,0
job-2,10,30,150,1
job-3,20,15,300,2
```

The profile-based Python and native C++/MPI dispatchers do not read an
application system-requirement field. Their performance table must instead
contain a positive relative-performance value for every system, so every job
profile is considered compatible with every system large enough to hold its
`num_nodes` request. Use the platform-ranking baseline when jobs require hard
GPU/CPU compatibility constraints.

## Platform-ranking baseline

`grpc_baseline_dispatch.py` provides a simpler baseline without application-
specific speedup profiles. Each job row adds an `application_type` value from
`gpu-portable`, `gpu-only`, or `cpu-only`. Each server is paired with a
`--system-type` (`gpu-enabled` or `cpu-only`) and a positive ordinal
`--performance-rank`; rank 1 is fastest within its platform type.

The baseline filters incompatible systems, finds the shortest estimated wait,
and treats systems within `--wait-tolerance` seconds as similarly available.
Performance rank selects among those systems. A GPU-portable application uses
a GPU-enabled system when one is within that wait band and otherwise falls
back to a CPU-only system. Set `--wait-tolerance 0` for the wait-only case:
only systems tied for the shortest estimated wait are compared by rank.

```bash
mpirun -np 4 python3 python/grpc_mpi_launcher.py \
  --server-binary "${CMAKE_INSTALL_PREFIX}/bin/dr_evt_server" \
  --client-script experimental/multi-cluster/grpc_baseline_dispatch.py -- \
  --jobs /shared/typed-jobs.csv \
  --system-id cpu-a --system-type cpu-only --performance-rank 1 \
  --system-id gpu-a --system-type gpu-enabled --performance-rank 2 \
  --system-id gpu-b --system-type gpu-enabled --performance-rank 1 \
  --total-nodes 100 --wait-tolerance 60 \
  --output baseline-decisions.csv
```
Use `srun` in place of `mpirun` in case of a Slurm environment.

## Profile-based dispatcher

For each arriving job, `grpc_performance_dispatch.py` finds the nearest row in
`performance_table.csv` using range-normalized Euclidean distance over node
count and time limit. It queries each server's backfill snapshot and queue
prediction horizon, then minimizes:

```text
predicted turnaround = predicted wait + time_limit / relative_performance
```

The performance table starts with `profile_id,num_nodes,time_limit`, followed
by one column per system. Each system value is a positive relative speed: `2.0`
means twice the baseline speed and half the processing time.

Run one controller and three simulation servers with:

```bash
mpirun -np 4 python3 python/grpc_mpi_launcher.py \
  --server-binary "${CMAKE_INSTALL_PREFIX}/bin/dr_evt_server" \
  --client-script experimental/multi-cluster/grpc_performance_dispatch.py -- \
  --jobs python/examples/sample_trace.csv \
  --performance-table experimental/multi-cluster/performance_table.csv \
  --system-id system-1 --system-id system-2 --system-id system-3 \
  --total-nodes 100 --prediction-utilization 1.0 \
  --output dispatch-decisions.csv
```
Use `srun` in case of a Slurm environment.

This requires `mpi4py`, `grpcio`, `grpcio-tools`, and `protobuf`. The MPI
launcher uses rank 0 for the controller and one non-root rank per server.

### Native C++/MPI dispatcher

`mpi_performance_dispatch` implements the same profile-based policy without
gRPC or Python. Rank 0 is the controller and each remaining rank owns a DR_EVT
`Simulation`. Ser20 serializes only MPI commands, submitted jobs, backfill
query results, and final statistics; simulation state remains local to its
worker rank.

Build with MPI and Ser20 enabled, then run one controller and three simulation
workers:

```bash
mpirun -np 4 "${CMAKE_INSTALL_PREFIX}/bin/mpi_performance_dispatch" \
  --jobs python/examples/sample_trace.csv \
  --performance-table experimental/multi-cluster/performance_table.csv \
  --system-id system-1 --system-id system-2 --system-id system-3 \
  --system-nodes 100 --system-nodes 200 --system-nodes 50 \
  --prediction-utilization 0.95 \
  --output dispatch-decisions.csv
```

Use `srun` in place of `mpirun` on a Slurm allocation. The executable requires
exactly one worker rank per `--system-id`; optional repeated `--system-nodes`
values override `--total-nodes` for heterogeneous capacities.

With the sample trace above, `system-3` receives no jobs: the trace selects
only the short-small and medium profiles, where systems 1 and 2 respectively
have the best relative performance. This is an intentional test case rather
than a load-balancing guarantee. The focused unit test also appends a 45-node,
900-second job, which selects the large-long profile and fits the 50-node third
system; that augmented workload verifies that all three systems are selected.

Run the experiment's focused unit tests directly:

```bash
python3 experimental/multi-cluster/test_performance_dispatch.py
python3 experimental/multi-cluster/test_baseline_dispatch.py
```
