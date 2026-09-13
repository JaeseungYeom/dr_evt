#!/bin/bash
# Replay Mode Tests
#
# Verifies that the tracer (standalone replay binary - no scheduler
# involved) faithfully reproduces the resource usage a simulator run
# already produced:
# 1. Run simulation mode (simulator) → job_trace + resource_trace_sim
# 2. Replay job_trace (tracer, using its begin_time/end_time directly)
#    → resource_trace_replay
# 3. Compare: resource_trace_sim == resource_trace_replay

set -e

USE_VALGRIND=0
if [ "${1:-}" = "--valgrind" ]; then
    USE_VALGRIND=1
    shift
fi
if [ "$#" -ne 0 ]; then
    echo "Usage: $0 [--valgrind]" >&2
    exit 2
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"

# Source common simulator and tracer path finders
source "$SCRIPT_DIR/set_simulator_path.sh"
source "$SCRIPT_DIR/set_tracer_path.sh"

echo "=========================================="
echo "Replay Mode Tests"
echo "=========================================="
echo ""
echo "Testing that replay mode reproduces simulation resource usage"
echo ""


PASS=0
FAIL=0
TEST_WORK_DIR=$(mktemp -d "/tmp/dr-evt-replay.XXXXXXXX")
trap 'rm -rf -- "$TEST_WORK_DIR"' EXIT INT TERM

RUN_PREFIX=()
if [ "$USE_VALGRIND" -eq 1 ]; then
    if ! command -v valgrind >/dev/null 2>&1; then
        echo "Error: --valgrind requested, but valgrind is unavailable" >&2
        exit 2
    fi
    RUN_PREFIX=(valgrind --quiet --leak-check=full --show-leak-kinds=definite,possible
        --errors-for-leak-kinds=definite,possible --track-origins=yes
        --error-exitcode=99)
    # Some LC allocations expose an unwritable per-user /var/tmp. Valgrind
    # needs a writable TMPDIR for its client command-line handoff files.
    export TMPDIR="$TEST_WORK_DIR"
    echo "Valgrind memory checking is enabled for every replay test process"
    echo ""
fi

# Exercise the internal replay reclamation boundaries before the CLI-level
# simulation/replay comparisons below.
RECLAMATION_BIN="${RECLAMATION_BIN:-${CMAKE_INSTALL_PREFIX:-./install}/bin/tests/test_replay_reclamation}"

echo "Testing: replay job-store reclamation boundaries"
if [ ! -x "$RECLAMATION_BIN" ]; then
    echo "  ✗ FAIL - installed test_replay_reclamation binary not found"
    echo "    Expected: $RECLAMATION_BIN"
    FAIL=$((FAIL + 1))
elif "${RUN_PREFIX[@]}" "$RECLAMATION_BIN"; then
    echo "  ✓ PASS - replay reclamation boundaries"
    PASS=$((PASS + 1))
else
    echo "  ✗ FAIL - replay reclamation boundaries"
    FAIL=$((FAIL + 1))
fi

# Use several scheduler-correctness fixtures as replay test inputs
REPLAY_TESTS=(
    "01_backfill_allowed"
    "05_multiple_backfills"
    "13_consecutive_fcfs"
    "21_sustained_high_load"
)

