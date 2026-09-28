/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file trace_policy.cpp
 * @brief Input adapters for the explicitly supported trace policies.
 */

#include "trace/trace_policy.hpp"

#include "trace/data_columns.hpp"
#include "trace/job_io.hpp"

namespace dr_evt {

int Standard_Trace_Policy::load_records(const std::string &fname,
                                        const Data_Columns &dcols,
                                        std::vector<record_type> &records,
                                        num_jobs_t max_count,
                                        const Trace_Admission_Limits &limits) {
  return load(fname, dcols, records, max_count, &limits);
}

int Pcon_Trace_Policy::load_records(const std::string &fname,
                                    const Data_Columns &dcols,
                                    std::vector<record_type> &records,
                                    num_jobs_t max_count,
                                    const Trace_Admission_Limits &limits) {
  return load(fname, dcols, records, max_count, &limits);
}

} // namespace dr_evt
