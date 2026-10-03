#!/usr/bin/env bash
# Shared final-status reporting for shell test runners. Source this file.

DR_EVT_TEST_REPORT_NAME=""
DR_EVT_TEST_REPORT_CLEANUP=""
DR_EVT_TEST_REPORT_STATE=""
DR_EVT_TEST_REPORT_REASON=""

test_report_enable() {
    DR_EVT_TEST_REPORT_NAME="${1:-$(basename "$0" .sh)}"
    DR_EVT_TEST_REPORT_CLEANUP="${2:-}"
    trap '_dr_evt_test_report_exit "$?"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

test_report_set_cleanup() {
    DR_EVT_TEST_REPORT_CLEANUP="${1:-}"
}

test_report_skip() {
    DR_EVT_TEST_REPORT_STATE="SKIP"
    DR_EVT_TEST_REPORT_REASON="${1:-not run}"
}

_dr_evt_test_report_exit() {
    local status="$1"
    local banner_left="<<<<<<<<<<<<<<<<"
    local banner_right=">>>>>>>>>>>>>>>>"
    trap - EXIT INT TERM

    if [[ -n "$DR_EVT_TEST_REPORT_CLEANUP" ]]; then
        "$DR_EVT_TEST_REPORT_CLEANUP" "$status" || true
    fi

    if [[ "$DR_EVT_TEST_REPORT_STATE" == "SKIP" && "$status" -eq 0 ]]; then
        printf '\n%s TEST RESULT: SKIP | %s | %s %s\n' \
            "$banner_left" "$DR_EVT_TEST_REPORT_NAME" \
            "$DR_EVT_TEST_REPORT_REASON" "$banner_right"
    elif [[ "$status" -eq 0 ]]; then
        printf '\n%s TEST RESULT: PASS | %s %s\n' \
            "$banner_left" "$DR_EVT_TEST_REPORT_NAME" "$banner_right"
    else
        printf '\n%s TEST RESULT: FAIL | %s | exit=%d %s\n' \
            "$banner_left" "$DR_EVT_TEST_REPORT_NAME" "$status" \
            "$banner_right" >&2
    fi

    exit "$status"
}