for test_base in "${REPLAY_TESTS[@]}"; do
    echo "Testing: $test_base"

    input_trace="tests/test_traces/scheduler_correctness/${test_base}.csv"

    if [ ! -f "$input_trace" ]; then
        echo "  ✗ Input not found: $input_trace"
        FAIL=$((FAIL + 1))
        continue
    fi

    # Step 1: Run simulation mode
    case_dir="$TEST_WORK_DIR/$test_base"
    mkdir -p "$case_dir"
    sim_job_output="$case_dir/simulation-jobs.csv"
    sim_resource_output="$case_dir/simulation-resources.csv"
    sim_log="$case_dir/simulation.log"

    # Use run_time_mode=limit (jobs run for full time_limit)
    if ! "${RUN_PREFIX[@]}" "$SIMULATOR" "$input_trace" \
        --total_nodes 100 \
        --trace_format simple \
        --timestamp_format epoch \
        --run_time_mode limit \
        --outfile "$sim_job_output" \
        --resource_trace "$sim_resource_output" \
        > "$sim_log" 2>&1; then
        echo "  ✗ Simulation process failed"
        sed 's/^/       /' "$sim_log"
        FAIL=$((FAIL + 1))
        continue
    fi

    if [ ! -f "$sim_job_output" ] || [ ! -f "$sim_resource_output" ]; then
        echo "  ✗ Simulation failed"
        FAIL=$((FAIL + 1))
        continue
    fi

    # Step 2: Replay the job trace - tracer only, no scheduler involved
    default_output_dir="$case_dir/default-replay"
    mkdir -p "$default_output_dir"
    replay_resource_output="$default_output_dir/replay-resources.csv"

    replay_log="$case_dir/replay.log"
    if ! (cd "$default_output_dir" && \
        "${RUN_PREFIX[@]}" "$TRACER" --infile "$sim_job_output" \
            --total_nodes 100 \
            --resource_trace "$replay_resource_output") \
            > "$replay_log" 2>&1; then
        echo "  ✗ Replay process failed"
        sed 's/^/       /' "$replay_log"
        FAIL=$((FAIL + 1))
        continue
    fi

    if [ ! -f "$replay_resource_output" ]; then
        echo "  ✗ Replay failed"
        FAIL=$((FAIL + 1))
        continue
    fi

    default_file_set=$(find "$default_output_dir" -maxdepth 1 -type f \
        -printf '%f\n' | LC_ALL=C sort)
    if [ "$default_file_set" != "replay-resources.csv" ]; then
        echo "  ✗ Unexpected default output-file set"
        echo "$default_file_set" | sed 's/^/       /'
        FAIL=$((FAIL + 1))
        continue
    fi

    # One fixture also verifies the inverse: every optional report is
    # produced when explicitly requested. This is an integration behavior,
    # not merely a command-line parsing default.
    if [ "$test_base" = "01_backfill_allowed" ]; then
        explicit_output_dir="$case_dir/explicit-replay"
        mkdir -p "$explicit_output_dir"
        explicit_job_output="$explicit_output_dir/jobs.csv"
        explicit_resource_output="$explicit_output_dir/resources.csv"
        explicit_sub_output="$explicit_output_dir/submissions.csv"
        explicit_summary_output="$explicit_output_dir/submission-summary.csv"
        explicit_dat_output="$explicit_output_dir/dat.txt"
        expected_output_dir="$REPO_ROOT/tests/test_traces/replay"

        explicit_log="$case_dir/explicit-replay.log"
        if ! (cd "$explicit_output_dir" && \
            "${RUN_PREFIX[@]}" "$TRACER" --infile "$sim_job_output" \
                --total_nodes 100 \
                --datfile "$explicit_dat_output" \
                --outfile "$explicit_job_output" \
                --resource_trace "$explicit_resource_output" \
                --subfile "$explicit_sub_output" \
                --subsumf "$explicit_summary_output") \
                > "$explicit_log" 2>&1; then
            echo "  ✗ Explicit-report replay process failed"
            sed 's/^/       /' "$explicit_log"
            FAIL=$((FAIL + 1))
            continue
        fi

        explicit_file_set=$(find "$explicit_output_dir" -maxdepth 1 -type f \
            -printf '%f\n' | LC_ALL=C sort)
        expected_file_set=$(printf '%s\n' dat.txt jobs.csv resources.csv \
            submission-summary.csv submissions.csv | LC_ALL=C sort)
        if [ "$explicit_file_set" != "$expected_file_set" ]; then
            echo "  ✗ Unexpected explicitly enabled output-file set"
            echo "$explicit_file_set" | sed 's/^/       /'
            FAIL=$((FAIL + 1))
            continue
        fi

        reports_match=1
        diff -u "$expected_output_dir/optional_reports.expected_jobs.csv" \
            "$explicit_job_output" || reports_match=0
        diff -u "$expected_output_dir/optional_reports.expected_resources.csv" \
            "$explicit_resource_output" || reports_match=0
        diff -u "$expected_output_dir/optional_reports.expected_submissions.csv" \
            "$explicit_sub_output" || reports_match=0
        diff -u \
            "$expected_output_dir/optional_reports.expected_submission_summary.csv" \
            "$explicit_summary_output" || reports_match=0
        diff -u "$expected_output_dir/optional_reports.expected_dat.txt" \
            "$explicit_dat_output" || reports_match=0
        if [ "$reports_match" -ne 1 ]; then
            echo "  ✗ Optional replay report content differs from expected"
            FAIL=$((FAIL + 1))
            continue
        fi
    fi

    # Step 3: Compare resource traces
    if diff -q "$sim_resource_output" "$replay_resource_output" > /dev/null; then
        echo "  ✓ PASS - Resource traces match"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL - Resource traces differ"
        echo "    Simulation:  $sim_resource_output"
        echo "    Replay:      $replay_resource_output"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ $FAIL -eq 0 ]; then
    echo "✓ ALL REPLAY TESTS PASSED"
    echo ""
    echo "Replay mode correctly reproduces simulation resource usage."
    exit 0
else
    echo "✗ SOME REPLAY TESTS FAILED"
    echo ""
    echo "Replay mode does not match simulation resource usage."
    exit 1
fi
