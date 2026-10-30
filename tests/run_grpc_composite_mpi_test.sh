#!/usr/bin/env bash
# Run the two-server composite-stream gRPC integration test. CI (or the
# caller) builds and installs the binaries before this script runs.
# The test requires one node and four tasks: two server ranks plus their
# paired client ranks.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_DIR="$REPO_ROOT/tests"
source "$SCRIPT_DIR/test_reporting.sh"
test_report_enable
INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX:-"$REPO_ROOT/install"}
if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi
PORT=${DR_EVT_COMPOSITE_TEST_PORT:-55100}
if [ -n "${DR_EVT_COMPOSITE_TEST_RUN_DIR:-}" ]; then
    RUN_DIR=$DR_EVT_COMPOSITE_TEST_RUN_DIR
elif ! RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-composite.XXXXXX" 2>/dev/null); then
    RUN_DIR=$(mktemp -d "/tmp/dr-evt-composite.XXXXXX")
fi
SERVER="$INSTALL_PREFIX/bin/dr_evt_server"
MPI_TEST="$INSTALL_PREFIX/bin/tests/test_grpc_multi_client_server"

if command -v mpirun >/dev/null 2>&1; then
    # CI runners may provide fewer than four Open MPI slots (Open MPI 5
    # counts physical cores).  Allow the four local ranks required by this
    # test without passing Open MPI-only options to another implementation.
    #
    # Detect Open MPI through ompi_info first: the Ubuntu 26.04 Open MPI
    # 5.0.10 package prints only a "couldn't open the help file" message for
    # `mpirun --version`, which would otherwise hide the Open MPI launcher
    # and make the launch fail with prte-rmaps-base:alloc-error.
    if { command -v ompi_info >/dev/null 2>&1 && \
         ompi_info --version 2>/dev/null | grep -qi "Open MPI"; } || \
       mpirun --version 2>&1 | grep -qi "Open MPI"; then
        MPI_LAUNCHER=(mpirun --oversubscribe -np 4)
        # Open MPI 4 reads the OMPI_ setting; Open MPI 5 (PRRTE) reads the
        # PRTE_ mapping policy. Each ignores the other.
        export OMPI_MCA_rmaps_base_oversubscribe=1
        export PRTE_MCA_rmaps_default_mapping_policy=:oversubscribe
    else
        MPI_LAUNCHER=(mpirun -np 4)
    fi
    MPI_LAUNCHER_NAME="mpirun"
elif command -v srun >/dev/null 2>&1; then
    MPI_LAUNCHER=(srun --nodes=1 --ntasks=4 --kill-on-bad-exit=1)
    MPI_LAUNCHER_NAME="srun"
else
    echo "error: this runner requires mpirun or srun on PATH" >&2
    exit 2
fi

mkdir -p "$RUN_DIR"
# MPI launchers may reset a rank's working directory. Resolve this before
# launching so every rank (and each server child it forks) writes reports in
# the selected run directory, even when a relative override was supplied.
RUN_DIR=$(cd "$RUN_DIR" && pwd)

for binary in "$SERVER" "$MPI_TEST"; do
    if [ ! -x "$binary" ]; then
        echo "error: required prebuilt binary is missing or not executable: $binary" >&2
        echo "       build and install the project with gRPC and MPI support before running this test" >&2
        exit 2
    fi
done

echo "Running composite-stream test with $MPI_LAUNCHER_NAME in: $RUN_DIR"
"${MPI_LAUNCHER[@]}" /bin/bash -c '
    cd "$1"
    shift
    exec "$@"
' run-composite-rank "$RUN_DIR" "$MPI_TEST" \
    "$SERVER" \
    "$PORT" \
    "$REPO_ROOT/tests/test_traces/grpc/composite_server1.csv" \
    "$REPO_ROOT/tests/test_traces/grpc/composite_server2.csv" \
    "$REPO_ROOT/tests/test_traces/grpc/composite_jobs.csv"

echo "PASS: composite-stream test completed. Session reports are in: $RUN_DIR"
