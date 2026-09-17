/******************************************************************************
 * SPDX-License-Identifier: MIT
 ******************************************************************************/

/** @file test_queue_input.cpp
 * @brief Parser coverage for queue schemas and supported trace formats.
 *
 * Built once in each compile-time schema.  Each binary covers simple and
 * Lassen input, replay and simulation records, and selected queue field
 * present/absent.  The unselected schema field is deliberately present to
 * prove it is ignored.
 */

#define DR_EVT_HAS_CONFIG 1
#include "trace/data_columns.hpp"
#include "trace/job_io.hpp"

#include <array>
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace dr_evt;

namespace {

void write_file(const std::string &path, const std::string &contents) {
  std::ofstream file(path);
  assert(file);
  file << contents;
}

template <class Container> std::string join(const Container &fields) {
  std::ostringstream out;
  bool first = true;
  for (const auto &field : fields) {
    if (!first)
      out << ',';
    out << field;
    first = false;
  }
  return out.str();
}

std::string simple_trace(bool replay, bool selected_queue_present) {
  std::vector<std::string> header{"job_submit_time"};
  std::vector<std::string> row{"0"};
  if (replay) {
    header.insert(header.end(), {"begin_time", "end_time"});
    row.insert(row.end(), {"1", "11"});
  }
  header.emplace_back("num_nodes");
  row.emplace_back("10");
#if DR_EVT_LEGACY_QUEUE_INPUT
  if (selected_queue_present) {
    header.emplace_back("queue");
    row.emplace_back("pbatch");
  }
  header.emplace_back("q_id");
  row.emplace_back("2");
#else
  header.emplace_back("queue");
  row.emplace_back("not-a-queue-name");
  if (selected_queue_present) {
    header.emplace_back("q_id");
    row.emplace_back("2");
  }
#endif
  header.emplace_back("time_limit");
  row.emplace_back("10");
  return join(header) + '\n' + join(row) + '\n';
}

std::string lassen_trace(bool replay, bool selected_queue_present) {
  // The positional fields match the documented Lassen layout.  A quoted
  // comma in user_script exercises its special forward/backward parsing.
  std::array<std::string, 33> header{};
  std::array<std::string, 33> row{};
  for (std::size_t i = 0; i < header.size(); ++i) {
    header[i] = "unused_" + std::to_string(i);
    row[i] = "x";
  }
  header[11] = "num_nodes";
  row[11] = "10";
  header[22] = "user_script";
  row[22] = "\"echo a,b\"";
  if (replay) {
    header[23] = "begin_time";
    row[23] = "1";
    header[24] = "end_time";
    row[24] = "11";
  }
  header[29] = "job_submit_time";
  row[29] = "0";
  header[32] = "time_limit";
  row[32] = "10";
#if DR_EVT_LEGACY_QUEUE_INPUT
  header[30] = selected_queue_present ? "queue" : "q_id";
  row[30] = selected_queue_present ? "pbatch" : "2";
#else
  header[30] = selected_queue_present ? "q_id" : "queue";
  row[30] = selected_queue_present ? "2" : "not-a-queue-name";
#endif
  return join(header) + '\n' + join(row) + '\n';
}

void parse_and_check(const std::string &format, bool replay,
                     bool selected_queue_present) {
  const std::string path =
      "/tmp/test_queue_input_" + format +
      (replay ? "_replay_" : "_simulation_") +
      (selected_queue_present ? "present.csv" : "absent.csv");
  write_file(path, format == "simple"
                       ? simple_trace(replay, selected_queue_present)
                       : lassen_trace(replay, selected_queue_present));

  Data_Columns columns(format, "epoch", "UTC");
  assert(columns.check_header(path));
  assert(columns.get_trace_mode() ==
         (replay ? TraceMode::REPLAY : TraceMode::SIMULATION));
#if DR_EVT_LEGACY_QUEUE_INPUT
  assert(columns.has_queue_column() == selected_queue_present);
#else
  assert(columns.has_q_id_column() == selected_queue_present);
#endif

  std::vector<Job_Record> records;
  assert(load(path, columns, records) == EXIT_SUCCESS);
  if (records.size() != 1u) {
    std::cerr << "parser dropped " << format << ' '
              << (replay ? "replay" : "simulation") << ' '
              << (selected_queue_present ? "with queue field"
                                         : "without queue field")
              << '\n';
  }
  assert(records.size() == 1u);
  assert(records.front().get_num_nodes() == 10u);
  assert(records.front().get_limit_time() == 10u);
#if DR_EVT_LEGACY_QUEUE_INPUT
  assert(records.front().get_queue() == Queue1);
#else
  assert(records.front().get_queue() ==
         (selected_queue_present ? Queue2 : Queue1));
  // Numeric queue IDs are site-neutral. In particular, q_id=2 must not
  // inherit the legacy pAll queue's exclusive/DAT semantics.
  if (_Is_Exclusive(records.front().get_queue())) {
    throw std::runtime_error(
        "numeric q_id was treated as a legacy exclusive queue");
  }
#endif
  if (replay) {
    assert(records.front().get_begin_time().first == 1);
    assert(records.front().get_end_time().first == 11);
  }
}

std::vector<Job_Record> load_simple(const std::string &name,
                                    const std::string &contents) {
  const std::string path = "/tmp/test_runtime_validation_" + name + ".csv";
  write_file(path, contents);
  Data_Columns columns("simple", "epoch", "UTC");
  assert(columns.check_header(path));
  std::vector<Job_Record> records;
  assert(load(path, columns, records) == EXIT_SUCCESS);
  return records;
}

void test_input_runtime_validation() {
  const auto replay =
      load_simple("replay",
                  "job_submit_time,begin_time,end_time,num_nodes,time_limit,"
                  "actual_run_time\n"
                  "0,0.1,0.3,1,1,0.2\n" // equal within timestamp precision
                  "0,1,3,1,2,2.1\n"     // greater than observed interval
                  "0,1,3,1,2,1.9\n");   // less than observed interval
  assert(replay.size() == 1u);
  assert(std::fabs(replay.front().get_actual_run_time() - 0.2) < 1.0e-12);

  const auto simulation =
      load_simple("simulation",
                  "job_submit_time,num_nodes,time_limit,actual_run_time\n"
                  "0,1,10,9\n"  // below the requested limit
                  "1,1,10,10\n" // equal to the requested limit
                  "2,1,10,11\n" // exceeds the requested limit
                  "3,1,10,nan\n");
  assert(simulation.size() == 2u);
  assert(simulation[0].get_actual_run_time() == 9.0);
  assert(simulation[1].get_actual_run_time() == 10.0);
}

} // namespace

int main() {
#if DR_EVT_LEGACY_QUEUE_INPUT
  if (!_Is_Exclusive(Queue2)) {
    throw std::runtime_error(
        "legacy pAll queue is not classified as exclusive");
  }
#endif
  for (const std::string format : {"simple", "lassen"}) {
    for (const bool replay : {false, true}) {
      parse_and_check(format, replay, true);
      parse_and_check(format, replay, false);
    }
  }
  test_input_runtime_validation();
  std::cout << "queue input parser tests passed\n";
  return EXIT_SUCCESS;
}
