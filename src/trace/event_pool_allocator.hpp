/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

/** @file event_pool_allocator.hpp
 * @brief Per-owner fixed-size node pool for the simulation event queue.
 * @details The event queue is a red-black tree whose nodes are allocated and
 * freed once per start, end, and release event. General-purpose malloc places
 * the 56-byte node in a 64-byte size class, so consecutive nodes start at the
 * same offset within a cache line and each lookup touches two lines; measured
 * at about 1.5 percent of simulation wall time. A pool with the exact node
 * stride avoids that and removes malloc from the event loop. The pool is
 * owned by the allocator's state object, not shared globally, so trace
 * instances on different threads never contend or race.
 */

#ifndef DR_EVT_TRACE_EVENT_POOL_ALLOCATOR_HPP
#define DR_EVT_TRACE_EVENT_POOL_ALLOCATOR_HPP

#include <boost/pool/pool.hpp>
#include <cstddef>
#include <memory>
#include <new>

namespace dr_evt {
/** \addtogroup dr_evt_trace
 *  @{ */

/**
 * @brief One fixed-size pool, sized by the first single-object request.
 * @details A container rebinds its allocator to exactly one node type, so one
 * block size serves every single-object request. Requests of another size
 * (none occur in practice) fall back to the global heap, decided by size
 * alone so allocation and deallocation always agree.
 */
class Event_Pool_State {
public:
  /** @brief Allocate one block of @p size bytes. */
  void *allocate(std::size_t size) {
    if (!m_pool) {
      m_pool = std::make_unique<boost::pool<>>(size);
    }
    if (m_pool->get_requested_size() == size) {
      void *block = m_pool->malloc();
      if (block == nullptr) {
        throw std::bad_alloc();
      }
      return block;
    }
    return ::operator new(size);
  }

  /** @brief Return a block obtained from allocate() with the same size. */
  void deallocate(void *block, std::size_t size) noexcept {
    if (m_pool && m_pool->get_requested_size() == size) {
      m_pool->free(block);
    } else {
      ::operator delete(block);
    }
  }

private:
  std::unique_ptr<boost::pool<>> m_pool; ///< Created on first use.
};

/**
 * @brief Standard allocator that draws single objects from an Event_Pool_State.
 * @details Stateful: copies and rebound copies share one state, so a container
 * and the node allocator it rebinds to use the same pool. Two allocators
 * compare equal only when they share a state. Multi-object requests use the
 * global heap.
 * @tparam T Value type; containers rebind it to their node type.
 */
template <typename T> class Event_Pool_Allocator {
public:
  using value_type = T;

  /** @brief Create an allocator with its own pool. */
  Event_Pool_Allocator() : m_state(std::make_shared<Event_Pool_State>()) {}

  /** @brief Share the pool of an allocator for another type (rebind). */
  template <typename U>
  Event_Pool_Allocator(const Event_Pool_Allocator<U> &other) noexcept
      : m_state(other.state()) {}

  /** @brief Allocate storage for @p n objects. */
  T *allocate(std::size_t n) {
    if (n == 1) {
      return static_cast<T *>(m_state->allocate(sizeof(T)));
    }
    return static_cast<T *>(::operator new(n * sizeof(T)));
  }

  /** @brief Release storage obtained from allocate() with the same @p n. */
  void deallocate(T *p, std::size_t n) noexcept {
    if (n == 1) {
      m_state->deallocate(p, sizeof(T));
    } else {
      ::operator delete(p);
    }
  }

  /** @brief Expose the shared state for rebinding. */
  const std::shared_ptr<Event_Pool_State> &state() const noexcept {
    return m_state;
  }

  template <typename U>
  bool operator==(const Event_Pool_Allocator<U> &other) const noexcept {
    return m_state == other.state();
  }
  template <typename U>
  bool operator!=(const Event_Pool_Allocator<U> &other) const noexcept {
    return m_state != other.state();
  }

private:
  std::shared_ptr<Event_Pool_State> m_state; ///< Pool shared with rebinds.
};

/**@}*/
} // end of namespace dr_evt

#endif // DR_EVT_TRACE_EVENT_POOL_ALLOCATOR_HPP
