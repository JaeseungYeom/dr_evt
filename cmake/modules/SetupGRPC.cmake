# Setup gRPC (and, transitively, Protobuf) for the DR_EVT client/server.
#
# Tries find_package() for both; if either is missing, fetches and
# builds gRPC from source (which bundles a compatible Protobuf).

find_package(Threads REQUIRED)

option(protobuf_MODULE_COMPATIBLE TRUE)

# Skip default system package locations while retaining explicit paths and a
# non-system project install prefix. If no complete package is found, the
# FetchContent fallback below builds gRPC with its bundled Protobuf.
option(AVOID_SYSTEM_GRPC "Do not search default system paths for gRPC/Protobuf" FALSE)
# find_package() consumes gRPC_DIR as a CMake variable, not directly as a
# shell environment variable. Promote an exported value unless an explicit
# -DgRPC_DIR=... has already been supplied.
if (NOT DEFINED gRPC_DIR AND DEFINED ENV{gRPC_DIR})
  set(gRPC_DIR "$ENV{gRPC_DIR}")
endif()
if (AVOID_SYSTEM_GRPC)
  set(DR_EVT_GRPC_SEARCH_MODE NO_DEFAULT_PATH)
else()
  set(DR_EVT_GRPC_SEARCH_MODE "")
endif()
unset(DR_EVT_GRPC_FETCHCONTENT CACHE)

# Dependency selection is re-evaluated on every configure.  A previous
# FetchContent build can leave _deps in the build directory, but it must not
# prevent a newly available installed gRPC/Protobuf from being selected.
unset(gRPC_FOUND CACHE)
unset(gRPC_FOUND)
set(DR_EVT_GRPC_INSTALL_HINTS)
# A package config can itself call find_package() for its dependencies (for
# example, gRPCConfig.cmake locates Abseil). HINTS only applies to the outer
# call, so make a nonempty project prefix visible through the whole nested
# lookup. Restore the caller's prefix list immediately afterward.
set(DR_EVT_SAVED_CMAKE_PREFIX_PATH "${CMAKE_PREFIX_PATH}")
set(DR_EVT_USE_INSTALL_PREFIX TRUE)
if (AVOID_SYSTEM_GRPC AND CMAKE_INSTALL_PREFIX)
  list(FIND CMAKE_SYSTEM_PREFIX_PATH "${CMAKE_INSTALL_PREFIX}"
       DR_EVT_GRPC_SYSTEM_PREFIX_INDEX)
  if (NOT DR_EVT_GRPC_SYSTEM_PREFIX_INDEX EQUAL -1)
    set(DR_EVT_USE_INSTALL_PREFIX FALSE)
  endif()
endif()
if (CMAKE_INSTALL_PREFIX AND DR_EVT_USE_INSTALL_PREFIX)
  list(PREPEND CMAKE_PREFIX_PATH "${CMAKE_INSTALL_PREFIX}")
  list(APPEND DR_EVT_GRPC_INSTALL_HINTS
       "${CMAKE_INSTALL_PREFIX}/lib/cmake/grpc"
       "${CMAKE_INSTALL_PREFIX}/lib64/cmake/grpc"
       "${CMAKE_INSTALL_PREFIX}")
endif()
unset(DR_EVT_GRPC_SYSTEM_PREFIX_INDEX)
unset(DR_EVT_USE_INSTALL_PREFIX)

# A FetchContent build tree can contain gRPCConfig.cmake before its exported
# target files exist. It is not an installed package; allow FetchContent below
# to reuse its source tree instead of loading this incomplete config.
if (DEFINED gRPC_DIR AND
    gRPC_DIR STREQUAL "${CMAKE_BINARY_DIR}/_deps/grpc-build" AND
    (NOT EXISTS "${gRPC_DIR}/gRPCTargets.cmake" OR
     NOT EXISTS "${gRPC_DIR}/gRPCPluginTargets.cmake"))
  unset(gRPC_DIR CACHE)
  unset(gRPC_DIR)
endif()

# Select gRPC first. Importing standalone Protobuf before this decision leaves
# protobuf::* aliases that collide with gRPC's bundled copy on fallback.
find_package(gRPC CONFIG QUIET
             HINTS ${DR_EVT_GRPC_INSTALL_HINTS} ${CMAKE_PREFIX_PATH}
             ${DR_EVT_GRPC_SEARCH_MODE})
