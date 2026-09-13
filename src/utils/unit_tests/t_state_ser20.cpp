/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#if defined(DR_EVT_HAS_CONFIG)
#include "dr_evt_config.hpp"
#else
#error "no config"
#endif

#if defined(DR_EVT_HAS_SER20)
#include "utils/seed.hpp"
#include "utils/state_io_ser20.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <span>
#include <sstream>
#include <stdexcept>
#include <vector>

ENABLE_CUSTOM_SER20(std::minstd_rand);

namespace {
struct nested_state {
  int value = 0;

  template <class Archive> void serialize(Archive &archive) { archive(value); }
  bool operator==(const nested_state &) const = default;
};

struct simulation_state {
  std::uint64_t event = 0;
  double time = 0.0;
  char kind = '\0';
  nested_state nested;
  std::minstd_rand generator;

  template <class Archive> void serialize(Archive &archive) {
    archive(event, time, kind, nested, generator);
  }
  bool operator==(const simulation_state &) const = default;
};

simulation_state make_state() {
  simulation_state state{42, 3.25, 'e', {17}, {}};
  const auto seed_input = dr_evt::make_seed_seq_input(
      state.event, state.time, state.kind, state.nested.value);
  std::seed_seq sequence(seed_input.begin(), seed_input.end());
  state.generator.seed(sequence);
  static_cast<void>(state.generator());
  return state;
}

bool check(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAILED: " << message << '\n';
  }
  return condition;
}

bool use_stringstream(const simulation_state &original) {
  std::stringstream stream;
  dr_evt::save_state(original, stream);

  simulation_state restored;
  dr_evt::load_state(restored, stream);
  return check(restored == original, "stream API remains compatible");
}

bool use_streamvec(const simulation_state &original,
                   std::vector<char> &buffer) {
  buffer.reserve(512);
  const auto *const allocation = buffer.data();
  const auto written = dr_evt::serialize_binary(original, buffer);

  bool ok = check(written == buffer.size(),
                  "vector result has exact serialized size");
  ok &= check(allocation == buffer.data(),
              "reserved vector storage was reused");

  simulation_state restored;
  dr_evt::deserialize_binary(restored, buffer);
  ok &= check(restored == original, "vector-backed Ser20 round trip");
  return ok;
}

bool use_streambuff(const simulation_state &original,
                    const std::vector<char> &expected) {
  bool ok = true;
  std::array<char, 512> fixed_buffer{};
  const auto written =
      dr_evt::serialize_binary(original, std::span<char>{fixed_buffer});
  ok &= check(written == expected.size(),
              "fixed and vector buffers report the same byte count");
  ok &= check(std::equal(expected.begin(), expected.end(),
                         fixed_buffer.begin()),
              "fixed and vector buffers contain identical binary data");

  simulation_state restored;
  dr_evt::deserialize_binary(
      restored, std::span<const char>{fixed_buffer.data(), written});
  ok &= check(restored == original, "fixed-span Ser20 round trip");

  std::array<std::byte, 512> byte_buffer{};
  const auto byte_count =
      dr_evt::serialize_binary(original, std::span<std::byte>{byte_buffer});
  simulation_state restored_from_bytes;
  dr_evt::deserialize_binary(
      restored_from_bytes,
      std::span<const std::byte>{byte_buffer.data(), byte_count});
  ok &= check(byte_count == expected.size() &&
                  restored_from_bytes == original,
              "std::byte span round trip");

  std::array<char, 1> undersized{};
  bool rejected = false;
  try {
    static_cast<void>(
        dr_evt::serialize_binary(original, std::span<char>{undersized}));
  } catch (const std::length_error &) {
    rejected = true;
  }
  ok &= check(rejected, "undersized fixed buffer is rejected");
  return ok;
}
} // namespace

int main() {
  const auto original = make_state();
  std::vector<char> dynamic_buffer;
  bool ok = true;
  ok &= use_streamvec(original, dynamic_buffer);
  ok &= use_streambuff(original, dynamic_buffer);
  ok &= use_stringstream(original);

  return ok ? 0 : 1;
}
#endif // defined(DR_EVT_HAS_SER20)
