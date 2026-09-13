#!/bin/bash
# Python API Test Runner
#
# Tests Python bindings for DR_EVT streaming API.
# Requires building with -DDR_EVT_BUILD_PYTHON=ON

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$SCRIPT_DIR/.."

cd "$REPO_ROOT"

echo "=========================================="
echo "Python API Tests"
echo "=========================================="
echo ""

# Find installed Python bindings. A CPython extension is tied to the major and
# minor interpreter version encoded in its filename (for example, cpython-313).
INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-./install}"
mapfile -t PYTHON_MODULES < <(
    find "$INSTALL_PREFIX/lib/python" "$INSTALL_PREFIX/lib64/python" \
        -type f -name "dr_evt*.so" -print 2>/dev/null | LC_ALL=C sort
)

if [ "${#PYTHON_MODULES[@]}" -eq 0 ]; then
    echo "✗ Error: Python module not found"
    echo ""
    echo "Build Python bindings with:"
    echo "  cd build && cmake .. -DDR_EVT_BUILD_PYTHON=ON && make && make install"
    exit 1
fi

PYTHON_BIN="${PYTHON_EXECUTABLE:-python3}"
EXPLICIT_PYTHON=0
if [ -n "${PYTHON_EXECUTABLE:-}" ]; then
    EXPLICIT_PYTHON=1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "✗ Error: Python interpreter not found: $PYTHON_BIN"
    exit 1
fi

python_tag() {
    "$1" -c 'import sys; print(f"cpython-{sys.version_info.major}{sys.version_info.minor}")'
}

PYTHON_TAG=$(python_tag "$PYTHON_BIN")
PYTHON_MODULE=""
for candidate_module in "${PYTHON_MODULES[@]}"; do
    if [[ "$(basename "$candidate_module")" == *".${PYTHON_TAG}-"* ]]; then
        PYTHON_MODULE="$candidate_module"
        break
    fi
done

# If the default python3 does not match, select the versioned interpreter
# encoded in one of the installed extension names. An explicitly requested
# PYTHON_EXECUTABLE is never replaced silently.
if [ -z "$PYTHON_MODULE" ] && [ "$EXPLICIT_PYTHON" -eq 0 ]; then
    for candidate_module in "${PYTHON_MODULES[@]}"; do
        module_name=$(basename "$candidate_module")
        if [[ "$module_name" =~ \.cpython-([0-9]+)- ]]; then
            digits="${BASH_REMATCH[1]}"
            required_tag="cpython-${digits}"
            for candidate_python in \
                "python${digits:0:1}.${digits:1}" python python3; do
                if command -v "$candidate_python" >/dev/null 2>&1; then
                    candidate_path=$(command -v "$candidate_python")
                    if [ "$(python_tag "$candidate_path")" = "$required_tag" ]; then
                        PYTHON_BIN="$candidate_path"
                        PYTHON_TAG="$required_tag"
                        PYTHON_MODULE="$candidate_module"
                        break 2
                    fi
                fi
            done
        fi
    done
fi

if [ -z "$PYTHON_MODULE" ]; then
    echo "✗ Error: no installed dr_evt module matches $PYTHON_BIN ($PYTHON_TAG)"
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
