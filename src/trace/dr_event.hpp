/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file dr_event.hpp
 * @brief Discrete resource-event representation and ordering.
 */

#ifndef DR_EVT_TRACE_DR_EVENT_HPP
#define DR_EVT_TRACE_DR_EVENT_HPP

#include "common.hpp"
#include "trace/epoch.hpp"
#include "trace/event_pool_allocator.hpp"
#include <set>

namespace dr_evt {
/** \addtogroup dr_evt_trace
 *  @{ */

/**
 * @brief A scheduled start, end, or node-release event for one trace job.
 * @details Events are ordered by timestamp, event kind, and job identifier so
 * an event_q_t can process resource-state transitions deterministically.
 * The timestamp is the simulation clock's own scalar type: it is converted
 * from a job record's epoch_t once, when the event is created, so the event
 * loop compares a single value and never re-derives it. A node-release event
 * carries its own node count: its job record may already have been written
 * out and reclaimed by the time the release is processed.
 */
class DR_Event {
protected:
  /// Trace job identifier affected by this event.
  job_no_t m_jidx;
  /// Simulation time at which the resource transition occurs.
  sim_time_t m_t;
  /// Nodes returned to service by a release event; zero for other kinds.
  num_nodes_t m_nodes;
  /// Event kind: start, end, or delayed node release.
  DR_Event_Kind m_type;

public:
  /** @brief Construct a job start or end event.
   * @param[in] idx Trace job identifier.
   * @param[in] t Event timestamp.
   * @param[in] type Event kind. */
  DR_Event(job_no_t idx, sim_time_t t, DR_Event_Kind type)
      : m_jidx(idx), m_t(t), m_nodes(static_cast<num_nodes_t>(0u)),
        m_type(type) {}

  /// The kind is an enumeration; a bool (the former representation) would
  /// otherwise convert silently.
  DR_Event(job_no_t idx, sim_time_t t, bool) = delete;
  /// Timestamps are scalar; convert an epoch_t with convert_epoch() first.
  DR_Event(job_no_t idx, const epoch_t &t, DR_Event_Kind type) = delete;

  /** @brief Build a node-release event.
   * @param[in] idx Trace job identifier whose nodes are released.
   * @param[in] t Time at which the nodes become reusable.
   * @param[in] nodes Node count returned to service at @p t.
   * @return Event of kind DR_Event_Kind::release carrying @p nodes. */
  static DR_Event release(job_no_t idx, sim_time_t t, num_nodes_t nodes) {
    DR_Event event(idx, t, node_release);
    event.m_nodes = nodes;
    return event;
  }

  DR_Event(const DR_Event &other) = default;
  DR_Event(DR_Event &&other) noexcept = default;
  DR_Event &operator=(const DR_Event &rhs) = default;
  DR_Event &operator=(DR_Event &&rhs) noexcept = default;

  /** @brief Return the affected Trace job identifier. @return job_no_t value.
   */
  job_no_t get_job_idx() const { return m_jidx; }
  /** @brief Return the event time. @return Simulation time in seconds. */
  sim_time_t get_time() const { return m_t; }
  /** @brief Return the event kind. @return DR_Event_Kind value. */
  DR_Event_Kind get_type() const { return m_type; }
  /** @brief Return the node count carried by a release event.
   * @return Nodes released; zero for start and end events. */
  num_nodes_t get_nodes() const { return m_nodes; }
  /** @brief Report whether this is a start event. @return true for
   * arrival/start. */
  bool is_arrival() const { return (m_type == arrival); }
  /** @brief Report whether this is an end event. @return true for
   * departure/end. */
  bool is_departure() const { return (m_type == departure); }
  /** @brief Report whether this is a delayed node-release event.
   * @return true when the event returns an ended job's nodes to service. */
  bool is_release() const { return (m_type == node_release); }

  friend bool operator<(const DR_Event &e1, const DR_Event &e2);
  friend bool operator==(const DR_Event &e1, const DR_Event &e2);
};

/** @brief Order events deterministically for the event queue.
 * @param[in] e1 Left event.
 * @param[in] e2 Right event.
 * @return `true` when @p e1 sorts before @p e2. */
inline bool operator<(const DR_Event &e1, const DR_Event &e2) {
  return LESS_OR(e1.m_t, e2.m_t,
                 LESS_OR(e1.m_type, e2.m_type, (e1.m_jidx < e2.m_jidx)));
}

/** @brief Compare all event identity and timing fields.
 * @param[in] e1 Left event.
 * @param[in] e2 Right event.
 * @return `true` when both events are equal. */
inline bool operator==(const DR_Event &e1, const DR_Event &e2) {
  return (e1.m_jidx == e2.m_jidx) && (e1.m_type == e2.m_type) &&
         (e1.m_t == e2.m_t);
}

/** @brief Write an event's textual representation.
 * @param[in,out] os Destination stream.
 * @param[in] evt Event to format.
 * @return The destination stream after writing. */
std::ostream &operator<<(std::ostream &os, const DR_Event &evt);

/// Ordered set used as the simulation event queue.
/// @details Each queue owns a fixed-size node pool (see
/// event_pool_allocator.hpp): tree nodes come from blocks of exactly the node
/// size instead of general-purpose malloc, whose size-class placement
/// measurably affects the event loop's cache behavior.
using event_q_t =
    std::set<DR_Event, std::less<DR_Event>, Event_Pool_Allocator<DR_Event>>;

/**@}*/
} // end of namespace dr_evt
#endif // DR_EVT_TRACE_DR_EVENT_HPP
