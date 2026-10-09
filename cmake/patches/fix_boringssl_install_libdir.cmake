# Patch gRPC's bundled BoringSSL compatibility package to honor the library
# directory selected by the parent project's GNUInstallDirs configuration.
# BoringSSL already uses CMAKE_INSTALL_LIBDIR for its archives, but hardcodes
# the OpenSSL CMake package under lib/cmake, producing both lib and lib64.

# PATCH_COMMAND runs with gRPC's fetched source root as its working directory.
set(_target_file
    "third_party/boringssl-with-bazel/CMakeLists.txt")
file(READ "${_target_file}" _content)

set(_old_destination "DESTINATION lib/cmake/OpenSSL)")
set(_new_destination
    "DESTINATION \${CMAKE_INSTALL_LIBDIR}/cmake/OpenSSL)")
string(FIND "${_content}" "${_old_destination}" _old_position)
string(FIND "${_content}" "${_new_destination}" _new_position)

if(NOT _old_position EQUAL -1)
  string(REPLACE "${_old_destination}" "${_new_destination}"
         _patched_content "${_content}")
  file(WRITE "${_target_file}" "${_patched_content}")
  message(STATUS "Patched BoringSSL's OpenSSL package install directory")
elseif(NOT _new_position EQUAL -1)
  message(STATUS "BoringSSL's OpenSSL package install directory is patched")
else()
  message(FATAL_ERROR
    "fix_boringssl_install_libdir.cmake: expected install destinations were "
    "not found; BoringSSL's CMakeLists.txt may have changed")
endif()
