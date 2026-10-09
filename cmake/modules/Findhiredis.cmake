# Locate hiredis installations that provide headers and a library but no CMake
# package configuration. This also lets redis++-config.cmake satisfy its
# find_dependency(hiredis) call on such systems.

find_path(hiredis_INCLUDE_DIR NAMES hiredis/hiredis.h)
find_library(hiredis_LIBRARY NAMES hiredis hiredis_static)

set(hiredis_VERSION "")
if (hiredis_INCLUDE_DIR)
  file(STRINGS "${hiredis_INCLUDE_DIR}/hiredis/hiredis.h"
    _hiredis_version_lines
    REGEX "^#define HIREDIS_(MAJOR|MINOR|PATCH) [0-9]+$")
  foreach (_hiredis_component MAJOR MINOR PATCH)
    foreach (_hiredis_version_line IN LISTS _hiredis_version_lines)
      if (_hiredis_version_line MATCHES
          "^#define HIREDIS_${_hiredis_component} ([0-9]+)$")
        set(_hiredis_${_hiredis_component} "${CMAKE_MATCH_1}")
      endif ()
    endforeach ()
  endforeach ()
  if (DEFINED _hiredis_MAJOR AND DEFINED _hiredis_MINOR AND
      DEFINED _hiredis_PATCH)
    set(hiredis_VERSION
      "${_hiredis_MAJOR}.${_hiredis_MINOR}.${_hiredis_PATCH}")
  endif ()
endif ()

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(hiredis
  REQUIRED_VARS hiredis_LIBRARY hiredis_INCLUDE_DIR
  VERSION_VAR hiredis_VERSION)

if (hiredis_FOUND)
  set(hiredis_INCLUDE_DIRS "${hiredis_INCLUDE_DIR}")
  set(hiredis_LIBRARIES "${hiredis_LIBRARY}")
  if (NOT TARGET hiredis::hiredis)
    add_library(hiredis::hiredis UNKNOWN IMPORTED)
    set_target_properties(hiredis::hiredis PROPERTIES
      IMPORTED_LOCATION "${hiredis_LIBRARY}"
      INTERFACE_INCLUDE_DIRECTORIES "${hiredis_INCLUDE_DIR}")
  endif ()
endif ()

mark_as_advanced(hiredis_INCLUDE_DIR hiredis_LIBRARY)

unset(_hiredis_component)
unset(_hiredis_version_line)
unset(_hiredis_version_lines)
unset(_hiredis_MAJOR)
unset(_hiredis_MINOR)
unset(_hiredis_PATCH)
