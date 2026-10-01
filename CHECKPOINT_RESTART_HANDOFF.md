# Simulation checkpoint/restart handoff

## Outcome

DR_EVT can save and restore the exact logical state of a standard simulation
through `Simulation::save_checkpoint()` and `Simulation::load_checkpoint()`.
Both stream and filename overloads are available when Ser20 is enabled. Python
exposes filename-based methods, and gRPC exposes binary save/load messages.

Checkpoint files are same-build binary artifacts. Loading validates the format
version, `Job_Record` ABI, scheduler/runtime/capacity settings, and output paths
before reconstructing scheduler ownership and resuming output in append mode.

## State included

- Resident job records and reclaimed-job offset
- Scheduler-owned pending job IDs, running jobs, and simulation event queues
- Simulation time, capacities, utilization areas, and queue statistics
- Trace completion, wait-time, turnaround, makespan, and output cursors
- Resource history and incremental output state
- Random-number generator state through Ser20

Scheduler pending IDs are stored explicitly because a loaded trace record is
not necessarily submitted to the scheduler. Restore validates that every saved
ID is unique, resident, and pending before rebuilding the selected queue.

## Supported and rejected modes

Supported: standard simulation-mode traces, standard FCFS/SJF/LJF schedulers,
and callback-based Custom FCFS when the restart instance is constructed with
equivalent callbacks. Queue entries, computed costs, candidate state, and
accounting are restored without calling the cost callback during load; the
callbacks and any externally owned state are not serialized.

Rejected: Custom FCFS subclasses that may contain additional unknown state,
experimental Pcon traces, replay or warm-start execution, and progressive file
loading. The native and Python APIs are omitted when
`DR_EVT_WITH_SER20=OFF`; gRPC retains a stable schema and returns an error for
checkpoint requests in that configuration.

## Tests and CI

`tests/test_checkpoint_restart.cpp` covers:

- Exact uninterrupted-versus-restored continuation of deterministic 256-job
  workloads, followed by 256 additional post-checkpoint jobs, for circular,
  deque, multimap, and block FCFS queues plus SJF, LJF, and callback-based
  Custom FCFS
- Checkpoint boundaries containing completed, running, waiting, and future jobs
- Custom FCFS restore without recomputing saved job costs
- Preservation of loaded-but-unsubmitted jobs
- Reclaimed record handling and byte-identical job/resource CSV output versus
  uninterrupted execution
- Configuration mismatch rejection

The test target and CTest registration are conditional on Ser20. CI explicitly
builds it, runs it through the core CTest step, and reports the updated native
and Python test counts. `tests/README.md` documents the coverage.

## Verification performed

- Ser20 native checkpoint/restart test: passed
- Existing append-job native suite with Ser20 enabled: passed
- Ser20-disabled core build and append-job suite: passed
- Python API suite, including checkpoint/restart: 19/19 passed
- gRPC server build with generated protobuf/gRPC sources: passed
- Protobuf schema validation with system `protoc`: passed
- Checkpoint CTest registration and focused execution: passed
- `git diff --check`: passed

The full CTest matrix was not run; the focused checkpoint registration was.

## Suggested commit message

```text
feat: add Ser20 simulation checkpoint and restart

Add exact same-build persistence for simulation state through stream/file C++
APIs, Python filename bindings, and binary gRPC requests.

Serialize resident jobs, scheduler-owned pending IDs, running jobs, event
queues, capacity/accounting state, trace statistics, resource history, output
cursors, RNG state, and callback-based Custom FCFS queue/accounting state.
Validate checkpoint identity and configuration on load, rebuild standard
scheduler queues deterministically, retain caller-supplied custom callbacks,
and reopen active outputs in append mode. Reject modes whose state cannot yet
be restored exactly.

Add Doxygen documentation for every new C++ method and document the native,
Python, and client/server contracts and limitations.

Add Ser20-conditional native regression coverage that compares uninterrupted
and restarted 256-job workloads for every standard scheduler and callback-based
Custom FCFS. Cover loaded-but-unsubmitted jobs, byte-identical job/resource
output after reclaimed output continuation, and configuration mismatch. Extend
Python coverage and update CMake, CTest, CI build targets, reported test counts,
and test documentation.
```

## Workspace note

The worktree is intentionally uncommitted. The existing `HANDOFF.md` describes
separate multi-cluster dispatcher work and was not modified. Preserve unrelated
untracked build directories, core files, and local support paths.
