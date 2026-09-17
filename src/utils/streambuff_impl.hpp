/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_UTILS_STREAMBUFF_IMPL_HPP
#define DR_EVT_UTILS_STREAMBUFF_IMPL_HPP

#include <iomanip>
#include <iostream>
#include <sstream>

namespace dr_evt {
/** \addtogroup dr_evt_utils
 *  @{ */

//---------------------------- ostreambuff --------------------------------

template <binary_character CharT, typename Traits>
ostreambuff<CharT, Traits>::ostreambuff(CharT *data, size_t max_size) noexcept
    : buf(data), m_capacity(max_size) {
  this->setp(data, max_size == 0 ? data : data + max_size);
}

template <binary_character CharT, typename Traits>
ostreambuff<CharT, Traits>::ostreambuff(std::span<CharT> storage) noexcept
    : ostreambuff(storage.data(), storage.size()) {}

template <binary_character CharT, typename Traits>
size_t ostreambuff<CharT, Traits>::size() const noexcept {
  if (this->pbase() == nullptr) {
    return 0;
  }
  return static_cast<size_t>(this->pptr() - this->pbase());
}

template <binary_character CharT, typename Traits>
size_t ostreambuff<CharT, Traits>::capacity() const noexcept {
  return m_capacity;
}

template <binary_character CharT, typename Traits>
std::ostream &ostreambuff<CharT, Traits>::print(std::ostream &os,
                                                bool show_content) const {
  const auto sz = size();
  std::stringstream ss;
  using std::operator<<;

  ss << std::hex << " pbase (0x"
     << reinterpret_cast<unsigned long long>(this->pbase()) << ") pptr (0x"
     << reinterpret_cast<unsigned long long>(this->pptr()) << ") epptr (0x"
     << reinterpret_cast<unsigned long long>(this->epptr()) << ")";

  if (show_content) {
    ss << std::endl << "buf: [";
    for (unsigned i = 0u; i < sz; ++i) {
      ss << ' '
         << static_cast<unsigned int>(static_cast<unsigned char>(buf[i]));
    }
    ss << " ]";
  }

  os << "size(" << sz << ") " << ss.str() << std::endl;
  return os;
}

template <binary_character CharT, typename Traits>
void ostreambuff<CharT, Traits>::shrink_to_fit() {
  m_capacity = size();

  auto const end = m_capacity == 0 ? buf : buf + m_capacity;
  this->setp(buf, end);    // set pbase and epptr
  this->pbump(m_capacity); // set pptr
}

//---------------------------- istreambuff --------------------------------
template <binary_character CharT, typename Traits>
istreambuff<CharT, Traits>::istreambuff(const CharT *data, size_t sz) noexcept
    : buf(data), m_size(sz) {
  auto const p = const_cast<CharT *>(data);
  this->setg(p, p, sz == 0 ? p : p + sz);
}

template <binary_character CharT, typename Traits>
istreambuff<CharT, Traits>::istreambuff(std::span<const CharT> storage) noexcept
    : istreambuff(storage.data(), storage.size()) {}

template <binary_character CharT, typename Traits>
size_t istreambuff<CharT, Traits>::size() const noexcept {
  return m_size;
}

template <binary_character CharT, typename Traits>
std::ostream &istreambuff<CharT, Traits>::print(std::ostream &os,
                                                bool show_content) const {
  const auto sz = size();
  std::stringstream ss;
  using std::operator<<;

  ss << std::hex << " eback (0x"
     << reinterpret_cast<unsigned long long>(this->eback()) << ") gptr (0x"
     << reinterpret_cast<unsigned long long>(this->gptr()) << ") egptr (0x"
     << reinterpret_cast<unsigned long long>(this->egptr()) << ")";

  if (show_content) {
    ss << std::endl << "buf: [";
    for (unsigned i = 0u; i < sz; ++i) {
      ss << ' '
         << static_cast<unsigned int>(static_cast<unsigned char>(buf[i]));
    }
    ss << " ]";
  }

  os << "size(" << sz << ") " << ss.str() << std::endl;
  return os;
}

//---------------------------- streambuff --------------------------------

template <binary_character CharT, typename Traits>
streambuff<CharT, Traits>::streambuff(CharT *data, size_t max_size,
                                      size_t cur_size) noexcept
    : buf(data), m_capacity(max_size) {
  auto const csz = std::min(max_size, cur_size);
  auto const d_end = max_size == 0 ? data : data + max_size;
  auto const d_cur = csz == 0 ? data : data + csz;

  this->setg(data, data, d_cur); // set eback, gptr, and egptr
  this->setp(data, d_end);       // set pbase and epptr
  this->pbump(csz);              // set pptr
}

template <binary_character CharT, typename Traits>
streambuff<CharT, Traits>::streambuff(std::span<CharT> storage,
                                      size_t current_size) noexcept
    : streambuff(storage.data(), storage.size(), current_size) {}

template <binary_character CharT, typename Traits>
size_t streambuff<CharT, Traits>::size() const noexcept {
  if (this->pbase() == nullptr) {
    return 0;
  }
  return static_cast<size_t>(this->pptr() - this->pbase());
}

template <binary_character CharT, typename Traits>
size_t streambuff<CharT, Traits>::capacity() const noexcept {
  return m_capacity;
}

template <binary_character CharT, typename Traits>
std::ostream &streambuff<CharT, Traits>::print(std::ostream &os,
                                               bool show_content) const {
  const auto sz = size();
  std::stringstream ss;
  using std::operator<<;

  ss << std::hex << " pbase (0x"
     << reinterpret_cast<unsigned long long>(this->pbase()) << ") pptr (0x"
     << reinterpret_cast<unsigned long long>(this->pptr()) << ") epptr (0x"
     << reinterpret_cast<unsigned long long>(this->epptr()) << ")";

  ss << std::hex << " eback (0x"
     << reinterpret_cast<unsigned long long>(this->eback()) << ") gptr (0x"
     << reinterpret_cast<unsigned long long>(this->gptr()) << ") egptr (0x"
     << reinterpret_cast<unsigned long long>(this->egptr()) << ")";

  if (show_content) {
    ss << std::endl << "buf: [";
    for (unsigned i = 0u; i < sz; ++i) {
      ss << ' '
         << static_cast<unsigned int>(static_cast<unsigned char>(buf[i]));
    }
    ss << " ]";
  }

  os << "size(" << sz << ") " << ss.str() << std::endl;
  return os;
}

template <binary_character CharT, typename Traits>
std::streamsize
streambuff<CharT, Traits>::xsputn(const streambuff<CharT, Traits>::char_type *s,
                                  std::streamsize count) {
  if (count <= 0) {
    return 0;
  }
  if (static_cast<size_t>(count) + size() > capacity()) {
    return static_cast<std::streamsize>(0);
  }
  this->setg(this->eback(), this->gptr(), this->pptr() + count);
  return std::basic_streambuf<CharT, Traits>::xsputn(s, count);
}

/*
template<typename CharT, typename Traits>
std::streamsize streamvec<CharT, Traits>::xsgetn(
    streambuff<CharT, Traits>::char_type* s,
    std::streamsize count)
{
    this->setg(this->eback(), this->gptr(), this->pptr());
    print();
    return std::basic_streambuf<CharT, Traits>::xsgetn(s, count);
}
*/

template <binary_character CharT, typename Traits>
void streambuff<CharT, Traits>::shrink_to_fit() {
  const auto sz = size();
  const auto sz_read = this->eback() == nullptr
                           ? 0
                           : static_cast<size_t>(this->gptr() - this->eback());

  auto const new_end = sz == 0 ? buf : buf + sz;
  const auto read_offset = std::min(sz, sz_read);
  auto const new_read = read_offset == 0 ? buf : buf + read_offset;

  this->setg(buf, new_read, new_end); // set eback, gptr, and egptr
  this->setp(buf, new_end);           // set pbase and epptr
  this->pbump(sz);                    // set pptr
}

/**@}*/
} // namespace dr_evt
#endif // DR_EVT_UTILS_STREAMBUFF_IMPL_HPP
