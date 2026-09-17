#!/bin/bash
# End-to-end checks for the simulator's inclusive --max_time boundary.

set -u

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <simulator>" >&2
    exit 2
fi

SIMULATOR="$1"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dr-evt-max-time.XXXXXXXX")"
trap 'rm -rf -- "$WORK_DIR"' EXIT INT TERM

fail() {
    echo "max_time test failed: $1" >&2
    exit 1
}

expect_line() {
    grep -Fqx -- "$1" "$2" || fail "missing '$1' in $2"
}

# Ordinary simulation: the t=5 completion and arrival are both processed.
# The second job remains running, and the t=20 arrival remains pending.
cat >"$WORK_DIR/simulation.csv" <<'EOF'
job_submit_time,num_nodes,time_limit,actual_run_time
0,2,5,5
5,2,10,10
20,1,1,1
EOF
"$SIMULATOR" "$WORK_DIR/simulation.csv" --total_nodes 2 \
    --run_time_mode actual --max_time 5 \
    --outfile "$WORK_DIR/simulation.out.csv" \
    --resource_trace "$WORK_DIR/simulation.resources.csv" \
    >"$WORK_DIR/simulation.log" 2>&1 || fail "ordinary simulation exited nonzero"
expect_line "Current time: 5" "$WORK_DIR/simulation.log"
expect_line "Jobs completed: 1" "$WORK_DIR/simulation.log"
[ "$(wc -l <"$WORK_DIR/simulation.out.csv")" -eq 2 ] || \
    fail "ordinary output should contain exactly one completed job"
expect_line "0,0,5,2,0,5" "$WORK_DIR/simulation.out.csv"
[ "$(tail -n 1 "$WORK_DIR/simulation.resources.csv")" = "5,0,2" ] || \
    fail "ordinary simulation did not settle the inclusive t=5 events"

# Full replay uses recorded begin/end times but observes the same boundary.
cat >"$WORK_DIR/replay.csv" <<'EOF'
job_submit_time,begin_time,end_time,num_nodes,exit_status,time_limit
0,0,5,2,0,5
4,5,10,2,0,5
11,11,12,1,0,1
EOF
"$SIMULATOR" "$WORK_DIR/replay.csv" --total_nodes 4 --max_time 5 \
    --outfile "$WORK_DIR/replay.out.csv" \
    --resource_trace "$WORK_DIR/replay.resources.csv" \
    >"$WORK_DIR/replay.log" 2>&1 || fail "replay exited nonzero"
expect_line "Current time: 5" "$WORK_DIR/replay.log"
expect_line "Jobs completed: 1" "$WORK_DIR/replay.log"
[ "$(wc -l <"$WORK_DIR/replay.out.csv")" -eq 2 ] || \
    fail "replay output should contain exactly one completed job"
[ "$(tail -n 1 "$WORK_DIR/replay.resources.csv")" = "5,2,2" ] || \
    fail "replay did not settle the inclusive t=5 events"

# A limit reached during warmup must stop there without entering an unbounded
# ordinary drain. Both the historical and newly scheduled job are still live.
cat >"$WORK_DIR/warm.csv" <<'EOF'
job_submit_time,begin_time,end_time,num_nodes,exit_status,time_limit
0,2,15,2,0,13
10,20,24,2,0,4
EOF
"$SIMULATOR" "$WORK_DIR/warm.csv" --total_nodes 4 \
    --sim_start_time 10 --max_time 12 --run_time_mode actual \
    --outfile "$WORK_DIR/warm.out.csv" \
    --resource_trace "$WORK_DIR/warm.resources.csv" \
    >"$WORK_DIR/warm.log" 2>&1 || fail "warm start exited nonzero"
expect_line "Current time: 12" "$WORK_DIR/warm.log"
expect_line "Jobs completed: 0" "$WORK_DIR/warm.log"
[ "$(wc -l <"$WORK_DIR/warm.out.csv")" -eq 1 ] || \
    fail "warm-start output should contain no completed jobs"
[ "$(tail -n 1 "$WORK_DIR/warm.resources.csv")" = "10,0,4" ] || \
    fail "warm-start live occupancy is incorrect at the boundary"

echo "max_time CLI tests passed"
