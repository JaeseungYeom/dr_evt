#!/bin/bash
# Python API Test Runner
#
# Tests Python bindings for DR_EVT streaming API.
# Requires building with -DDR_EVT_BUILD_PYTHON=ON

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."
source "$SCRIPT_DIR/test_reporting.sh"
test_report_enable

cd "$REPO_ROOT"
source "$SCRIPT_DIR/select_python.sh"

echo "=========================================="
echo "Python API Tests"
echo "=========================================="
echo ""

# Find installed Python bindings. A CPython extension is tied to the major and
# minor interpreter version encoded in its filename (for example, cpython-313).
INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-./install}"
mapfile -t PYTHON_MODULE_DIRS < <(python_install_module_dirs "$INSTALL_PREFIX")
mapfile -t PYTHON_MODULES < <(
    find "${PYTHON_MODULE_DIRS[@]}" \
        -type f -name "dr_evt*.so" -print 2>/dev/null | LC_ALL=C sort
)

if [ "${#PYTHON_MODULES[@]}" -eq 0 ]; then
    echo "✗ Error: Python module not found"
    echo ""
    echo "Build Python bindings with:"
    echo "  cd build && cmake .. -DDR_EVT_BUILD_PYTHON=ON && make && make install"
    exit 1
fi

python_tag() {
    "$1" -c 'import sys; print(f"cpython-{sys.version_info.major}{sys.version_info.minor}")'
}

PYTHON_BIN=""
PYTHON_TAG=""
PYTHON_MODULE=""
while IFS=$'\t' read -r _ candidate_python; do
    if ! "$candidate_python" -c \
        'import sys; raise SystemExit(sys.version_info < (3, 7))' \
        >/dev/null 2>&1; then
        continue
    fi
    candidate_tag=$(python_tag "$candidate_python")
    for candidate_module in "${PYTHON_MODULES[@]}"; do
        if [[ "$(basename "$candidate_module")" == *".${candidate_tag}-"* ]]; then
            PYTHON_BIN="$candidate_python"
            PYTHON_TAG="$candidate_tag"
            PYTHON_MODULE="$candidate_module"
            break 2
        fi
    done
done < <(python_interpreter_candidates)

if [ -z "$PYTHON_MODULE" ]; then
    echo "✗ Error: no Python 3.7+ interpreter matches an installed dr_evt module"
    echo "Installed modules:"
    printf '  %s\n' "${PYTHON_MODULES[@]}"
    echo "Set PYTHON_EXECUTABLE to the Python used when configuring CMake."
    exit 1
fi

echo "Found Python module: $PYTHON_MODULE"
echo ""

# Check Python version
PYTHON_VERSION=$("$PYTHON_BIN" --version 2>&1)
echo "Python version: $PYTHON_VERSION"
echo ""

# Run Python API tests
echo "Running Python API test suite..."
echo ""

# Set PYTHONPATH to include the module directory
MODULE_DIR=$(dirname "$PYTHON_MODULE")
export PYTHONPATH="$MODULE_DIR${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON_BIN" tests/test_python_api.py

EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ PYTHON API TESTS PASSED"
else
    echo "❌ PYTHON API TESTS FAILED"
fi
echo "=========================================="

exit $EXIT_CODE
