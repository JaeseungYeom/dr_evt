/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file dr_event.cpp
 * @brief Discrete resource-event formatting.
 */

#include "trace/dr_event.hpp"

namespace dr_evt {

std::ostream &operator<<(std::ostream &os, const DR_Event &evt) {
  using dr_evt::to_string;
  using std::to_string;

  const char *verb = "\tends at ";
  if (evt.is_arrival()) {
    verb = "\tstarts at ";
  } else if (evt.is_release()) {
    verb = "\treleases nodes at ";
  }
  std::string msg = "job " + to_string(evt.get_job_idx()) + ' ' + verb +
                    to_string(evt.get_time());
  os << msg;
  return os;
}

} // end of namespace dr_evt
