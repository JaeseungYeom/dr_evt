#!/bin/bash
# Feature Tests - Simulation Mode with Reference Comparison
#
# Tests specific features/policies:
# - Conservative vs EASY backfilling
# - Different scheduling policies
#
# Each test compares simulator output against expected reference files.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"

# Resolve an explicitly configured or installed simulator before falling back
# to the repository build tree.
source "$SCRIPT_DIR/set_simulator_path.sh"

echo "=========================================="
echo "Feature Tests (Simulation Mode)"
echo "=========================================="
echo ""

PASS=0
FAIL=0
MISSING=0
TEST_WORK_DIR=$(mktemp -d "/tmp/dr-evt-feature.XXXXXXXX")
trap 'rm -rf -- "$TEST_WORK_DIR"' EXIT INT TERM

TRACE_DIR="tests/test_traces/feature"

for expected_file in "$TRACE_DIR"/*.expected_output.csv; do
    test_name=$(basename "$expected_file" .expected_output.csv)
    test_file="$TRACE_DIR/${test_name}.csv"
    echo "Testing: $test_name"

    EXPECTED="$expected_file"

    if [ ! -f "$test_file" ]; then
        echo "  ✗ FAIL - Input trace not found"
        MISSING=$((MISSING + 1))
        continue
    fi

    # Optional companion file: extra CLI args for this specific test only
    # (e.g. --msec_output). Absent for every existing feature test, so
    # this is purely additive - no effect on tests that don't have one.
    EXTRA_ARGS=()
    FLAGS_FILE="$TRACE_DIR/${test_name}.flags"
    if [ -f "$FLAGS_FILE" ]; then
        # Word-split intentionally: the .flags file holds space-separated
        # CLI arguments, not a single opaque string.
        read -r -a EXTRA_ARGS < "$FLAGS_FILE"
    fi

    # Run simulator
    SIM_OUT="$TEST_WORK_DIR/${test_name}.csv"
    SIM_RESOURCES="$TEST_WORK_DIR/${test_name}_resources.csv"
    SIM_LOG="$TEST_WORK_DIR/${test_name}.log"

    if ! "$SIMULATOR" "$test_file" \
        --total_nodes 100 \
        --trace_format simple \
        --timestamp_format epoch \
        --run_time_mode limit \
        --backfill_policy easy \
        --priority_policy fcfs \
        "${EXTRA_ARGS[@]}" \
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

    # Convert simulator output to comparable format
    OUTPUT="$TEST_WORK_DIR/${test_name}_comparable.csv"
    awk -F, 'NR==1 {print "job_id,start_time,end_time"; next} {print NR-2","$2","$3}' "$SIM_OUT" > "$OUTPUT"

    # Compare job schedules
    if diff -w "$EXPECTED" "$OUTPUT" > /dev/null 2>&1; then
        JOB_MATCH=0
    else
        JOB_MATCH=1
    fi

    # Compare resource traces
    EXPECTED_RESOURCES="$TRACE_DIR/${test_name}.expected_resources.csv"
    RESOURCE_MATCH=0

    if [ -f "$EXPECTED_RESOURCES" ]; then
        RESOURCES_COMPARABLE="$TEST_WORK_DIR/${test_name}_resources_comparable.csv"
        awk -F, 'NR==1 {print "time,nodes_used,nodes_free"; next}
                 NR==2 && $1=="0" && $3=="0" {next}
                 {print $1","$3","$2}' "$SIM_RESOURCES" > "$RESOURCES_COMPARABLE"

        if ! diff -w "$EXPECTED_RESOURCES" "$RESOURCES_COMPARABLE" > /dev/null 2>&1; then
            RESOURCE_MATCH=1
        fi
    fi

    # Report results
    if [ $JOB_MATCH -eq 0 ] && [ $RESOURCE_MATCH -eq 0 ]; then
        echo "  ✓ PASS"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL"
        [ $JOB_MATCH -ne 0 ] && echo "     Job schedule mismatch"
        [ $RESOURCE_MATCH -ne 0 ] && echo "     Resource trace mismatch"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed, $MISSING missing fixtures"
echo "=========================================="

if [ $FAIL -eq 0 ] && [ $MISSING -eq 0 ]; then
    echo "✓ ALL FEATURE TESTS PASSED"
    exit 0
else
    echo "✗ SOME FEATURE TESTS FAILED"
    exit 1
fi
