#!/bin/bash
# Scale Tests - Simulation Mode
#
# Tests scheduler performance at scale:
# - 10 to 10,000 jobs
# - Compares against expected outputs
#
# These are required correctness checks at progressively larger job counts.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"

# Source common simulator path finder
source "$SCRIPT_DIR/set_simulator_path.sh"

echo "=========================================="
echo "Scale Tests (Simulation Mode)"
echo "=========================================="
echo ""


PASS=0
FAIL=0
MISSING=0
TEST_WORK_DIR=$(mktemp -d "/tmp/dr-evt-scale.XXXXXXXX")
trap 'rm -rf -- "$TEST_WORK_DIR"' EXIT INT TERM

TRACE_DIR="tests/test_traces/scale"

for test_file in "$TRACE_DIR"/*.csv; do
    # Skip expected files
    if [[ "$test_file" == *.expected* ]]; then
        continue
    fi

    test_name=$(basename "$test_file" .csv)
    echo "Testing: $test_name"

    EXPECTED="$TRACE_DIR/${test_name}.expected_output.csv"

    if [ ! -f "$EXPECTED" ]; then
        echo "  ✗ FAIL - No expected output"
        MISSING=$((MISSING + 1))
        continue
    fi

    SIM_OUT="$TEST_WORK_DIR/${test_name}.csv"
    SIM_RESOURCES="$TEST_WORK_DIR/${test_name}_resources.csv"
    SIM_LOG="$TEST_WORK_DIR/${test_name}.log"

    # scale/ jobs require 795 nodes (matches src/dr_evt_types.hpp's
    # default), NOT 100 - several jobs request up to 497 nodes and
    # cannot be scheduled at all under 100 nodes. Using --total_nodes
    # 100 here previously caused jobs to silently never start, and
    # made every scale test fail against expected_output.csv files
    # that were generated at 795 nodes.
    #
    # Scale test traces lack actual_run_time column, so use run_time_mode=limit
    if ! "$SIMULATOR" "$test_file" \
        --total_nodes 795 \
        --trace_format simple \
        --timestamp_format epoch \
        --run_time_mode limit \
        --backfill_policy easy \
        --priority_policy fcfs \
        --outfile "$SIM_OUT" \
        --resource_trace "$SIM_RESOURCES" > "$SIM_LOG" 2>&1; then
        echo "  ✗ FAIL - Simulator process failed"
        sed 's/^/     /' "$SIM_LOG"
        FAIL=$((FAIL + 1))
        continue
    fi

    if [ ! -f "$SIM_OUT" ]; then
        echo "  ✗ FAIL - Simulator did not produce output"
        FAIL=$((FAIL + 1))
        continue
    fi

    OUTPUT="$TEST_WORK_DIR/${test_name}_comparable.csv"
    awk -F, 'NR==1 {print "job_id,start_time,end_time"; next} {print NR-2","$2","$3}' "$SIM_OUT" > "$OUTPUT"

    EXPECTED_RESOURCES="$TRACE_DIR/${test_name}.expected_resources.csv"
    ACTUAL_RESOURCES_COMPARABLE="$TEST_WORK_DIR/${test_name}_resources_comparable.csv"
    EXPECTED_RESOURCES_COMPARABLE="$TEST_WORK_DIR/${test_name}_expected_resources_comparable.csv"
    if [ ! -f "$EXPECTED_RESOURCES" ]; then
        echo "  ✗ FAIL - No expected resource trace"
        MISSING=$((MISSING + 1))
        continue
    fi
    awk -F, 'NR==1 {print "time,nodes_used,nodes_free"; next}
             NR==2 && $1=="0" && $3=="0" {next}
             {print $1","$3","$2}' "$SIM_RESOURCES" > "$ACTUAL_RESOURCES_COMPARABLE"
    awk -F, '{print $1","$2","$3}' "$EXPECTED_RESOURCES" > "$EXPECTED_RESOURCES_COMPARABLE"

    # -w ignores presentation-only whitespace differences. Both the complete
    # schedule and resource transition history must match their fixtures.
    if diff -w "$EXPECTED" "$OUTPUT" > /dev/null 2>&1 && \
       diff -w "$EXPECTED_RESOURCES_COMPARABLE" "$ACTUAL_RESOURCES_COMPARABLE" > /dev/null 2>&1; then
        echo "  ✓ PASS"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL - Schedule or resource trace mismatch"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed, $MISSING missing fixtures"
echo "=========================================="

if [ $FAIL -eq 0 ] && [ $MISSING -eq 0 ]; then
    echo "✓ ALL SCALE TESTS PASSED"
    exit 0
else
    echo "✗ SOME SCALE TESTS FAILED"
    exit 1
fi
