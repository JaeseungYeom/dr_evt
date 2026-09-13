/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_UTILS_STATE_IO_IMPL_HPP
#define DR_EVT_UTILS_STATE_IO_IMPL_HPP

#include <cstring>

namespace dr_evt {
/** \addtogroup dr_evt_utils
 *  @{ */

template <typename S, typename T> S &operator<<(S &os, const bits_t<T &> &b) {
  using value_type = std::remove_cvref_t<T>;
  if constexpr (is_vector_v<T>) {
    const typename value_type::size_type sz = b.v.size();
    os.write(reinterpret_cast<const typename S::char_type *>(&sz), sizeof(sz));
    if (sz > 0ul) {
      os.write(reinterpret_cast<const typename S::char_type *>(b.v.data()),
               sz * sizeof(typename value_type::value_type));
    }
  } else {
    os.write(reinterpret_cast<const typename S::char_type *>(&b.v),
             sizeof(value_type));
  }
  return os;
}

template <typename S, typename T> S &operator>>(S &is, const bits_t<T &> &b) {
  using value_type = std::remove_cvref_t<T>;
  if constexpr (is_vector_v<T>) {
    typename value_type::size_type sz = 0ul;
    is.read(reinterpret_cast<typename S::char_type *>(&sz), sizeof(sz));
    if (!is) {
      return is;
    }
    b.v.resize(sz);
    if (sz > 0ul) {
      is.read(reinterpret_cast<typename S::char_type *>(b.v.data()),
              sz * sizeof(typename value_type::value_type));
    }
  } else {
    is.read(reinterpret_cast<typename S::char_type *>(&b.v),
            sizeof(value_type));
  }
  return is;
}

namespace detail {
template <raw_binary_serializable ObjT, binary_character CharT>
bool contains_raw_binary_state(std::span<const CharT> buffer) noexcept {
  using object_type = std::remove_cvref_t<ObjT>;
  if constexpr (is_vector_v<object_type>) {
    using size_type = typename object_type::size_type;
    using element_type = typename object_type::value_type;
    if (buffer.size_bytes() < sizeof(size_type)) {
      return false;
    }

    size_type count{};
    std::memcpy(&count, buffer.data(), sizeof(count));
    const auto available = buffer.size_bytes() - sizeof(count);
    return count <= available / sizeof(element_type);
  } else {
    return buffer.size_bytes() >= sizeof(object_type);
  }
}
} // namespace detail

template <raw_binary_serializable ObjT, binary_character CharT,
          typename Traits>
bool save_state(const ObjT &obj, std::vector<CharT> &buffer) {
  /* Resize or reserve vector space to avoid overhead of reallocation
       Especially when there are multiple items to pack and the sizes are
       known in advance. For ostreamvec, the vector is considered empty
       even if the size is larger than 0.
  if (buffer.size()*sizeof(CharT) < sizeof(obj)) {
      buffer.resize((sizeof(obj) + sizeof(CharT) - 1u)/sizeof(CharT));
  }
  */

  ostreamvec<CharT, Traits> ostrmbuf(buffer);
  // streamvec<CharT, Traits> ostrmbuf(buffer);
  std::basic_ostream<CharT, Traits> oss(&ostrmbuf);

  oss << bits(obj);

  return oss.good();
}

template <raw_binary_serializable ObjT, binary_character CharT,
          typename Traits>
bool load_state(ObjT &obj, const std::vector<CharT> &buffer) {
  if (!detail::contains_raw_binary_state<ObjT>(
          std::span<const CharT>{buffer.data(), buffer.size()})) {
    return false;
  }

  istreamvec<CharT, Traits> istrmbuf(buffer);
  std::basic_istream<CharT, Traits> iss(&istrmbuf);
  iss >> bits(obj);
  return iss.good();
}

template <raw_binary_serializable ObjT, binary_character CharT,
          typename Traits>
bool save_state(const ObjT &obj, std::span<CharT> buffer) {
  ostreambuff<CharT, Traits> ostrmbuf(buffer);
  std::basic_ostream<CharT, Traits> oss(&ostrmbuf);

  oss << bits(obj);
  return oss.good();
}

template <raw_binary_serializable ObjT, binary_character CharT,
          typename Traits>
bool load_state(ObjT &obj, std::span<const CharT> buffer) {
  if (!detail::contains_raw_binary_state<ObjT>(buffer)) {
    return false;
  }

  istreambuff<CharT, Traits> istrmbuf(buffer);
  std::basic_istream<CharT, Traits> iss(&istrmbuf);
  iss >> bits(obj);
  return iss.good();
}

template <raw_binary_scalar ObjT, binary_character CharT, typename Traits>
bool save_state(const ObjT &obj, CharT *buffer) {
  return save_state<ObjT, CharT, Traits>(
      obj, std::span<CharT>{buffer, sizeof(ObjT)});
}

template <raw_binary_scalar ObjT, binary_character CharT, typename Traits>
bool load_state(ObjT &obj, const CharT *buffer) {
  return load_state<ObjT, CharT, Traits>(
      obj, std::span<const CharT>{buffer, sizeof(ObjT)});
}

/**@}*/
} // end of namespace dr_evt

#endif // DR_EVT_UTILS_STATE_IO_IMPL_HPP
