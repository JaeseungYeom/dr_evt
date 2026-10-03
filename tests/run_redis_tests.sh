#!/bin/bash
# End-to-end Redis output test. Starts an isolated Redis server, runs the
# simulator, and validates the complete CSV, per-job hashes, sorted indexes,
# namespace replacement, batched job flushes, a pipelined multi-job lookup, and byte-identical
# output compared with the file sink for a 200-job simulation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test_reporting.sh"
test_report_enable
cd "$REPO_ROOT"

# shellcheck source=tests/set_simulator_path.sh
# shellcheck disable=SC1091
source "$SCRIPT_DIR/set_simulator_path.sh"
source "$SCRIPT_DIR/select_python.sh"

for command_name in redis-server redis-cli; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Error: required command not found: $command_name" >&2
        exit 1
    fi
done
if ! select_python_interpreter 3 6; then
    echo "Error: Python 3.6 or newer is required" >&2
    exit 1
fi

if ! TEST_WORK_DIR=$(mktemp -d \
    "${TMPDIR:-/tmp}/dr-evt-redis.XXXXXXXX" 2>/dev/null); then
    TEST_WORK_DIR=$(mktemp -d "/tmp/dr-evt-redis.XXXXXXXX")
fi
REDIS_PID=""

cleanup() {
    exit_code="${1:-$?}"
    if [ -n "$REDIS_PID" ]; then
        kill "$REDIS_PID" 2>/dev/null || true
        wait "$REDIS_PID" 2>/dev/null || true
    fi
    if [ "$exit_code" -ne 0 ]; then
        echo "Redis server log:" >&2
        sed 's/^/  /' "$TEST_WORK_DIR/redis.log" >&2 2>/dev/null || true
        echo "Simulator log:" >&2
        sed 's/^/  /' "$TEST_WORK_DIR/simulator.log" >&2 2>/dev/null || true
    fi
    rm -rf -- "$TEST_WORK_DIR"
}
test_report_set_cleanup cleanup

