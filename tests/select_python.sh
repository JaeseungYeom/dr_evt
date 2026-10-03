#!/usr/bin/env bash
# Shared Python interpreter and installed-module discovery helpers.
# Source this file; do not execute it directly.

# Print available Python interpreters as "version-key<TAB>absolute-path",
# newest first. PYTHON_EXECUTABLE, when set, is the only candidate considered.
python_interpreter_candidates() {
    local names candidate candidate_path version_key
    local -a names

    if [[ -n "${PYTHON_EXECUTABLE:-}" ]]; then
        names=("$PYTHON_EXECUTABLE")
    else
        names=(python python3 python3.15 python3.14 python3.13 python3.12
               python3.11 python3.10 python3.9 python3.8 python3.7 python3.6)
    fi

    for candidate in "${names[@]}"; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        candidate_path=$(command -v "$candidate")
        if ! version_key=$("$candidate_path" -c \
            'import sys; print(sys.version_info[0] * 1000000 + sys.version_info[1] * 1000 + sys.version_info[2])' \
            2>/dev/null); then
            continue
        fi
        printf '%09d\t%s\n' "$version_key" "$candidate_path"
    done | LC_ALL=C sort -t $'\t' -k1,1nr -k2,2 -u
}

# Select the newest available interpreter satisfying a minimum major/minor.
# Sets and exports PYTHON_BIN. An explicit PYTHON_EXECUTABLE is never replaced.
select_python_interpreter() {
    local minimum_major="$1"
    local minimum_minor="$2"
    local version_key candidate_path meets_minimum

    PYTHON_BIN=""
    while IFS=$'\t' read -r version_key candidate_path; do
        if meets_minimum=$("$candidate_path" -c \
            "import sys; print(int(sys.version_info >= (${minimum_major}, ${minimum_minor})))" \
            2>/dev/null) && [[ "$meets_minimum" == 1 ]]; then
            PYTHON_BIN="$candidate_path"
            export PYTHON_BIN
            return 0
        fi
    done < <(python_interpreter_candidates)
    return 1
}

# Print possible installed Python-module directories without assuming lib or
# lib64. A configured CMAKE_INSTALL_LIBDIR is preferred when available.
python_install_module_dirs() {
    local install_prefix="$1"
    local libdir path
    local -A seen=()
    local -a libdirs=()

    if [[ -n "${CMAKE_INSTALL_LIBDIR:-}" ]]; then
        libdirs+=("$CMAKE_INSTALL_LIBDIR")
    fi
    libdirs+=(lib lib64)

    for libdir in "${libdirs[@]}"; do
        if [[ "$libdir" = /* ]]; then
            path="$libdir/python"
        else
            path="$install_prefix/$libdir/python"
        fi
        if [[ -z "${seen[$path]:-}" ]]; then
            seen[$path]=1
            printf '%s\n' "$path"
        fi
    done
}
