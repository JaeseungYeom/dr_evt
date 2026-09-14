#!/usr/bin/env bash
# Check callback-driven circular EASY scheduling against a reference schedule.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-$REPO_ROOT/install}"
if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi

TEST_BIN="${CUSTOM_SCHEDULER_TEST:-$INSTALL_PREFIX/bin/tests/test_custom_scheduler}"
if [[ "$TEST_BIN" != /* ]]; then
    TEST_BIN="$REPO_ROOT/${TEST_BIN#./}"
fi
if [[ ! -x "$TEST_BIN" ]]; then
    echo "Custom scheduler test executable not found: $TEST_BIN" >&2
    echo "Build/install test_custom_scheduler-bin, or set CUSTOM_SCHEDULER_TEST." >&2
    exit 1
fi

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-custom-scheduler.XXXXXXXX")"
trap 'rm -rf -- "$RUN_DIR"' EXIT INT TERM

echo "Running custom scheduler unit checks"
"$TEST_BIN"

compare_schedule() {
    local name="$1"
    local trace="$2"
    local expected="$3"
    local total_nodes="$4"
    local raw_output="$RUN_DIR/$name.csv"
    local actual="$RUN_DIR/$name.comparable.csv"

    "$TEST_BIN" --write-schedule "$trace" "$raw_output" "$total_nodes"
    awk -F, 'NR == 1 { print "job_id,start_time,end_time"; next }
             { print NR-2 "," $2 "," $3 }' "$raw_output" > "$actual"

    echo "Comparing $name custom schedule with reference output"
    if ! diff -u "$expected" "$actual"; then
        echo "Custom scheduler output does not match EASY reference for $name." >&2
        exit 1
    fi
}

compare_schedule \
    simultaneous-backfill \
    "$REPO_ROOT/tests/test_traces/scheduler_correctness/32_simultaneous_backfill_with_reservation.csv" \
    "$REPO_ROOT/tests/test_traces/scheduler_correctness/32_simultaneous_backfill_with_reservation.expected_output.csv" \
    100

# Exercise sustained arrivals, queue compaction, and thousands of scheduling
# decisions against the checked-in golden schedule.
compare_schedule \
    scale-2000 \
    "$REPO_ROOT/tests/test_traces/scale/huge_2000jobs.csv" \
    "$REPO_ROOT/tests/test_traces/scale/huge_2000jobs.expected_output.csv" \
    795

echo "Custom scheduler reference-output tests passed"
