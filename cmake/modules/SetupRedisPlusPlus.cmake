# Setup redis-plus-plus for DR_EVT.
#
# Prefer an installed package. If none is available, fetch and build a pinned
# upstream release. redis-plus-plus requires hiredis; its CMake project locates
# either an installed hiredis package or one supplied through CMake's usual
# hiredis hints.

option(AVOID_SYSTEM_REDIS_PLUS_PLUS
  "Do not search default system paths for redis-plus-plus" FALSE)

unset(DR_EVT_REDIS_PLUS_PLUS_FETCHCONTENT CACHE)

set(DR_EVT_REDIS_PLUS_PLUS_FIND_OPTIONS)
if (AVOID_SYSTEM_REDIS_PLUS_PLUS)
  list(APPEND DR_EVT_REDIS_PLUS_PLUS_FIND_OPTIONS NO_DEFAULT_PATH)
endif ()

# An installed redis++ package delegates hiredis discovery through
# find_dependency(), which is fatal even when redis++ itself was requested
# QUIETly. Probe that dependency first using this project's Findhiredis module,
# which supports systems that provide only the hiredis headers and library. If
# hiredis is genuinely unavailable, skip the unusable installed redis++ package
# so normal fetched-source handling can report or resolve the dependency.
find_package(hiredis QUIET)
if (hiredis_FOUND)
  find_package(redis++ CONFIG QUIET
    HINTS ${CMAKE_PREFIX_PATH}
    ${DR_EVT_REDIS_PLUS_PLUS_FIND_OPTIONS})
else ()
  message(STATUS
    "hiredis CMake package not found; skipping installed redis-plus-plus "
    "package discovery")
endif ()
unset(DR_EVT_REDIS_PLUS_PLUS_FIND_OPTIONS)

if (TARGET redis++::redis++)
  set(DR_EVT_REDIS_PLUS_PLUS_TARGET redis++::redis++)
elseif (TARGET redis++::redis++_static)
  set(DR_EVT_REDIS_PLUS_PLUS_TARGET redis++::redis++_static)
else ()
  include(FetchContent)

  if (FETCHCONTENT_SOURCE_DIR_REDIS_PLUS_PLUS)
    set(DR_EVT_REDIS_PLUS_PLUS_SOURCE_DIR
      "${FETCHCONTENT_SOURCE_DIR_REDIS_PLUS_PLUS}")
  else ()
    set(DR_EVT_REDIS_PLUS_PLUS_SOURCE_DIR
      "${FETCHCONTENT_BASE_DIR}/redis_plus_plus-src")
  endif ()

  if (EXISTS "${DR_EVT_REDIS_PLUS_PLUS_SOURCE_DIR}/CMakeLists.txt")
    message(STATUS "Reusing redis-plus-plus source already fetched under "
                   "${DR_EVT_REDIS_PLUS_PLUS_SOURCE_DIR} "
                   "(not re-downloading).")
  else ()
    message(STATUS "redis-plus-plus was not found as an installed package; "
                   "fetching its pinned source with FetchContent.")
  endif ()
  unset(DR_EVT_REDIS_PLUS_PLUS_SOURCE_DIR)

  # The tag is pinned so reconfiguration never needs to contact the remote.
  set(FETCHCONTENT_UPDATES_DISCONNECTED_REDIS_PLUS_PLUS ON)
  FetchContent_Declare(
    redis_plus_plus
    GIT_REPOSITORY https://github.com/sewenew/redis-plus-plus.git
    GIT_TAG 1.3.15
    GIT_SHALLOW TRUE)

  # DR_EVT does not need redis-plus-plus' tests. Build one library variant,
  # matching the parent project's BUILD_SHARED_LIBS choice.
  set(REDIS_PLUS_PLUS_BUILD_TEST OFF CACHE BOOL
    "Do not build redis-plus-plus tests with DR_EVT" FORCE)
  if (BUILD_SHARED_LIBS)
    set(REDIS_PLUS_PLUS_BUILD_SHARED ON CACHE BOOL
      "Build shared redis-plus-plus with DR_EVT" FORCE)
    set(REDIS_PLUS_PLUS_BUILD_STATIC OFF CACHE BOOL
      "Do not also build static redis-plus-plus" FORCE)
  else ()
    set(REDIS_PLUS_PLUS_BUILD_SHARED OFF CACHE BOOL
      "Do not also build shared redis-plus-plus" FORCE)
    set(REDIS_PLUS_PLUS_BUILD_STATIC ON CACHE BOOL
      "Build static redis-plus-plus with DR_EVT" FORCE)
  endif ()

  FetchContent_MakeAvailable(redis_plus_plus)
  set(DR_EVT_REDIS_PLUS_PLUS_FETCHCONTENT ON)

  if (TARGET redis++)
    set(DR_EVT_REDIS_PLUS_PLUS_TARGET redis++)
  elseif (TARGET redis++_static)
    set(DR_EVT_REDIS_PLUS_PLUS_TARGET redis++_static)
  endif ()
endif ()

if (NOT DR_EVT_REDIS_PLUS_PLUS_FETCHCONTENT AND
    DR_EVT_REDIS_PLUS_PLUS_TARGET)
  message(STATUS "Found redis-plus-plus: ${redis++_VERSION} "
                 "(redis++_DIR: ${redis++_DIR})")
endif ()

if (NOT DR_EVT_REDIS_PLUS_PLUS_TARGET)
  message(FATAL_ERROR
    "redis-plus-plus was requested but no redis-plus-plus target is available")
endif ()

# redis-plus-plus 1.3.15 does not propagate hiredis when its legacy
# FindHiredis.cmake fallback finds a library without a CMake package.  This is
# especially visible for static builds: redis++ is present on the final link
# line, but hiredis is not.  Give DR_EVT one complete dependency target for
# both the fetched and installed-package paths.
if (NOT TARGET DR_EVT::redis_plus_plus)
  if (TARGET hiredis::hiredis_static AND NOT BUILD_SHARED_LIBS)
    set(DR_EVT_HIREDIS_TARGET hiredis::hiredis_static)
  elseif (TARGET hiredis::hiredis)
    set(DR_EVT_HIREDIS_TARGET hiredis::hiredis)
  elseif (HIREDIS_LIB)
    set(DR_EVT_HIREDIS_TARGET "${HIREDIS_LIB}")
  else ()
    find_library(DR_EVT_HIREDIS_LIBRARY NAMES hiredis REQUIRED)
    set(DR_EVT_HIREDIS_TARGET "${DR_EVT_HIREDIS_LIBRARY}")
  endif ()

  add_library(dr_evt_redis_plus_plus INTERFACE)
  target_link_libraries(dr_evt_redis_plus_plus INTERFACE
    ${DR_EVT_REDIS_PLUS_PLUS_TARGET}
    ${DR_EVT_HIREDIS_TARGET})
  add_library(DR_EVT::redis_plus_plus ALIAS dr_evt_redis_plus_plus)
endif ()

set(DR_EVT_HAS_REDIS_PLUS_PLUS TRUE)
