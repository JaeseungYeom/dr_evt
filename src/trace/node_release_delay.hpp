/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file node_release_delay.hpp
 * @brief Delay between a job's end and the reuse of its nodes.
 * @details After a job ends, its nodes stay unavailable until the slowest of
 * them has finished cleaning up. The delay is a constant, or the maximum of
 * independent per-node cleanup times, which makes it grow with job size. One
 * random draw per job end suffices: the maximum of independent exponential or
 * uniform variables has a closed-form inverse distribution function.
 */

#ifndef DR_EVT_TRACE_NODE_RELEASE_DELAY_HPP
#define DR_EVT_TRACE_NODE_RELEASE_DELAY_HPP

#include "common.hpp"
#include "sim/scheduler_policies.hpp"
#include "utils/rngen.hpp"
#include <string>

namespace dr_evt {
/** \addtogroup dr_evt_trace
 *  @{ */

/** @brief Return the configuration spelling of a release-delay model.
 * @param[in] model Model value.
 * @return Static null-terminated name: "constant", "exponential", or
 * "uniform". */
const char *to_string(ReleaseDelayModel model);

/** @brief Parse a release-delay model name.
 * @param[out] model Receives the parsed value on success.
 * @param[in] name One of constant, exponential, uniform.
 * @return true when @p name was recognized. */
bool set_release_delay_model(ReleaseDelayModel &model, const std::string &name);

/** @brief Reject an invalid release-delay configuration.
 * @param[in] delay Configured delay in seconds.
 * @param[in] model Delay model.
 * @param[in] spread Uniform half-width in seconds.
 * @throws std::invalid_argument if @p delay or @p spread is negative or not
 * finite. */
void validate_node_release_delay(double delay, ReleaseDelayModel model,
                                 double spread);

/** @brief Report whether a configuration ever delays a release.
 * @details A zero delay disables the model, except that a uniform model with a
 * positive spread still produces positive per-node times.
 * @param[in] delay Configured delay in seconds.
 * @param[in] model Delay model.
 * @param[in] spread Uniform half-width in seconds.
 * @return true when some release would be delayed. */
bool node_release_delay_requested(double delay, ReleaseDelayModel model,
                                  double spread);

/**
 * @brief Per-job release delay sampler.
 * @details Owns a random engine seeded independently of the run-time sampler,
 * so enabling the delay never changes the sampled job durations of a run with
 * the same seed. The engine state is part of a simulation checkpoint. The
 * scheduler does not plan with the delay: as in Slurm, Flux, and LSF, a
 * running job's projected release is its start time plus its time limit, and
 * the cleanup time is discovered when the nodes fail to return. Once a job
 * has ended, its nodes are observably in cleanup and the simulation projects
 * their actual sampled release.
 */
class Node_Release_Delay {
public:
  /** @brief Construct an inactive model: every release is immediate. */
  Node_Release_Delay();

  /** @brief Construct a model from validated configuration.
   * @param[in] delay Constant delay, or the per-node mean (exponential) or
   * center (uniform), in seconds.
   * @param[in] model Delay model.
   * @param[in] spread Uniform half-width in seconds; ignored otherwise.
   * @param[in] seed Simulation seed from which the engine stream is derived.
   * @throws std::invalid_argument on an invalid configuration. */
  Node_Release_Delay(double delay, ReleaseDelayModel model, double spread,
                     unsigned seed);

  /** @brief Report whether releases are delayed at all.
   * @return true when sample() can return a positive delay. */
  bool active() const { return m_active; }

  /** @brief Return the configured delay. @return Seconds. */
  double delay() const { return m_delay; }
  /** @brief Return the configured model. @return ReleaseDelayModel value. */
  ReleaseDelayModel model() const { return m_model; }
  /** @brief Return the configured spread. @return Seconds. */
  double spread() const { return m_spread; }

  /** @brief Draw the delay for one release.
   * @param[in] nodes Node count of the job that ended.
   * @return Nonnegative finite delay in seconds; the constant when the model
   * is CONSTANT. */
  tdiff_t sample(num_nodes_t nodes);

  /** @brief Expose the engine for checkpoint serialization.
   * @return Mutable reference to the owned generator. */
  RNGen<> &engine() { return m_rng; }
  /** @copydoc engine() */
  const RNGen<> &engine() const { return m_rng; }

private:
  double m_delay;            ///< Configured delay in seconds.
  double m_spread;           ///< Configured uniform half-width in seconds.
  ReleaseDelayModel m_model; ///< Delay model.
  bool m_active;             ///< Whether any delay is applied.
  RNGen<> m_rng;             ///< Engine for sampled delays.
};

/**@}*/
} // end of namespace dr_evt

#endif // DR_EVT_TRACE_NODE_RELEASE_DELAY_HPP
