#!/bin/bash
# CLI/runtime rejection tests for invalid warm-start configurations.

set -u

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <simulator>" >&2
    exit 2
fi

SIMULATOR="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-warm-validation.XXXXXXXX")"
trap 'rm -rf -- "$WORK_DIR"' EXIT INT TERM

PASS=0
FAIL=0

expect_failure() {
    name="$1"
    expected="$2"
    shift 2
    log="$WORK_DIR/$name.log"
    if "$@" >"$log" 2>&1; then
        echo "  ✗ $name unexpectedly succeeded"
        FAIL=$((FAIL + 1))
    elif grep -q -- "$expected" "$log"; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
    else
        echo "  ✗ $name failed without expected diagnostic: $expected"
        sed 's/^/    /' "$log"
        FAIL=$((FAIL + 1))
    fi
}

expect_success() {
    name="$1"
    shift
    log="$WORK_DIR/$name.log"
    if "$@" >"$log" 2>&1; then
        echo "  ✓ $name"
        PASS=$((PASS + 1))
    else
        echo "  ✗ $name failed"
        sed 's/^/    /' "$log"
        FAIL=$((FAIL + 1))
    fi
}

cd "$REPO_ROOT"
REPLAY="tests/test_traces/feature/warm_start_native.csv"
SIMULATION="tests/test_traces/unit/simple_basic.csv"

expect_failure negative_start "must be finite and nonnegative" \
    "$SIMULATOR" "$REPLAY" --sim_start_time -1
expect_failure nan_start "must be finite and nonnegative" \
    "$SIMULATOR" "$REPLAY" --sim_start_time nan
expect_failure infinite_start "must be finite and nonnegative" \
    "$SIMULATOR" "$REPLAY" --sim_start_time inf
expect_failure negative_max_time "--max_time must be finite and nonnegative" \
    "$SIMULATOR" "$REPLAY" --max_time -1
expect_failure max_before_start "--max_time must be greater than or equal" \
    "$SIMULATOR" "$REPLAY" --sim_start_time 50 --max_time 49
expect_failure non_replay_input "requires replay-format input" \
    "$SIMULATOR" "$SIMULATION" --sim_start_time 1 --run_time_mode limit

printf '%s\n' "$SIMULATION" >"$WORK_DIR/traces.list"
expect_failure infile_list "not supported with --infile_list" \
    "$SIMULATOR" --infile_list "$WORK_DIR/traces.list" --sim_start_time 1 \
    --run_time_mode limit

ISO_REPLAY="$WORK_DIR/iso_replay.csv"
cat >"$ISO_REPLAY" <<'EOF'
job_submit_time,begin_time,end_time,num_nodes,exit_status,time_limit
2024-01-01T00:00:00,2024-01-01T00:00:01,2024-01-01T00:00:20,1,0,19
2024-01-01T00:00:10,2024-01-01T00:00:12,2024-01-01T00:00:14,1,0,2
EOF
expect_success iso_sim_start_time \
    "$SIMULATOR" "$ISO_REPLAY" \
    --sim_start_time "2024-01-01T00:00:10" \
    --timezone UTC --run_time_mode actual \
    --total_nodes 2 --outfile "$WORK_DIR/iso_jobs.csv" \
    --resource_trace "$WORK_DIR/iso_resources.csv"

MIXED_TIMESTAMPS="$WORK_DIR/mixed_timestamps.csv"
cat >"$MIXED_TIMESTAMPS" <<'EOF'
job_submit_time,num_nodes,time_limit
2024-01-01T00:00:00,1,1
1704067201,1,1
EOF
expect_failure mixed_timestamp_encoding "Failed to parse time string" \
    "$SIMULATOR" "$MIXED_TIMESTAMPS" --run_time_mode limit \
    --timezone UTC --total_nodes 1

echo "Warm-start validation: $PASS passed, $FAIL failed"
test "$FAIL" -eq 0
