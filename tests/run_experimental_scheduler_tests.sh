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

TEST_BIN="${EXPERIMENTAL_SCHEDULER_TEST:-$INSTALL_PREFIX/bin/tests/test_experimental_scheduler}"
if [[ "$TEST_BIN" != /* ]]; then
    TEST_BIN="$REPO_ROOT/${TEST_BIN#./}"
fi
if [[ ! -x "$TEST_BIN" ]]; then
    echo "Experimental scheduler test executable not found: $TEST_BIN" >&2
    echo "Build/install test_experimental_scheduler-bin, or set EXPERIMENTAL_SCHEDULER_TEST." >&2
    exit 1
fi

TRACE="$REPO_ROOT/tests/test_traces/scheduler_correctness/32_simultaneous_backfill_with_reservation.csv"
EXPECTED="$REPO_ROOT/tests/test_traces/scheduler_correctness/32_simultaneous_backfill_with_reservation.expected_output.csv"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-experimental-scheduler.XXXXXXXX")"
trap 'rm -rf -- "$RUN_DIR"' EXIT INT TERM

echo "Running experimental scheduler unit checks"
"$TEST_BIN"

RAW_OUTPUT="$RUN_DIR/experimental.csv"
ACTUAL="$RUN_DIR/experimental.comparable.csv"
"$TEST_BIN" --write-schedule "$TRACE" "$RAW_OUTPUT"

awk -F, 'NR == 1 { print "job_id,start_time,end_time"; next }
         { print NR-2 "," $2 "," $3 }' "$RAW_OUTPUT" > "$ACTUAL"

echo "Comparing experimental schedule with reference output"
if ! diff -u "$EXPECTED" "$ACTUAL"; then
    echo "Experimental scheduler output does not match EASY reference." >&2
    exit 1
fi

echo "Experimental scheduler reference-output test passed"
