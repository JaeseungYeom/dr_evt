#!/usr/bin/env bash
# Run the gRPC backfill-window API test against a fresh local server.
#
# The test executable also retains its append-job coverage; its third test is
# GetBackfillWindowRequest, which verifies a FCFS/EASY reservation snapshot
# with releases at (50, 40) and (100, 60).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-$REPO_ROOT/install}"
if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi

SERVER="$INSTALL_PREFIX/bin/dr_evt_server"
TEST_BIN="$INSTALL_PREFIX/bin/tests/test_grpc_streaming_api"

TRACE="$REPO_ROOT/tests/test_traces/feature/empty_trace.csv"
PORT="${DR_EVT_BACKFILL_WINDOW_TEST_PORT:-53211}"

if [[ ! -x "$SERVER" || ! -x "$TEST_BIN" ]]; then
    echo "Missing dr_evt_server or test_grpc_streaming_api." >&2
    echo "Build with: cmake --build build --target dr_evt_server-bin test_grpc_streaming_api-bin" >&2
    exit 1
fi

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-backfill-window.XXXXXXXX")"
SERVER_PID=""
cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$RUN_DIR"
}
trap cleanup EXIT

(cd "$RUN_DIR" && exec "$SERVER" "127.0.0.1:${PORT}") \
    >"$RUN_DIR/server.log" 2>&1 &
SERVER_PID=$!
sleep 1

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "dr_evt_server failed to start. Server log:" >&2
    sed 's/^/  /' "$RUN_DIR/server.log" >&2
    exit 1
fi

echo "Running GetBackfillWindowRequest test on 127.0.0.1:${PORT}"
if ! "$TEST_BIN" "127.0.0.1:${PORT}" "$TRACE"; then
    echo "dr_evt_server log:" >&2
    sed 's/^/  /' "$RUN_DIR/server.log" >&2
    exit 1
fi