# Ask the kernel for an unused loopback port. Redis is started immediately
# afterward, keeping the small bind/start race local to this isolated test.
REDIS_PORT=${DR_EVT_TEST_REDIS_PORT:-$("$PYTHON_BIN" -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}
REDIS_HOST=127.0.0.1
REDIS_URI="redis://${REDIS_HOST}:${REDIS_PORT}"
REDIS_PREFIX="dr_evt:test:${REDIS_PORT}"

redis-server \
    --bind "$REDIS_HOST" \
    --port "$REDIS_PORT" \
    --save "" \
    --appendonly no \
    --dir "$TEST_WORK_DIR" \
    --logfile "$TEST_WORK_DIR/redis.log" &
REDIS_PID=$!

redis_command() {
    redis-cli --no-auth-warning -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
}

redis_ready=false
for _ in $(seq 1 50); do
    if [ "$(redis_command PING 2>/dev/null || true)" = "PONG" ]; then
        redis_ready=true
        break
    fi
    sleep 0.1
done
if [ "$redis_ready" != true ]; then
    echo "Error: Redis server did not become ready" >&2
    exit 1
fi

assert_equal() {
    label=$1
    expected=$2
    actual=$3
    if [ "$actual" != "$expected" ]; then
        echo "FAIL: $label" >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        exit 1
    fi
}

# Seed the namespace to verify that opening a run replaces all job data owned
# by a previous run with the same prefix.
redis_command HSET "$REDIS_PREFIX:job:stale" job_id stale >/dev/null
redis_command SADD "$REDIS_PREFIX:job_ids" stale >/dev/null
redis_command ZADD "$REDIS_PREFIX:by_submit" 999 stale >/dev/null
redis_command HSET "$REDIS_PREFIX:resource:stale" sample_id stale >/dev/null
redis_command ZADD "$REDIS_PREFIX:resources:by_time" 999 stale >/dev/null

UNUSED_OUTFILE="$TEST_WORK_DIR/should-not-be-created.csv"
"$SIMULATOR" tests/test_traces/unit/simple_2jobs.csv \
    --total_nodes 100 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --priority_policy fcfs \
    --outfile "$UNUSED_OUTFILE" \
    --resource_trace "$TEST_WORK_DIR/resources.csv" \
    --redis_uri "$REDIS_URI" \
    --redis_key_prefix "$REDIS_PREFIX" \
    --job_flush_interval 1 >"$TEST_WORK_DIR/simulator.log" 2>&1

if [ -e "$UNUSED_OUTFILE" ]; then
    echo "FAIL: Redis output unexpectedly created --outfile" >&2
    exit 1
fi
if [ -e "$TEST_WORK_DIR/resources.csv" ]; then
    echo "FAIL: Redis output unexpectedly created --resource_trace file" >&2
    exit 1
fi

EXPECTED_CSV=$'job_submit_time,begin_time,end_time,num_nodes,exit_status,time_limit\n0,0,100,60,0,100\n0,100,120,60,0,20'
assert_equal "complete CSV" "$EXPECTED_CSV" \
    "$(redis_command --raw GET "$REDIS_PREFIX:csv")"

assert_equal "job count" "2" "$(redis_command SCARD "$REDIS_PREFIX:job_ids")"
assert_equal "old job hash removed" "0" \
    "$(redis_command EXISTS "$REDIS_PREFIX:job:stale")"
assert_equal "old job ID removed" "0" \
    "$(redis_command SISMEMBER "$REDIS_PREFIX:job_ids" stale)"
assert_equal "unwritten job absent" "0" \
    "$(redis_command EXISTS "$REDIS_PREFIX:job:2")"
assert_equal "old resource hash removed" "0" \
    "$(redis_command EXISTS "$REDIS_PREFIX:resource:stale")"
assert_equal "resource sample count" "5" \
    "$(redis_command ZCARD "$REDIS_PREFIX:resources:by_time")"

assert_equal "job 0 fields" $'0\n0\n100\n60\n0\n100' \
    "$(redis_command --raw HMGET "$REDIS_PREFIX:job:0" \
        job_submit_time begin_time end_time num_nodes exit_status time_limit)"
assert_equal "job 1 fields" $'0\n100\n120\n60\n0\n20' \
    "$(redis_command --raw HMGET "$REDIS_PREFIX:job:1" \
        job_submit_time begin_time end_time num_nodes exit_status time_limit)"

assert_equal "submission-time index" $'0\n1' \
    "$(redis_command --raw ZRANGEBYSCORE "$REDIS_PREFIX:by_submit" 0 0)"
assert_equal "start-time index" "1" \
    "$(redis_command --raw ZRANGEBYSCORE "$REDIS_PREFIX:by_start" 100 100)"
assert_equal "completion-time index" "0" \
    "$(redis_command --raw ZRANGEBYSCORE "$REDIS_PREFIX:by_completion" 100 100)"
assert_equal "resource index" $'0\n1' \
    "$(redis_command --raw ZRANGEBYSCORE "$REDIS_PREFIX:by_resources" 60 60)"
assert_equal "resource time index preserves duplicate times" $'0\n1' \
    "$(redis_command --raw ZRANGEBYSCORE \
        "$REDIS_PREFIX:resources:by_time" 0 0)"
assert_equal "resource sample 0 fields" $'0\n100\n0' \
    "$(redis_command --raw HMGET "$REDIS_PREFIX:resource:0" \
        time free_nodes allocated_nodes)"
assert_equal "resource sample 1 fields" $'0\n40\n60' \
    "$(redis_command --raw HMGET "$REDIS_PREFIX:resource:1" \
        time free_nodes allocated_nodes)"

# Pcon resource fields remain typed at the C++ boundary and are serialized as
# separately queryable hash fields instead of being available only in CSV.
PCON_PREFIX="$REDIS_PREFIX:pcon"
"$SIMULATOR" tests/test_traces/pcon_cli_input.csv \
    --trace_type pcon \
    --total_nodes 4 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --priority_policy fcfs \
    --outfile "$TEST_WORK_DIR/pcon_unused_output.csv" \
    --resource_trace "$TEST_WORK_DIR/pcon_unused_resources.csv" \
    --redis_uri "$REDIS_URI" \
    --redis_key_prefix "$PCON_PREFIX" >>"$TEST_WORK_DIR/simulator.log" 2>&1
assert_equal "Pcon resource fields" $'1.500000\n2.000000\n3.000000' \
    "$(redis_command --raw HMGET "$PCON_PREFIX:resource:1" \
        avgpcon minpcon maxpcon)"

# Send both HGETALL operations in one Redis protocol stream. Field values were
# checked above; here --pipe verifies that the bulk request receives two
# successful replies without one connection/round trip per job.
JOB_0_KEY="$REDIS_PREFIX:job:0"
JOB_1_KEY="$REDIS_PREFIX:job:1"
BULK_RESULT=$(
    {
        printf "*2\r\n\$7\r\nHGETALL\r\n\$%d\r\n%s\r\n" \
            "${#JOB_0_KEY}" "$JOB_0_KEY"
        printf "*2\r\n\$7\r\nHGETALL\r\n\$%d\r\n%s\r\n" \
            "${#JOB_1_KEY}" "$JOB_1_KEY"
    } | redis-cli --no-auth-warning -h "$REDIS_HOST" -p "$REDIS_PORT" --pipe
)
if ! grep -Eq 'errors: 0, replies: 2' <<<"$BULK_RESULT"; then
    echo "FAIL: pipelined bulk lookup did not return two successful replies" >&2
    echo "$BULK_RESULT" >&2
    exit 1
fi

# Stop at the boundary where job 0 has completed and job 1 has just started.
# This verifies the Redis-first/live-status contract: finalized job 0 is
# searchable, while unfinished job 1 is absent and must be queried through
# Simulation::get_job_statuses() by an in-process or RPC caller.
PARTIAL_PREFIX="$REDIS_PREFIX:partial"
"$SIMULATOR" tests/test_traces/unit/simple_2jobs.csv \
    --total_nodes 100 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --priority_policy fcfs \
    --max_time 100 \
    --resource_trace "$TEST_WORK_DIR/partial_resources.csv" \
    --redis_uri "$REDIS_URI" \
    --redis_key_prefix "$PARTIAL_PREFIX" \
    --job_flush_interval 1 >>"$TEST_WORK_DIR/simulator.log" 2>&1
assert_equal "finalized job searchable at partial boundary" "1" \
    "$(redis_command EXISTS "$PARTIAL_PREFIX:job:0")"
assert_equal "unfinished job absent at partial boundary" "0" \
    "$(redis_command EXISTS "$PARTIAL_PREFIX:job:1")"

# Run the same 200-job workload once through the ordinary file sink and once
# through Redis. Export the Redis CSV while the server is still available and
# require byte-for-byte equality before cleanup shuts the server down.
LARGE_TRACE=tests/test_traces/scale/large_200jobs.csv
FILE_OUTPUT="$TEST_WORK_DIR/large_file_output.csv"
REDIS_RAW_OUTPUT="$TEST_WORK_DIR/large_redis_raw.txt"
REDIS_CSV_OUTPUT="$TEST_WORK_DIR/large_redis_output.csv"
REDIS_RESOURCE_RAW_OUTPUT="$TEST_WORK_DIR/large_redis_resources_raw.txt"
REDIS_RESOURCE_CSV_OUTPUT="$TEST_WORK_DIR/large_redis_resources.csv"
LARGE_PREFIX="$REDIS_PREFIX:large"

"$SIMULATOR" "$LARGE_TRACE" \
    --total_nodes 795 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --priority_policy fcfs \
    --outfile "$FILE_OUTPUT" \
    --resource_trace "$TEST_WORK_DIR/large_file_resources.csv" \
    --job_flush_interval 17 >>"$TEST_WORK_DIR/simulator.log" 2>&1

"$SIMULATOR" "$LARGE_TRACE" \
    --total_nodes 795 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --priority_policy fcfs \
    --outfile "$TEST_WORK_DIR/large_unused_output.csv" \
    --resource_trace "$TEST_WORK_DIR/large_unused_resources.csv" \
    --redis_uri "$REDIS_URI" \
    --redis_key_prefix "$LARGE_PREFIX" \
    --job_flush_interval 17 >>"$TEST_WORK_DIR/simulator.log" 2>&1

# redis-cli terminates a raw bulk-string reply with one additional newline.
# Remove that delimiter while retaining the CSV's own final newline.
redis_command --raw GET "$LARGE_PREFIX:csv" >"$REDIS_RAW_OUTPUT"
head -c -1 "$REDIS_RAW_OUTPUT" >"$REDIS_CSV_OUTPUT"
redis_command --raw GET "$LARGE_PREFIX:resources:csv" \
    >"$REDIS_RESOURCE_RAW_OUTPUT"
head -c -1 "$REDIS_RESOURCE_RAW_OUTPUT" >"$REDIS_RESOURCE_CSV_OUTPUT"

if [ -e "$TEST_WORK_DIR/large_unused_output.csv" ] ||
        [ -e "$TEST_WORK_DIR/large_unused_resources.csv" ]; then
    echo "FAIL: Redis mode unexpectedly created a job or resource output file" >&2
    exit 1
fi

assert_equal "large Redis job count" "200" \
    "$(redis_command SCARD "$LARGE_PREFIX:job_ids")"

CHECKPOINT_TEST=${CHECKPOINT_RESTART_TEST:-$INSTALL_PREFIX/bin/tests/test_checkpoint_restart}
if [ ! -x "$CHECKPOINT_TEST" ] &&
        [ -x "$(dirname "$SIMULATOR")/test_checkpoint_restart" ]; then
    CHECKPOINT_TEST="$(dirname "$SIMULATOR")/test_checkpoint_restart"
fi
if [ -x "$CHECKPOINT_TEST" ]; then
    DR_EVT_TEST_REDIS_URI="$REDIS_URI" \
    DR_EVT_TEST_REDIS_PREFIX="$REDIS_PREFIX" \
        "$CHECKPOINT_TEST" >>"$TEST_WORK_DIR/simulator.log" 2>&1
    CHECKPOINT_BASELINE="$REDIS_PREFIX:checkpoint-baseline"
    CHECKPOINT_RESTART="$REDIS_PREFIX:checkpoint-restart"
    assert_equal "stitched checkpoint job count" \
        "$(redis_command SCARD "$CHECKPOINT_BASELINE:job_ids")" \
        "$(redis_command SCARD "$CHECKPOINT_RESTART:job_ids")"
    assert_equal "stitched checkpoint submit index" \
        "$(redis_command --raw ZRANGE "$CHECKPOINT_BASELINE:by_submit" 0 -1)" \
        "$(redis_command --raw ZRANGE "$CHECKPOINT_RESTART:by_submit" 0 -1)"
    assert_equal "stitched checkpoint resource count" \
        "$(redis_command ZCARD "$CHECKPOINT_BASELINE:resources:by_time")" \
        "$(redis_command ZCARD "$CHECKPOINT_RESTART:resources:by_time")"
elif [ "${REQUIRE_CHECKPOINT_RESTART:-0}" = "1" ]; then
    echo "FAIL: checkpoint/restart test executable not found: $CHECKPOINT_TEST" >&2
    echo "Set CHECKPOINT_RESTART_TEST to its build-tree path if tests are not installed." >&2
    exit 1
else
    echo "SKIP: checkpoint/restart Redis test requires a Ser20-enabled build"
fi

# No more Redis queries occur after this point. Shut the server down and prove
# that the exported CSV remains independently usable for comparison.
redis_command SHUTDOWN NOSAVE
wait "$REDIS_PID"
REDIS_PID=""

assert_equal "large exported CSV line count" "201" \
    "$(wc -l <"$REDIS_CSV_OUTPUT" | tr -d ' ')"
if ! cmp -s "$FILE_OUTPUT" "$REDIS_CSV_OUTPUT"; then
    echo "FAIL: 200-job Redis CSV differs from ordinary file output" >&2
    diff -u "$FILE_OUTPUT" "$REDIS_CSV_OUTPUT" >&2 || true
    exit 1
fi
if ! cmp -s "$TEST_WORK_DIR/large_file_resources.csv" \
        "$REDIS_RESOURCE_CSV_OUTPUT"; then
    echo "FAIL: Redis resource CSV differs from the file-output counterpart" >&2
    diff -u "$TEST_WORK_DIR/large_file_resources.csv" \
        "$REDIS_RESOURCE_CSV_OUTPUT" >&2 || true
    exit 1
fi

echo "PASS: Redis output integration"
