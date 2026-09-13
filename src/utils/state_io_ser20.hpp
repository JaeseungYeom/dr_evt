/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_UTILS_STATE_IO_SER20_HPP
#define DR_EVT_UTILS_STATE_IO_SER20_HPP

#if defined(DR_EVT_HAS_CONFIG)
#include "dr_evt_config.hpp"
#else
#error "no config"
#endif

#if defined(DR_EVT_HAS_SER20)
#include "streambuff.hpp"
#include "streamvec.hpp"
#include "traits.hpp"
#include <ser20/archives/binary.hpp>
#include <algorithm>
#include <cstddef>
#include <cstring>
#include <istream>
#include <iostream>
#include <limits>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace dr_evt {
/** \addtogroup dr_evt_utils
 *  @{ */

/**
 * @brief Test whether a type may use DR_EVT's raw-byte Ser20 adapter.
 * @tparam T Candidate archive value type.
 * @return bool compile-time value; true only for non-arithmetic, trivially
 * copyable objects whose in-memory representation can be archived directly.
 * @details The resulting archive is representation-dependent and should only
 * be exchanged between ABI-compatible builds.
 */
template <typename T>
concept custom_binary_ser20_serializable =
    !std::is_arithmetic_v<std::remove_cvref_t<T>> &&
    std::is_trivially_copyable_v<std::remove_cvref_t<T>>;

template <typename T> constexpr bool is_custom_bin_ser20_serializable() {
  return custom_binary_ser20_serializable<T>;
}
} // end of namespace dr_evt

/**
 * @brief Register raw-binary Ser20 save/load functions for one type.
 * @param[in] T Trivially copyable non-arithmetic type to register.
 * @details The generated functions preserve the exact object representation;
 * they do not provide endian, compiler, or library-version portability.
 */
#define ENABLE_CUSTOM_SER20(T)                                                \
  namespace ser20 {                                                           \
  inline void SER20_SAVE_FUNCTION_NAME(BinaryOutputArchive &ar, T const &t) { \
    static_assert(dr_evt::custom_binary_ser20_serializable<T>);               \
    const auto bytes = std::as_bytes(std::span{std::addressof(t), 1u});        \
    ar.saveBinary(bytes.data(), static_cast<std::streamsize>(bytes.size()));   \
  }                                                                           \
  inline void SER20_LOAD_FUNCTION_NAME(BinaryInputArchive &ar, T &t) {        \
    static_assert(dr_evt::custom_binary_ser20_serializable<T>);               \
    auto bytes = std::as_writable_bytes(                                      \
        std::span{std::addressof(t), 1u});                                    \
    ar.loadBinary(bytes.data(), static_cast<std::streamsize>(bytes.size()));   \
  }                                                                           \
  }

namespace dr_evt {

/** @brief Save one Ser20-serializable object to a binary archive.
 * @tparam T Archived object type.
 * @param[in] state Object whose complete state is written.
 * @param[in,out] os Destination stream.
 * @details Archive destruction flushes buffered Ser20 output before return. */
template <typename T> void save_state(const T &state, std::ostream &os) {
  // Create an output archive with the given stream
  ser20::BinaryOutputArchive oarchive(os);

  oarchive(state); // Write the data to the archive
                   // archive goes out of scope,
                   // ensuring all contents are flushed to the stream
}

/** @brief Restore one Ser20-serializable object from a binary archive.
 * @tparam T Archived object type.
 * @param[out] state Object populated from the archive.
 * @param[in,out] is Source stream positioned at an archive boundary. */
template <typename T> void load_state(T &state, std::istream &is) {
  // Create an input archive using the given stream
  ser20::BinaryInputArchive iarchive(is);

  iarchive(state); // Read the data from the archive
}

namespace detail {
/** A non-owning output buffer that records overflow without short writes.
 * Ser20 can therefore finish unwinding its internal buffer safely; the public
 * span helper reports insufficient capacity after archive destruction. */
class bounded_binary_output_buffer final : public std::streambuf {
public:
  explicit bounded_binary_output_buffer(std::span<char> storage) noexcept
      : storage_(storage) {}

  [[nodiscard]] size_t size() const noexcept { return size_; }
  [[nodiscard]] bool overflowed() const noexcept {
    return size_ > storage_.size();
  }

protected:
  std::streamsize xsputn(const char *source,
                         std::streamsize count) override {
    if (count <= 0) {
      return 0;
    }

    const auto requested = static_cast<size_t>(count);
    const auto offset = std::min(size_, storage_.size());
    const auto writable = std::min(requested, storage_.size() - offset);
    if (writable != 0) {
      std::memcpy(storage_.data() + offset, source, writable);
    }

    if (requested > std::numeric_limits<size_t>::max() - size_) {
      size_ = std::numeric_limits<size_t>::max();
    } else {
      size_ += requested;
    }
    return count;
  }

  int_type overflow(int_type value = traits_type::eof()) override {
    if (traits_type::eq_int_type(value, traits_type::eof())) {
      return traits_type::not_eof(value);
    }
    const char character = traits_type::to_char_type(value);
    static_cast<void>(xsputn(std::addressof(character), 1));
    return value;
  }

private:
  std::span<char> storage_;
  size_t size_ = 0;
};
} // namespace detail

/** @brief Serialize directly into a caller-owned vector.
 * @return Number of serialized bytes. Existing allocation is reused when its
 * capacity is sufficient; the vector is resized to the exact result. */
template <typename T>
[[nodiscard]] size_t serialize_binary(const T &state,
                                      std::vector<char> &buffer) {
  ostreamvec<char> stream_buffer(buffer);
  std::ostream stream(&stream_buffer);
  {
    ser20::BinaryOutputArchive archive(stream);
    archive(state);
  }
  return stream_buffer.size();
}

/** @brief Serialize directly into fixed caller-owned storage.
 * @return Number of serialized bytes.
 * @throws std::length_error if @p buffer is too small. */
template <typename T, binary_character CharT>
[[nodiscard]] size_t serialize_binary(const T &state,
                                      std::span<CharT> buffer) {
  auto bytes = std::span<char>{reinterpret_cast<char *>(buffer.data()),
                               buffer.size_bytes()};
  detail::bounded_binary_output_buffer stream_buffer(bytes);
  std::ostream stream(&stream_buffer);
  {
    ser20::BinaryOutputArchive archive(stream);
    archive(state);
  }

  if (stream_buffer.overflowed()) {
    throw std::length_error("Ser20 output requires " +
                            std::to_string(stream_buffer.size()) +
                            " bytes; fixed buffer has " +
                            std::to_string(buffer.size()));
  }
  return stream_buffer.size();
}

/** @brief Deserialize one object directly from caller-owned storage. */
template <typename T, binary_character CharT>
void deserialize_binary(T &state, std::span<const CharT> buffer) {
  const auto bytes =
      std::span<const char>{reinterpret_cast<const char *>(buffer.data()),
                            buffer.size_bytes()};
  istreambuff<char> stream_buffer(bytes);
  std::istream stream(&stream_buffer);
  ser20::BinaryInputArchive archive(stream);
  archive(state);
}

/** @brief Deserialize one object directly from a caller-owned vector. */
template <typename T>
void deserialize_binary(T &state, const std::vector<char> &buffer) {
  deserialize_binary(state,
                     std::span<const char>{buffer.data(), buffer.size()});
}

/**@}*/
} // end of namespace dr_evt
#endif // DR_EVT_HAS_SER20

#endif // DR_EVT_UTILS_STATE_IO_SER20_HPP
