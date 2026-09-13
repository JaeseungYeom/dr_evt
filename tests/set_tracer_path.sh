#!/bin/bash
# Common function to find the installed tracer - source this in test scripts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_PREFIX="${CMAKE_INSTALL_PREFIX:-$REPO_ROOT/install}"
if [[ "$INSTALL_PREFIX" != /* ]]; then
    INSTALL_PREFIX="$REPO_ROOT/${INSTALL_PREFIX#./}"
fi

if [ -n "${TRACER:-}" ] && [ -x "$TRACER" ]; then
    if [[ "$TRACER" != /* ]]; then
        TRACER="$REPO_ROOT/${TRACER#./}"
    fi
elif [ -x "$INSTALL_PREFIX/bin/tracer" ]; then
    TRACER="$INSTALL_PREFIX/bin/tracer"
else
    echo "Error: installed tracer not found or not executable: $INSTALL_PREFIX/bin/tracer"
    echo "Install the project first, or set CMAKE_INSTALL_PREFIX to its install prefix."
    exit 1
fi

export TRACER
