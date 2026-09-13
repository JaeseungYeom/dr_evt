# Setup Ser20 for DR_EVT.
#
# Prefer an installed package. If none is available, build the checkout in
# external/ser20 or fetch the pinned upstream release with FetchContent.

option(AVOID_SYSTEM_SER20
  "Do not search default system paths for Ser20" FALSE)

# Promote environment variables to the CMake spellings consumed here unless
# the caller supplied an explicit -D value.
if (NOT DEFINED ser20_DIR AND DEFINED ENV{ser20_DIR})
  set(ser20_DIR "$ENV{ser20_DIR}")
endif ()
if (NOT DEFINED ser20_ROOT AND DEFINED ENV{ser20_ROOT})
  set(ser20_ROOT "$ENV{ser20_ROOT}")
endif ()
if (NOT DEFINED SER20_ROOT AND DEFINED ENV{SER20_ROOT})
  set(SER20_ROOT "$ENV{SER20_ROOT}")
endif ()

unset(DR_EVT_SER20_FETCHCONTENT CACHE)
unset(ser20_FOUND CACHE)
unset(ser20_FOUND)

# Ser20 generates its package config in the build tree, but its exported
# targets file is created only by installation. Do not let a stale cached
# ser20_DIR load that incomplete config on a later configure.
if (DEFINED ser20_DIR AND
    ser20_DIR STREQUAL "${CMAKE_BINARY_DIR}/_deps/ser20-build" AND
    NOT EXISTS "${ser20_DIR}/ser20Targets.cmake")
  unset(ser20_DIR CACHE)
  unset(ser20_DIR)
endif ()

set(DR_EVT_SER20_INSTALL_HINTS)
if (DEFINED ser20_ROOT)
  list(APPEND DR_EVT_SER20_INSTALL_HINTS "${ser20_ROOT}")
endif ()
if (DEFINED SER20_ROOT)
  list(APPEND DR_EVT_SER20_INSTALL_HINTS "${SER20_ROOT}")
endif ()
if (CMAKE_PREFIX_PATH)
  list(APPEND DR_EVT_SER20_INSTALL_HINTS ${CMAKE_PREFIX_PATH})
endif ()
if (DEFINED ENV{CMAKE_PREFIX_PATH} AND
    NOT "$ENV{CMAKE_PREFIX_PATH}" STREQUAL "")
  cmake_path(CONVERT "$ENV{CMAKE_PREFIX_PATH}" TO_CMAKE_PATH_LIST
    DR_EVT_SER20_ENV_PREFIX_PATH NORMALIZE)
  list(APPEND DR_EVT_SER20_INSTALL_HINTS
    ${DR_EVT_SER20_ENV_PREFIX_PATH})
endif ()
if (CMAKE_INSTALL_PREFIX)
  list(APPEND DR_EVT_SER20_INSTALL_HINTS "${CMAKE_INSTALL_PREFIX}")
endif ()
list(REMOVE_DUPLICATES DR_EVT_SER20_INSTALL_HINTS)

if (AVOID_SYSTEM_SER20 OR DEFINED ser20_ROOT OR DEFINED SER20_ROOT)
  set(DR_EVT_SER20_SEARCH_MODE NO_DEFAULT_PATH)
else ()
  set(DR_EVT_SER20_SEARCH_MODE "")
endif ()

# CMP0144 makes the conventional uppercase SER20_ROOT spelling participate in
# find_package while the explicit HINTS retain support on CMake 3.24-3.26.
if (POLICY CMP0144)
  cmake_policy(PUSH)
  cmake_policy(SET CMP0144 NEW)
endif ()
find_package(ser20 CONFIG QUIET
  HINTS ${DR_EVT_SER20_INSTALL_HINTS}
  ${DR_EVT_SER20_SEARCH_MODE})
if (POLICY CMP0144)
  cmake_policy(POP)
endif ()

unset(DR_EVT_SER20_ENV_PREFIX_PATH)
unset(DR_EVT_SER20_INSTALL_HINTS)
unset(DR_EVT_SER20_SEARCH_MODE)

if (TARGET ser20::ser20)
  set(DR_EVT_SER20_FETCHCONTENT OFF)
  message(STATUS "Found Ser20: ${ser20_VERSION} (ser20_DIR: ${ser20_DIR})")
else ()
  include(FetchContent)

  set(DR_EVT_SER20_SOURCE_DIR "${CMAKE_SOURCE_DIR}/external/ser20")
  if (EXISTS "${DR_EVT_SER20_SOURCE_DIR}/CMakeLists.txt")
    message(STATUS "Ser20 was not found as an installed package; building "
                   "the local source at ${DR_EVT_SER20_SOURCE_DIR} with "
                   "FetchContent.")
    FetchContent_Declare(ser20 SOURCE_DIR "${DR_EVT_SER20_SOURCE_DIR}")
  else ()
    if (FETCHCONTENT_SOURCE_DIR_SER20)
      set(DR_EVT_SER20_FETCHCONTENT_SOURCE_DIR
        "${FETCHCONTENT_SOURCE_DIR_SER20}")
    else ()
      set(DR_EVT_SER20_FETCHCONTENT_SOURCE_DIR
        "${FETCHCONTENT_BASE_DIR}/ser20-src")
    endif ()
    if (EXISTS "${DR_EVT_SER20_FETCHCONTENT_SOURCE_DIR}/CMakeLists.txt")
      message(STATUS "Reusing Ser20 source already fetched under "
                     "${DR_EVT_SER20_FETCHCONTENT_SOURCE_DIR} "
                     "(not re-downloading).")
    else ()
      message(STATUS "Ser20 was not found as an installed package or under "
                     "external/ser20; fetching its pinned source with "
                     "FetchContent.")
    endif ()
    unset(DR_EVT_SER20_FETCHCONTENT_SOURCE_DIR)

    FetchContent_Declare(ser20
      GIT_REPOSITORY https://github.com/royjacobson/ser20.git
      GIT_TAG v0.9.1
      GIT_SHALLOW TRUE)
    set(FETCHCONTENT_UPDATES_DISCONNECTED_SER20 ON)
  endif ()

  # Ser20 is part of DR_EVT's public link interface, so a fallback build must
  # install its library, headers, and package metadata with DR_EVT. Its tests,
  # examples, documentation, and warnings-as-errors setting are not inherited.
  function(dr_evt_make_ser20_available)
    set(SER20_INSTALL ON)
    set(BUILD_DOC OFF)
    set(BUILD_SANDBOX OFF)
    set(BUILD_TESTS OFF)
    set(SKIP_PERFORMANCE_COMPARISON ON)
    set(WITH_WERROR OFF)
    FetchContent_MakeAvailable(ser20)
    set(DR_EVT_SER20_LICENSE "${ser20_SOURCE_DIR}/LICENSE" PARENT_SCOPE)
  endfunction()
  dr_evt_make_ser20_available()

  set(DR_EVT_SER20_FETCHCONTENT ON)
  unset(DR_EVT_SER20_SOURCE_DIR)
endif ()

if (NOT TARGET ser20::ser20)
  message(FATAL_ERROR "Ser20 was requested but ser20::ser20 is unavailable")
endif ()

set(DR_EVT_HAS_SER20 TRUE)

if (DR_EVT_SER20_LICENSE)
  install(FILES "${DR_EVT_SER20_LICENSE}"
          DESTINATION "${CMAKE_INSTALL_DATADIR}/licenses/DR_EVT"
          RENAME "ser20-LICENSE")
endif ()