if (gRPC_FOUND)
  message(STATUS "Found gRPC: ${gRPC_VERSION} (gRPC_DIR: ${gRPC_DIR})")
  if (TARGET protobuf::libprotobuf AND TARGET protobuf::protoc)
    set(Protobuf_FOUND TRUE)
  else()
    # Older gRPC configs do not always load their Protobuf dependency.
    find_package(Protobuf CONFIG QUIET HINTS "${gRPC_DIR}"
                 ${DR_EVT_GRPC_SEARCH_MODE})
    if (NOT Protobuf_FOUND AND NOT AVOID_SYSTEM_GRPC)
      find_package(Protobuf MODULE QUIET)
    endif()
  endif()
endif()

unset(DR_EVT_GRPC_INSTALL_HINTS)
set(CMAKE_PREFIX_PATH "${DR_EVT_SAVED_CMAKE_PREFIX_PATH}")
unset(DR_EVT_SAVED_CMAKE_PREFIX_PATH)

# gRPC and Protobuf must come from the same selected source.  Record the
# current result for this configure; do not cache it or use it to skip
# discovery on a future configure.
if (gRPC_FOUND AND Protobuf_FOUND)
  set(DR_EVT_GRPC_FETCHCONTENT OFF)
else()
  set(DR_EVT_GRPC_FETCHCONTENT ON)
endif()

