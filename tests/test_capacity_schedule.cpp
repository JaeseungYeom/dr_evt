/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#define DR_EVT_HAS_CONFIG 1
#include "sim/capacity_schedule.hpp"
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace dr_evt;

namespace {

void write_file(const std::filesystem::path &path, const std::string &text) {
  std::ofstream output(path);
  output << text;
  assert(output.good());
}

void expect_invalid(const std::filesystem::path &path,
                    const std::string &contents) {
  write_file(path, contents);
  bool threw = false;
  try {
    (void)load_capacity_schedule(path.string(), 100);
  } catch (const std::invalid_argument &) {
    threw = true;
  }
  assert(threw);
}

} // namespace

int main() {
  const auto suffix =
      std::chrono::steady_clock::now().time_since_epoch().count();
  const auto work = std::filesystem::temp_directory_path() /
                    ("dr_evt_capacity_schedule_" + std::to_string(suffix));
  std::filesystem::create_directory(work);

  try {
    assert(load_capacity_schedule("", 100).empty());

    const auto valid = work / "valid.csv";
    write_file(valid, "ignored,total_nodes,time\nx,0,1\ny,100,2.5\n");
    const auto changes = load_capacity_schedule(valid.string(), 100);
    assert(changes.size() == 2);
    assert(changes[0].time == 1.0 && changes[0].total_nodes == 0);
    assert(changes[1].time == 2.5 && changes[1].total_nodes == 100);

    expect_invalid(work / "empty.csv", "");
    expect_invalid(work / "header_only.csv", "time,total_nodes\n");
    expect_invalid(work / "missing_time.csv", "total_nodes\n10\n");
    expect_invalid(work / "missing_nodes.csv", "time\n1\n");
    expect_invalid(work / "short_row.csv", "time,x,total_nodes\n1,x\n");
    expect_invalid(work / "duplicate.csv", "time,total_nodes\n1,10\n1,20\n");
    expect_invalid(work / "decreasing.csv", "time,total_nodes\n2,10\n1,20\n");
    expect_invalid(work / "malformed.csv", "time,total_nodes\n1,nope\n");
    expect_invalid(work / "negative.csv", "time,total_nodes\n1,-1\n");
    expect_invalid(work / "above_max.csv", "time,total_nodes\n1,101\n");
  } catch (...) {
    std::filesystem::remove_all(work);
    throw;
  }

  std::filesystem::remove_all(work);
  std::cout << "Capacity schedule parser tests passed\n";
  return EXIT_SUCCESS;
}
