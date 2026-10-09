#!/usr/bin/env bash
# Run the dr_evt_market test suite.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test_reporting.sh"
test_report_enable
INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-$REPO_ROOT/install}"

if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi
source "$SCRIPT_DIR/select_python.sh"

supports_market_package() {
    command -v "$1" >/dev/null 2>&1 &&
        "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'
}

find_dr_evt_module() {
    local python_bin="$1"
    local module_dir candidate

    MODULE_DIR=""
    EXPECTED_EXTENSION=""
    if "$python_bin" -c 'import dr_evt' >/dev/null 2>&1; then
        return 0
    fi

    EXPECTED_EXTENSION=$("$python_bin" -c \
        'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')
    while IFS= read -r module_dir; do
        candidate="$module_dir/dr_evt$EXPECTED_EXTENSION"
        if [[ -f "$candidate" ]]; then
            MODULE_DIR="$module_dir"
            return 0
        fi
    done < <(python_install_module_dirs "$INSTALL_PREFIX"; printf '%s\n' "$REPO_ROOT/build")

    return 1
}

report_missing_binding() {
    local python_bin="$1"
    local extension="$2"

    echo "Error: dr_evt cannot be imported by $python_bin" >&2
    echo "No matching dr_evt$extension was found under:" >&2
    python_install_module_dirs "$INSTALL_PREFIX" | sed 's/^/  /' >&2
    echo "  $REPO_ROOT/build" >&2
    echo "Build/install the Python bindings with the same interpreter, or set PYTHONPATH." >&2
}

if [[ -n "${PYTHON_EXECUTABLE:-}" ]]; then
    PYTHON_BIN="$PYTHON_EXECUTABLE"
    if ! supports_market_package "$PYTHON_BIN"; then
        echo "Error: dr_evt_market requires Python 3.10+: $PYTHON_BIN" >&2
        exit 1
    fi
    if ! find_dr_evt_module "$PYTHON_BIN"; then
        report_missing_binding "$PYTHON_BIN" "$EXPECTED_EXTENSION"
        exit 1
    fi
else
    PYTHON_BIN=""
    FOUND_COMPATIBLE_PYTHON=0
    while IFS=$'\t' read -r _ candidate_path; do
        if supports_market_package "$candidate_path"; then
            FOUND_COMPATIBLE_PYTHON=1
            if find_dr_evt_module "$candidate_path"; then
                PYTHON_BIN="$candidate_path"
                break
            fi
        fi
    done < <(python_interpreter_candidates)
    if [[ -z "$PYTHON_BIN" ]]; then
        if [[ "$FOUND_COMPATIBLE_PYTHON" -eq 0 ]]; then
            echo "Error: dr_evt_market requires Python 3.10+; set PYTHON_EXECUTABLE" >&2
        else
            echo "Error: no Python 3.10+ interpreter has a matching dr_evt binding." >&2
            echo "Searched the configured PYTHONPATH and:" >&2
            python_install_module_dirs "$INSTALL_PREFIX" | sed 's/^/  /' >&2
            echo "  $REPO_ROOT/build" >&2
            echo "Build/install the bindings with a compatible interpreter, or set PYTHON_EXECUTABLE." >&2
        fi
        exit 1
    fi
fi

MARKET_PATH="$REPO_ROOT/python"
if [[ -n "$MODULE_DIR" ]]; then
    MARKET_PATH="$MODULE_DIR:$MARKET_PATH"
fi
export PYTHONPATH="$MARKET_PATH${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO_ROOT"
echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
"$PYTHON_BIN" -m unittest discover -s python/dr_evt_market/tests -v