if (DR_EVT_GRPC_FETCHCONTENT)
  if (EXISTS "${CMAKE_BINARY_DIR}/_deps/grpc-src/CMakeLists.txt")
    message(STATUS  "Reusing gRPC source already fetched under "
                    "${CMAKE_BINARY_DIR}/_deps/grpc-src (not re-downloading).")
  else()
    message(STATUS  "gRPC and/or Protobuf not found as installed packages - "
                    "building gRPC (with its own bundled Protobuf) via "
                    "FetchContent instead. This will download gRPC's full "
                    "source tree, which can take a while the first time.")
  endif()

  include(FetchContent)
  # Bundled gRPC and its dependencies are linked statically into DR_EVT, so
  # they are not installed unless explicitly requested. Installing the whole
  # compatible stack allows a later clean build to find it in this project's
  # install prefix rather than configuring and rebuilding the fetched source.
  if (DR_EVT_INSTALL_FETCHED_GRPC)
    message(STATUS "Fetched gRPC dependency installation enabled")
    set(ABSL_ENABLE_INSTALL ON CACHE BOOL
        "Install bundled Abseil with DR_EVT" FORCE)
    set(gRPC_INSTALL ON CACHE BOOL
        "Install bundled gRPC with DR_EVT" FORCE)
    set(protobuf_INSTALL ON CACHE BOOL
        "Install bundled Protobuf with DR_EVT" FORCE)
    set(utf8_range_ENABLE_INSTALL ON CACHE BOOL
        "Install bundled utf8_range with DR_EVT" FORCE)

    # gRPC defaults to lib even when the parent project uses lib64. Keep all
    # installed packages in the parent's GNUInstallDirs locations so nested
    # find_dependency() calls can resolve the complete stack from one prefix.
    set(gRPC_INSTALL_BINDIR "${CMAKE_INSTALL_BINDIR}" CACHE STRING
        "Installation directory for gRPC executables" FORCE)
    set(gRPC_INSTALL_LIBDIR "${CMAKE_INSTALL_LIBDIR}" CACHE STRING
        "Installation directory for gRPC libraries" FORCE)
    set(gRPC_INSTALL_INCLUDEDIR "${CMAKE_INSTALL_INCLUDEDIR}" CACHE STRING
        "Installation directory for gRPC headers" FORCE)
    set(gRPC_INSTALL_CMAKEDIR
        "${CMAKE_INSTALL_LIBDIR}/cmake/grpc" CACHE STRING
        "Installation directory for gRPC CMake package files" FORCE)
    set(gRPC_INSTALL_SHAREDIR "${CMAKE_INSTALL_DATADIR}/grpc" CACHE STRING
        "Installation directory for gRPC data files" FORCE)

    # Bundled zlib predates GNUInstallDirs and otherwise caches absolute
    # ${CMAKE_INSTALL_PREFIX}/lib paths.  Normal variables override those
    # cache defaults while zlib is configured and keep it aligned with the
    # parent's selected lib/lib64 layout.
    set(INSTALL_BIN_DIR "${CMAKE_INSTALL_FULL_BINDIR}" CACHE PATH
        "Installation directory for bundled zlib executables" FORCE)
    set(INSTALL_LIB_DIR "${CMAKE_INSTALL_FULL_LIBDIR}" CACHE PATH
        "Installation directory for bundled zlib libraries" FORCE)
    set(INSTALL_INC_DIR "${CMAKE_INSTALL_FULL_INCLUDEDIR}" CACHE PATH
        "Installation directory for bundled zlib headers" FORCE)
    set(INSTALL_MAN_DIR "${CMAKE_INSTALL_FULL_MANDIR}" CACHE PATH
        "Installation directory for bundled zlib man pages" FORCE)
    set(INSTALL_PKGCONFIG_DIR "${CMAKE_INSTALL_FULL_LIBDIR}/pkgconfig"
        CACHE PATH "Installation directory for bundled zlib pkg-config files"
        FORCE)
  else()
    set(ABSL_ENABLE_INSTALL OFF CACHE BOOL
        "Do not install bundled Abseil with DR_EVT" FORCE)
    set(gRPC_INSTALL OFF CACHE BOOL
        "Do not install bundled gRPC with DR_EVT" FORCE)
    set(protobuf_INSTALL OFF CACHE BOOL
        "Do not install bundled Protobuf with DR_EVT" FORCE)
    # Protobuf forwards protobuf_INSTALL to this option without FORCE. An
    # older configure can otherwise leave it ON and produce an invalid
    # utf8_range export referring to non-installed Abseil targets.
    set(utf8_range_ENABLE_INSTALL OFF CACHE BOOL
        "Do not install bundled utf8_range with DR_EVT" FORCE)
  endif()

  # Pinned version. Bumping this should come with re-verifying
  # dr_evt_server.cpp/dr_evt_client.cpp's gRPC C++ API usage against
  # whatever that release's API surface looks like.
  #
  # CMAKE_POLICY_VERSION_MINIMUM: gRPC bundles third-party dependencies
  # (e.g. c-ares) whose CMakeLists.txt specify a cmake_minimum_required()
  # below 3.5, which recent CMake refuses outright. This applies
  # policies as if 3.5 had been requested for those subprojects.
  set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

  # A nested cmake_minimum_required() can reset the caller's CMP0077 setting.
  # Supply the default explicitly so legacy gRPC dependencies honor the
  # BUILD_TESTING and BUILD_SHARED_LIBS values set by this parent project.
  if(DEFINED CMAKE_POLICY_DEFAULT_CMP0077)
    set(DR_EVT_SAVED_POLICY_DEFAULT_CMP0077
        "${CMAKE_POLICY_DEFAULT_CMP0077}")
    set(DR_EVT_HAD_POLICY_DEFAULT_CMP0077 TRUE)
  else()
    set(DR_EVT_HAD_POLICY_DEFAULT_CMP0077 FALSE)
  endif()
  set(CMAKE_POLICY_DEFAULT_CMP0077 NEW)

  # gRPC's bundled Abseil emits both x86_64 and arm64 SIMD flags on
  # every Apple build via -Xarch_<arch> pairs, which a single-arch
  # compiler rejects. The PATCH_COMMAND below restricts that loop to
  # the actual target arch; this just sets CMAKE_OSX_ARCHITECTURES
  # explicitly so the patch has something to work with.
  if (APPLE AND NOT CMAKE_OSX_ARCHITECTURES)
    execute_process(COMMAND uname -m OUTPUT_VARIABLE DR_EVT_HOST_ARCH
                     OUTPUT_STRIP_TRAILING_WHITESPACE)
    set(CMAKE_OSX_ARCHITECTURES "${DR_EVT_HOST_ARCH}")
    unset(DR_EVT_HOST_ARCH)
  endif()

  # Force static for gRPC's own dependencies (upb, Abseil, etc.) -
  # shared would otherwise fail to link on macOS. Restored after.
  set(DR_EVT_SAVED_BUILD_SHARED_LIBS ${BUILD_SHARED_LIBS})
  set(BUILD_SHARED_LIBS OFF)

  # gRPC's own tests - and BoringSSL's bundled googletest they pull
  # in - are memory-hungry to compile and unused by dr_evt. Skipping
  # them avoids OOM-killing the build on memory-constrained machines
  # (e.g. `make -j$(nproc)` on standard GitHub Actions runners).
  set(DR_EVT_SAVED_BUILD_TESTING ${BUILD_TESTING})
  set(BUILD_TESTING OFF)
  set(gRPC_BUILD_TESTS OFF CACHE BOOL "" FORCE)
  # These bundled projects otherwise put examples and test programs in the
  # default ALL target when gRPC is included normally for installation.
  set(RE2_BUILD_TESTING OFF CACHE BOOL "" FORCE)
  set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

  # dr_evt only compiles .proto files to C++, so skip gRPC's codegen
  # plugins for other languages. (Python client authors: use
  # `pip install grpcio-tools` instead - see CLIENT_SERVER_GUIDE.md.)
  set(gRPC_BUILD_GRPC_CSHARP_PLUGIN OFF CACHE BOOL "" FORCE)
  set(gRPC_BUILD_GRPC_NODE_PLUGIN OFF CACHE BOOL "" FORCE)
  set(gRPC_BUILD_GRPC_OBJECTIVE_C_PLUGIN OFF CACHE BOOL "" FORCE)
  set(gRPC_BUILD_GRPC_PHP_PLUGIN OFF CACHE BOOL "" FORCE)
  set(gRPC_BUILD_GRPC_PYTHON_PLUGIN OFF CACHE BOOL "" FORCE)
  set(gRPC_BUILD_GRPC_RUBY_PLUGIN OFF CACHE BOOL "" FORCE)

  # NOT set here: gRPC_SSL_PROVIDER=package would skip building
  # BoringSSL from source entirely (the biggest remaining compile
  # cost), using system OpenSSL instead. Left as an opt-in
  # (`-DgRPC_SSL_PROVIDER=package`, requires libssl-dev) since it's
  # unverified end-to-end and BoringSSL/OpenSSL can drift apart.

  # This project's own warning flags would otherwise apply to gRPC's source.
  # Suppress both languages because upb, c-ares, and other bundled libraries
  # contain C sources. Trailing -w overrides earlier warning flags.
  set(DR_EVT_SAVED_C_FLAGS "${CMAKE_C_FLAGS}")
  set(DR_EVT_SAVED_CXX_FLAGS "${CMAKE_CXX_FLAGS}")
  set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -w")
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -w")

  # BoringSSL hardcodes lib/cmake/OpenSSL. Patch it to honor GNUInstallDirs so
  # installations selected as lib64 do not also create a lib directory.
  set(DR_EVT_BORINGSSL_INSTALL_PATCH
      ${CMAKE_SOURCE_DIR}/cmake/patches/fix_boringssl_install_libdir.cmake)
  set(DR_EVT_GRPC_PATCH_COMMAND
      ${CMAKE_COMMAND} -P ${DR_EVT_BORINGSSL_INSTALL_PATCH})
  # PATCH_COMMAND does not rerun for an already-populated FetchContent source.
  # Apply the idempotent patch now as well so reconfiguration is sufficient.
  if(EXISTS "${CMAKE_BINARY_DIR}/_deps/grpc-src/third_party/boringssl-with-bazel/CMakeLists.txt")
    execute_process(
      COMMAND ${CMAKE_COMMAND} -P ${DR_EVT_BORINGSSL_INSTALL_PATCH}
      WORKING_DIRECTORY "${CMAKE_BINARY_DIR}/_deps/grpc-src"
      COMMAND_ERROR_IS_FATAL ANY)
  endif()
  if (APPLE)
    list(APPEND DR_EVT_GRPC_PATCH_COMMAND
        COMMAND ${CMAKE_COMMAND} -DCMAKE_OSX_ARCHITECTURES=${CMAKE_OSX_ARCHITECTURES}
                         -DCMAKE_SYSTEM_PROCESSOR=${CMAKE_SYSTEM_PROCESSOR}
                         -P ${CMAKE_SOURCE_DIR}/cmake/patches/fix_abseil_randen_copts.cmake
        COMMAND ${CMAKE_COMMAND}
                -P ${CMAKE_SOURCE_DIR}/cmake/patches/fix_zlib_fdopen.cmake)
  endif()

  # Without this, FetchContent re-contacts the git remote on every
  # configure to check for upstream changes, even once fully
  # downloaded. The tag is pinned, so there's nothing to check for.
  set(FETCHCONTENT_UPDATES_DISCONNECTED_GRPC ON)

  set(DR_EVT_GRPC_FETCHCONTENT_OPTIONS)
  if (NOT DR_EVT_INSTALL_FETCHED_GRPC AND
      CMAKE_VERSION VERSION_GREATER_EQUAL 3.28)
    list(APPEND DR_EVT_GRPC_FETCHCONTENT_OPTIONS EXCLUDE_FROM_ALL)
  endif()
  FetchContent_Declare(
    grpc
    GIT_REPOSITORY https://github.com/grpc/grpc.git
    GIT_TAG        v1.83.1
    PATCH_COMMAND  ${DR_EVT_GRPC_PATCH_COMMAND}
    ${DR_EVT_GRPC_FETCHCONTENT_OPTIONS})
  # FetchContent_MakeAvailable() adds gRPC as a normal subdirectory, which
  # also adds every nested third-party install rule to this project's
  # `install` target.  Populate explicitly so CMake 3.24 (our minimum) can
  # add it with EXCLUDE_FROM_ALL; FetchContent_Declare(EXCLUDE_FROM_ALL) is
  # only available in newer CMake versions.
  if (CMAKE_VERSION VERSION_GREATER_EQUAL 3.28)
    FetchContent_MakeAvailable(grpc)
  else()
    FetchContent_GetProperties(grpc)
    if (NOT grpc_POPULATED)
      FetchContent_Populate(grpc)
      if (DR_EVT_INSTALL_FETCHED_GRPC)
        add_subdirectory("${grpc_SOURCE_DIR}" "${grpc_BINARY_DIR}")
      else()
        add_subdirectory("${grpc_SOURCE_DIR}" "${grpc_BINARY_DIR}"
                         EXCLUDE_FROM_ALL)
      endif()
    endif()
  endif()
  unset(DR_EVT_GRPC_FETCHCONTENT_OPTIONS)
  unset(DR_EVT_GRPC_PATCH_COMMAND)
  unset(DR_EVT_BORINGSSL_INSTALL_PATCH)
  set(CMAKE_C_FLAGS "${DR_EVT_SAVED_C_FLAGS}")
  set(CMAKE_CXX_FLAGS "${DR_EVT_SAVED_CXX_FLAGS}")
  unset(DR_EVT_SAVED_C_FLAGS)
  unset(DR_EVT_SAVED_CXX_FLAGS)
  if(DR_EVT_HAD_POLICY_DEFAULT_CMP0077)
    set(CMAKE_POLICY_DEFAULT_CMP0077
        "${DR_EVT_SAVED_POLICY_DEFAULT_CMP0077}")
  else()
    unset(CMAKE_POLICY_DEFAULT_CMP0077)
  endif()
  unset(DR_EVT_SAVED_POLICY_DEFAULT_CMP0077)
  unset(DR_EVT_HAD_POLICY_DEFAULT_CMP0077)
  set(BUILD_SHARED_LIBS ${DR_EVT_SAVED_BUILD_SHARED_LIBS})
  unset(DR_EVT_SAVED_BUILD_SHARED_LIBS)
  set(BUILD_TESTING ${DR_EVT_SAVED_BUILD_TESTING})
  unset(DR_EVT_SAVED_BUILD_TESTING)

  # FetchContent's add_subdirectory gives gRPC's own (non-namespaced)
  # target names directly. Alias them to the same protobuf::* / gRPC::*
  # names find_package() would produce, so the rest of this project's
  # CMake doesn't need to know which discovery path was taken.
  if (NOT TARGET protobuf::libprotobuf)
    add_library(protobuf::libprotobuf ALIAS libprotobuf)
  endif()
  if (NOT TARGET protobuf::protoc)
    add_executable(protobuf::protoc ALIAS protoc)
  endif()
  if (NOT TARGET gRPC::grpc++)
    add_library(gRPC::grpc++ ALIAS grpc++)
  endif()
  if (NOT TARGET gRPC::grpc_cpp_plugin)
    add_executable(gRPC::grpc_cpp_plugin ALIAS grpc_cpp_plugin)
  endif()
else()
  if (NOT TARGET gRPC::grpc_cpp_plugin)
    message(FATAL_ERROR
      "gRPC found, but the gRPC::grpc_cpp_plugin target is missing - "
      "check that your gRPC installation includes the C++ plugin "
      "(on Debian/Ubuntu, this is part of protobuf-compiler-grpc).")
  endif()
endif()

set(DR_EVT_HAS_GRPC TRUE)

# This module provides Protobuf too, whenever gRPC is enabled - see
# CMakeLists.txt's root-level ordering, which skips SetupProtobuf.cmake
# in that case.
set(DR_EVT_HAS_PROTOBUF TRUE)
