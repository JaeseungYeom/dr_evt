/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#include "utils/rngen.hpp"
#include <cstdlib>
#include <iostream>
#include <limits>

int main(int argc, char **argv) {
  using namespace std;
  if (argc > 2) {
    cerr << "usage: " << argv[0] << " [seed]" << endl;
    return 2;
  }

  const unsigned seed =
      argc == 2 ? static_cast<unsigned>(std::strtoul(argv[1], nullptr, 10))
                : 42u;
  bool ok = true;

  using rng1_t = dr_evt::RNGen<std::uniform_real_distribution>;
  rng1_t r1(seed);
  rng1_t r1_duplicate(seed);

  r1.param(typename rng1_t::param_type(0.0, 1.0));
  r1_duplicate.param(typename rng1_t::param_type(0.0, 1.0));
  for (int i = 0; i < 1000; ++i) {
    const double value = r1();
    ok = ok && value >= 0.0 && value < 1.0;
    ok = ok && value == r1_duplicate();
  }

  using rng2_t = dr_evt::RNGen<std::uniform_int_distribution, unsigned>;
  rng2_t r2(seed);
  rng2_t r2_duplicate(seed);

  constexpr unsigned unsigned_max = std::numeric_limits<unsigned>::max();
  r2.param(typename rng2_t::param_type(1, unsigned_max - 1));
  r2_duplicate.param(typename rng2_t::param_type(1, unsigned_max - 1));
  for (int i = 0; i < 1000; ++i) {
    const unsigned value = r2();
    ok = ok && value >= 1 && value <= unsigned_max - 1;
    ok = ok && value == r2_duplicate();
  }

  ok = ok && r2.distribution().min() == 1;
  ok = ok && r2.distribution().max() == unsigned_max - 1;

  if (!ok) {
    cerr << "RNGen deterministic-sequence or distribution-bound check failed"
         << endl;
    return 1;
  }
  cout << "RNGen deterministic-sequence and distribution-bound checks passed"
       << endl;
  return 0;
}
