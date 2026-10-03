#!/bin/bash
# End-to-end test of the example client's Redis-first status lookup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test_reporting.sh"
test_report_enable
INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-$REPO_ROOT/install}"
if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi
TEST_WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-redis-grpc.XXXXXXXX")
SERVER_PID=""
REDIS_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "$REDIS_PID" ]; then
        redis-cli -h 127.0.0.1 -p "${REDIS_PORT:-6391}" SHUTDOWN NOSAVE \
            >/dev/null 2>&1 || true
        wait "$REDIS_PID" 2>/dev/null || true
    fi
    rm -rf -- "$TEST_WORK_DIR"
}
test_report_set_cleanup cleanup

SERVER_BIN=${DR_EVT_SERVER:-"$INSTALL_PREFIX/bin/dr_evt_server"}
CLIENT_BIN=${DR_EVT_CLIENT:-"$INSTALL_PREFIX/bin/dr_evt_client"}
SERVER_PORT=${DR_EVT_SERVER_PORT:-53991}
REDIS_PORT=${DR_EVT_REDIS_PORT:-6391}
REDIS_PREFIX="dr_evt:grpc-client-example"
TRACE="$REPO_ROOT/tests/test_traces/unit/simple_2jobs.csv"

for command_name in redis-server redis-cli; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Error: required command not found: $command_name" >&2
        exit 1
    fi
done
for executable in "$SERVER_BIN" "$CLIENT_BIN"; do
    if [ ! -x "$executable" ]; then
        echo "Error: executable not found: $executable" >&2
        echo "Install the project first, or set CMAKE_INSTALL_PREFIX to its install prefix." >&2
        exit 1
    fi
done

redis-server --bind 127.0.0.1 --port "$REDIS_PORT" --save "" \
    --appendonly no --dir "$TEST_WORK_DIR" \
    --logfile "$TEST_WORK_DIR/redis.log" &
REDIS_PID=$!
for _ in $(seq 1 50); do
    if redis-cli -h 127.0.0.1 -p "$REDIS_PORT" PING 2>/dev/null |
        grep -q PONG; then
        break
    fi
    sleep 0.1
done
if ! redis-cli -h 127.0.0.1 -p "$REDIS_PORT" PING 2>/dev/null |
    grep -q PONG; then
    echo "Error: Redis did not become ready" >&2
    exit 1
fi

(cd "$TEST_WORK_DIR" && exec "$SERVER_BIN" "127.0.0.1:$SERVER_PORT") \
    >"$TEST_WORK_DIR/server.log" 2>&1 &
SERVER_PID=$!
sleep 1

if ! "$CLIENT_BIN" "127.0.0.1:$SERVER_PORT" "$TRACE" \
    --redis-uri "redis://127.0.0.1:$REDIS_PORT" \
    --redis-key-prefix "$REDIS_PREFIX" --advance-to 100 \
    >"$TEST_WORK_DIR/client.log" 2>&1; then
    sed 's/^/server: /' "$TEST_WORK_DIR/server.log" >&2
    sed 's/^/client: /' "$TEST_WORK_DIR/client.log" >&2
    exit 1
fi

COMPLETED_LINE=$(grep -n '^Job 0: completed (Redis' \
    "$TEST_WORK_DIR/client.log" | cut -d: -f1)
RUNNING_LINE=$(grep -n '^Job 1: running (server' \
    "$TEST_WORK_DIR/client.log" | cut -d: -f1)
if [ -z "$COMPLETED_LINE" ] || [ -z "$RUNNING_LINE" ] || \
    [ "$COMPLETED_LINE" -ge "$RUNNING_LINE" ]; then
    echo "Error: merged Redis/server statuses are missing or out of order" >&2
    cat "$TEST_WORK_DIR/client.log" >&2
    exit 1
fi

if [ "$(redis-cli --raw -h 127.0.0.1 -p "$REDIS_PORT" \
    SCARD "$REDIS_PREFIX:job_ids")" != "2" ]; then
    echo "Error: FinishSimulation did not flush both jobs to Redis" >&2
    exit 1
fi

echo "Redis gRPC client example test passed"
