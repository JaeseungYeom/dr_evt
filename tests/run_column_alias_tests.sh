#!/bin/bash
# Column Alias Tests
#
# Tests that time_limit and actual_run_time each accept multiple column-name
# aliases in "simple"-format traces, so an existing trace file can be
# reused without editing its header (slow to do by hand on a large file).
# Added in src/trace/data_columns.cpp's find_column()/find_column_optional().
#
# time_limit accepts: time_limit, timelimit, walltime
# actual_run_time accepts: actual_run_time, duration, actual_duration, run_time
#
# No other test suite exercises any alias other than the canonical name.
#
# Tests verify that column name aliases work correctly with run_time_mode.
# Uses --run_time_mode limit for traces without actual_run_time column,
# and --run_time_mode actual for traces with that column.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"

# Source common simulator path finder
source "$SCRIPT_DIR/set_simulator_path.sh"

echo "=========================================="
echo "Column Alias Tests"
echo "=========================================="
echo ""


PASS=0
FAIL=0
TEST_WORK_DIR=$(mktemp -d "/tmp/dr-evt-column-aliases.XXXXXXXX")
trap 'rm -rf -- "$TEST_WORK_DIR"' EXIT INT TERM

# ------------------------------------------------------------------------
# time_limit aliases
# ------------------------------------------------------------------------
echo "--- time_limit column aliases ---"

for alias in time_limit timelimit walltime; do
    INPUT="$TEST_WORK_DIR/tl_${alias}.csv"
    OUTPUT="$TEST_WORK_DIR/tl_${alias}_out.csv"
    LOG="$TEST_WORK_DIR/tl_${alias}.log"
    cat > "$INPUT" << EOF
job_submit_time,num_nodes,${alias}
0,70,200
EOF

    if "$SIMULATOR" "$INPUT" \
        --priority_policy fcfs \
        --total_nodes 100 \
        --trace_format simple \
        --timestamp_format epoch \
        --run_time_mode limit \
        --backfill_policy easy \
        --outfile "$OUTPUT" \
        > "$LOG" 2>&1; then

        # run_time_mode=limit should use the column's value (200) as the
        # job's execution time, regardless of which alias named it.
        LINE=$(awk -F, '$1 == 0' "$OUTPUT")
        BEGIN=$(echo "$LINE" | cut -d, -f2)
        END=$(echo "$LINE" | cut -d, -f3)
        EXEC_TIME=$((END - BEGIN))
        if [ "$EXEC_TIME" -eq 200 ]; then
            echo "✓ PASS: '$alias' recognized as time_limit (execution time 200s)"
            PASS=$((PASS + 1))
        else
            echo "✗ FAIL: '$alias' recognized but wrong execution time: ${EXEC_TIME}s (expected 200s)"
            FAIL=$((FAIL + 1))
        fi
    else
        echo "✗ FAIL: '$alias' not recognized as time_limit"
        cat "$LOG"
        FAIL=$((FAIL + 1))
    fi
done

echo ""

# ------------------------------------------------------------------------
# actual_run_time aliases
# ------------------------------------------------------------------------
echo "--- actual_run_time column aliases (run_time_mode=actual) ---"

for alias in actual_run_time duration actual_duration run_time; do
    INPUT="$TEST_WORK_DIR/ar_${alias}.csv"
    OUTPUT="$TEST_WORK_DIR/ar_${alias}_out.csv"
    LOG="$TEST_WORK_DIR/ar_${alias}.log"
    cat > "$INPUT" << EOF
job_submit_time,num_nodes,time_limit,${alias}
0,70,200,50
EOF

    if "$SIMULATOR" "$INPUT" \
        --priority_policy fcfs \
        --total_nodes 100 \
        --trace_format simple \
        --timestamp_format epoch \
        --run_time_mode actual \
        --backfill_policy easy \
        --outfile "$OUTPUT" \
        > "$LOG" 2>&1; then

        # run_time_mode=actual should read the real run time (50), not
        # time_limit (200), from whichever alias named the column.
        LINE=$(awk -F, '$1 == 0' "$OUTPUT")
        BEGIN=$(echo "$LINE" | cut -d, -f2)
        END=$(echo "$LINE" | cut -d, -f3)
        EXEC_TIME=$((END - BEGIN))
        if [ "$EXEC_TIME" -eq 50 ]; then
            echo "✓ PASS: '$alias' recognized as actual_run_time (execution time 50s, not time_limit's 200s)"
            PASS=$((PASS + 1))
        else
            echo "✗ FAIL: '$alias' recognized but wrong execution time: ${EXEC_TIME}s (expected 50s)"
            FAIL=$((FAIL + 1))
        fi
    else
        echo "✗ FAIL: '$alias' not recognized as actual_run_time"
        cat "$LOG"
        FAIL=$((FAIL + 1))
    fi
done

echo ""

# ------------------------------------------------------------------------
# Missing column: clear error, not a silent default
# ------------------------------------------------------------------------
echo "--- Missing time_limit column (should reject clearly) ---"

MISSING_INPUT="$TEST_WORK_DIR/no_time_limit.csv"
cat > "$MISSING_INPUT" << 'EOF'
job_submit_time,num_nodes
0,70
EOF

if ERR_MSG=$("$SIMULATOR" "$MISSING_INPUT" \
    --priority_policy fcfs \
    --total_nodes 100 \
    --trace_format simple \
    --timestamp_format epoch \
    --run_time_mode limit \
    --backfill_policy easy \
    --outfile "$TEST_WORK_DIR/no_time_limit_out.csv" 2>&1); then
    echo "✗ FAIL: missing time_limit was accepted"
    FAIL=$((FAIL + 1))
else
    if echo "$ERR_MSG" | grep -q "time_limit.*not found\|not found.*time_limit"; then
        echo "✓ PASS: missing time_limit (and all aliases) rejected with a clear error"
        PASS=$((PASS + 1))
    else
        echo "✗ FAIL: expected a clear 'column not found' error, got:"
        echo "$ERR_MSG"
        FAIL=$((FAIL + 1))
    fi
fi

echo ""

echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ "$FAIL" -eq 0 ]; then
    echo "✓ ALL COLUMN ALIAS TESTS PASSED"
    exit 0
else
    echo "✗ SOME TESTS FAILED"
    exit 1
fi
