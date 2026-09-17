/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_UTILS_STATE_IO_HPP
#define DR_EVT_UTILS_STATE_IO_HPP
#include "streambuff.hpp"
#include "streamvec.hpp"
#include "traits.hpp"
#include <istream>
#include <ostream>
#include <span>
#include <type_traits>
#include <vector>

namespace dr_evt {
/** \addtogroup dr_evt_utils
 *  @{ */

/**
 * Override the stream operators only for trivially copyable types.
 * Users call `bits()` on the object of such a type to use the overriden
 * interfaces. Note that the function is only defined for trivially
 * copyable ones. Calling on an object of a wrong type would generate a
 * compiler error.
 * https://stackoverflow.com/questions/1559254/are-there-binary-memory-streams-in-c
 */
template <typename T> struct bits_t {
  using value_type = std::remove_cvref_t<T>; ///< Unqualified payload type.
  T v; ///< Referenced object whose bytes are streamed.
};

/** @brief Wrap scalar or contiguous vector storage for byte-wise stream I/O.
 * @tparam T Trivially copyable scalar or vector of trivially copyable values.
 * @param[in,out] v Object whose representation will be transferred.
 * @return bits_t<T&> retaining a reference to @p v. */
template <raw_binary_serializable T> bits_t<T &> bits(T &v) { return {v}; }

/** @brief Write a wrapped object's binary representation.
 * @tparam S Output stream type. @tparam T Wrapped payload type.
 * @param[in,out] os Destination stream. @param[in] b Wrapped object.
 * @return S& referring to @p os. */
template <typename S, typename T> S &operator<<(S &os, const bits_t<T &> &b);

/** @brief Read a binary representation into a wrapped object.
 * @tparam S Input stream type. @tparam T Wrapped payload type.
 * @param[in,out] is Source stream. @param[out] b Wrapper referencing the
 * destination.
 * @return S& referring to @p is. */
template <typename S, typename T> S &operator>>(S &is, const bits_t<T &> &b);

template <raw_binary_serializable ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Serialize an object into a caller-owned byte vector.
 * @tparam ObjT Trivially copyable scalar or supported vector type.
 * @tparam CharT One-byte buffer element type.
 * @tparam Traits Character traits used by the memory stream.
 * @param[in] obj Object to snapshot. @param[out] buffer Resized serialized
 * bytes.
 * @return bool indicating stream success. */
bool save_state(const ObjT &obj, std::vector<CharT> &buffer);

template <raw_binary_serializable ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Restore an object from a byte vector.
 * @tparam ObjT Trivially copyable scalar or supported vector type.
 * @tparam CharT One-byte buffer element type.
 * @tparam Traits Character traits used by the memory stream.
 * @param[out] obj Object receiving restored state. @param[in] buffer Snapshot
 * bytes.
 * @return bool indicating stream success. */
bool load_state(ObjT &obj, const std::vector<CharT> &buffer);

template <raw_binary_serializable ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Serialize an object into fixed caller-owned storage.
 * @param[in] obj Object to snapshot. @param[out] buffer Destination storage.
 * @return bool indicating that the entire representation fit. */
bool save_state(const ObjT &obj, std::span<CharT> buffer);

template <raw_binary_serializable ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Restore an object from fixed caller-owned storage.
 * @param[out] obj Object receiving restored state. @param[in] buffer Source
 * storage.
 * @return bool indicating that a complete representation was available. */
bool load_state(ObjT &obj, std::span<const CharT> buffer);

template <raw_binary_scalar ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Serialize an object into a preallocated byte buffer.
 * @param[in] obj Object to snapshot. @param[out] buffer Storage of at least
 * sizeof(ObjT) bytes.
 * @return bool indicating stream success. */
bool save_state(const ObjT &obj, CharT *buffer);

template <raw_binary_scalar ObjT, binary_character CharT = char,
          typename Traits = std::char_traits<CharT>>
/** @brief Restore an object from a preallocated byte buffer.
 * @param[out] obj Object receiving restored state. @param[in] buffer Complete
 * snapshot storage.
 * @return bool indicating stream success. */
bool load_state(ObjT &obj, const CharT *buffer);

/**@}*/
} // end of namespace dr_evt

#include "state_io_impl.hpp"

#endif // DR_EVT_UTILS_STATE_IO_HPP
