#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 INPUT.csv OUTPUT.csv" >&2
    exit 2
fi

input=$1
output=$2
if [[ "$input" == "$output" ]]; then
    echo "input and output must be different files" >&2
    exit 2
fi

LC_ALL=C awk -F, '
BEGIN { OFS="," }
NR == 1 {
    for (i = 1; i <= NF; ++i) column[$i] = i
    required[1] = "submit_time"
    required[2] = "num_nodes"
    required[3] = "begin_time"
    required[4] = "end_time"
    required[5] = "time_limit"
    for (i = 1; i <= 5; ++i) {
        if (!(required[i] in column)) {
            print "missing column: " required[i] > "/dev/stderr"
            exit 2
        }
    }
    print "submit_time", "num_nodes", "time_limit", "duration"
    next
}
{
    duration = $(column["end_time"]) - $(column["begin_time"])
    limit = $(column["time_limit"]) + 0
    if (duration <= 0 || limit <= 0) {
        print "invalid duration or time limit at line " NR > "/dev/stderr"
        exit 2
    }
    output_limit = $(column["time_limit"])
    if (duration > limit) {
        output_limit = int(duration)
        if (output_limit < duration)
            ++output_limit
        ++adjusted
    }
    printf "%s,%s,%s,%.6f\n", \
        $(column["submit_time"]), $(column["num_nodes"]), \
        output_limit, duration
    ++written
}
END {
    if (written > 0)
        print "wrote " written " jobs; extended limits=" adjusted > "/dev/stderr"
}
' "$input" > "$output"
