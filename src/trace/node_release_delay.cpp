/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file node_release_delay.cpp
 * @brief Configuration helpers and sampler for the node release delay.
 */

#include "trace/node_release_delay.hpp"

#include <algorithm>
#include <cmath>
#include <random>
#include <stdexcept>

namespace dr_evt {

const char *to_string(ReleaseDelayModel model) {
  switch (model) {
  case ReleaseDelayModel::EXPONENTIAL:
    return "exponential";
  case ReleaseDelayModel::UNIFORM:
    return "uniform";
  case ReleaseDelayModel::CONSTANT:
  default:
    return "constant";
  }
}

bool set_release_delay_model(ReleaseDelayModel &model,
                             const std::string &name) {
  if (name == "constant") {
    model = ReleaseDelayModel::CONSTANT;
  } else if (name == "exponential") {
    model = ReleaseDelayModel::EXPONENTIAL;
  } else if (name == "uniform") {
    model = ReleaseDelayModel::UNIFORM;
  } else {
    return false;
  }
  return true;
}

void validate_node_release_delay(double delay, ReleaseDelayModel model,
                                 double spread) {
  (void)model;
  if (!std::isfinite(delay) || delay < 0.0) {
    throw std::invalid_argument(
        "node_release_delay must be finite and nonnegative");
  }
  if (!std::isfinite(spread) || spread < 0.0) {
    throw std::invalid_argument(
        "node_release_delay_spread must be finite and nonnegative");
  }
}

bool node_release_delay_requested(double delay, ReleaseDelayModel model,
                                  double spread) {
  if (delay > 0.0) {
    return true;
  }
  return model == ReleaseDelayModel::UNIFORM && spread > 0.0;
}

Node_Release_Delay::Node_Release_Delay()
    : m_delay(0.0), m_spread(0.0), m_model(ReleaseDelayModel::CONSTANT),
      m_active(false), m_rng(0u) {}

Node_Release_Delay::Node_Release_Delay(double delay, ReleaseDelayModel model,
                                       double spread, unsigned seed,
                                       num_nodes_t max_nodes)
    : m_delay(delay), m_spread(spread), m_model(model),
      m_active(node_release_delay_requested(delay, model, spread)),
      m_rng(seed) {
  validate_node_release_delay(delay, model, spread);
  // Derive a stream distinct from the run-time sampler's, which is seeded
  // with the bare seed: same seed, same job durations, with or without a
  // release delay. The second word is an arbitrary fixed salt.
  m_rng.use_seed_seq(seed_seq_param_t{seed, 0x6e72646cu});
  if (m_active && m_model == ReleaseDelayModel::EXPONENTIAL) {
    // H(0) = 0, H(n) = H(n-1) + 1/n, summed in index order so a reference
    // implementation accumulating the same way reproduces every value.
    m_harmonic.resize(static_cast<size_t>(max_nodes) + 1, 0.0);
    for (size_t n = 1; n < m_harmonic.size(); ++n) {
      m_harmonic[n] = m_harmonic[n - 1] + 1.0 / static_cast<double>(n);
    }
  }
}

double Node_Release_Delay::harmonic(num_nodes_t n) const {
  if (n < m_harmonic.size()) {
    return m_harmonic[n];
  }
  double h = m_harmonic.empty() ? 0.0 : m_harmonic.back();
  for (size_t k = std::max<size_t>(m_harmonic.size(), 1); k <= n; ++k) {
    h += 1.0 / static_cast<double>(k);
  }
  return h;
}

tdiff_t Node_Release_Delay::planning_delay(num_nodes_t nodes) const {
  if (!m_active) {
    return 0.0;
  }
  switch (m_model) {
  case ReleaseDelayModel::EXPONENTIAL:
    return m_delay * harmonic(nodes);
  case ReleaseDelayModel::UNIFORM: {
    const double lo = std::max(0.0, m_delay - m_spread);
    const double hi = m_delay + m_spread;
    const double n = static_cast<double>(std::max<num_nodes_t>(nodes, 1u));
    return lo + (hi - lo) * n / (n + 1.0);
  }
  case ReleaseDelayModel::CONSTANT:
  default:
    return m_delay;
  }
}

tdiff_t Node_Release_Delay::sample(num_nodes_t nodes) {
  if (!m_active) {
    return 0.0;
  }
  if (m_model == ReleaseDelayModel::CONSTANT) {
    return m_delay;
  }
  // The maximum of n independent draws with distribution function F has
  // distribution function F^n, so one uniform u inverted through
  // F^-1(u^(1/n)) samples it directly.
  const double n = static_cast<double>(std::max<num_nodes_t>(nodes, 1u));
  std::uniform_real_distribution<double> unit(0.0, 1.0);
  double p = std::pow(m_rng.sample(unit), 1.0 / n);
  if (p >= 1.0) {
    p = std::nextafter(1.0, 0.0);
  }
  double value = m_delay;
  if (m_model == ReleaseDelayModel::EXPONENTIAL) {
    value = -m_delay * std::log1p(-p);
  } else {
    const double lo = std::max(0.0, m_delay - m_spread);
    const double hi = m_delay + m_spread;
    value = lo + (hi - lo) * p;
  }
  if (!std::isfinite(value) || value < 0.0) {
    value = 0.0;
  }
  return value;
}

} // end of namespace dr_evt
