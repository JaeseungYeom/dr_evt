# Performance-aware multi-cluster dispatch experiment

This study routes an arrival stream among independent DR_EVT systems by
predicted turnaround time. It is experimental code rather than a supported
general-purpose client.

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

This requires `mpi4py`, `grpcio`, `grpcio-tools`, and `protobuf`. The MPI
launcher uses rank 0 for the controller and one non-root rank per server.

Run the experiment's focused unit tests directly:

```bash
python3 experimental/multi-cluster/test_performance_dispatch.py
```
