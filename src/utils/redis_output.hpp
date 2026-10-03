/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#ifndef DR_EVT_UTILS_REDIS_OUTPUT_HPP
#define DR_EVT_UTILS_REDIS_OUTPUT_HPP

#include "dr_evt_types.hpp"

#include <sw/redis++/redis++.h>

#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace dr_evt {

/** Typed data for one finalized job at the Redis serialization boundary. */
struct RedisJobRecord {
  job_no_t job_id;                  ///< Permanent simulation job ID.
  sim_time_t submit_time;           ///< Submission time in seconds.
  sim_time_t start_time;            ///< Scheduled start time in seconds.
  sim_time_t completion_time;       ///< Completion time in seconds.
  num_nodes_t num_nodes;            ///< Requested node count.
  int exit_status;                  ///< Simulated completion status.
  std::optional<job_queue_t> queue; ///< Queue when present in the schema.
  timeout_t time_limit;             ///< Requested runtime limit in seconds.
};

/** Optional policy-specific values attached to a resource sample. */
struct RedisPconValues {
  double average; ///< Aggregate average-Pcon value.
  double minimum; ///< Aggregate minimum-Pcon value.
  double maximum; ///< Aggregate maximum-Pcon value.
};

/** Typed data for one resource-history sample. */
struct RedisResourceRecord {
  sim_time_t time;                     ///< Sample time in seconds.
  num_nodes_t free_nodes;              ///< Available nodes at @p time.
  num_nodes_t allocated_nodes;         ///< Allocated nodes at @p time.
  std::optional<RedisPconValues> pcon; ///< Pcon values when enabled.
};

/** Redis output position captured only when a checkpoint is requested. */
struct RedisCheckpointBoundary {
  std::uint64_t job_csv_bytes = 0;      ///< Committed job CSV byte count.
  std::uint64_t resource_csv_bytes = 0; ///< Committed resource CSV byte count.
  std::uint64_t resource_count = 0;     ///< Committed resource sample count.
  std::vector<std::string> job_ids;     ///< Committed job hashes/index members.
  bool resource_trace_active = false;   ///< Whether resource output exists.
};

/** Writes simulated-job and resource-history output to Redis. */
class RedisOutput {
public:
  /**
   * Construct a Redis output sink and clear output owned by the same prefix.
   * @param[in] uri Redis++ connection URI.
   * @param[in] key_prefix Namespace prefix for every key written by this sink.
   * @param[in] csv_header Header for the compatibility job CSV value.
   * @param[in] timestamps_in_msec Whether job times retain millisecond
   * precision instead of being truncated to whole seconds.
   */
  RedisOutput(const std::string &uri, const std::string &key_prefix,
              const std::string &csv_header, bool timestamps_in_msec);
  ~RedisOutput();

  RedisOutput(const RedisOutput &) = delete;
  RedisOutput &operator=(const RedisOutput &) = delete;
  RedisOutput(RedisOutput &&) noexcept;
  RedisOutput &operator=(RedisOutput &&) noexcept;

  /**
   * Consume an existing ordered job range in one Redis transaction.
   * The projection returns a record for jobs that belong in the output and
   * std::nullopt for records that should be skipped.
   * @tparam Iterator Const iterator over the owning job-record container.
   * @tparam Projection Callable returning `std::optional<RedisJobRecord>` from
   * a permanent job ID and the corresponding dereferenced iterator.
   * @param[in] first First job in the range.
   * @param[in] last One-past-the-last job in the range.
   * @param[in] first_job_id Permanent ID corresponding to @p first.
   * @param[in] project Converts one referenced job into Redis output without
   * retaining the source record.
   */
  template <typename Iterator, typename Projection>
  void flush_job_range(Iterator first, Iterator last, job_no_t first_job_id,
                       Projection project) {
    if (first == last) {
      return;
    }
    auto transaction = redis().transaction(false);
    std::string csv_rows;
    bool has_output = false;
    job_no_t job_id = first_job_id;
    for (; first != last; ++first, ++job_id) {
      auto record = project(job_id, *first);
      if (record) {
        queue_job_output(transaction, *record, csv_rows);
        has_output = true;
      }
    }
    if (has_output) {
      transaction.append(key_prefix() + ":csv", csv_rows).exec();
    }
  }

  /**
   * Initialize resource-history output for this namespace.
   * @param[in] csv_header Header for the compatibility resource CSV value.
   * @param[in] timestamps_in_msec Whether resource times retain millisecond
   * precision instead of being truncated to whole seconds.
   */
  void start_resource_trace(const std::string &csv_header,
                            bool timestamps_in_msec);

  /**
   * Consume an existing resource-sample range in one Redis transaction.
   * @tparam Iterator Const iterator over the owning resource-history buffer.
   * @tparam Projection Callable converting a dereferenced iterator into a
   * `RedisResourceRecord` without retaining the source sample.
   * @param[in] first First resource sample in the range.
   * @param[in] last One-past-the-last resource sample in the range.
   * @param[in] project Converts one referenced sample into Redis output.
   * @throws std::logic_error If resource output was not initialized.
   */
  template <typename Iterator, typename Projection>
  void append_resource_trace(Iterator first, Iterator last,
                             Projection project) {
    if (!resource_trace_active()) {
      throw std::logic_error("Redis resource trace has not been initialized");
    }
    if (first == last) {
      return;
    }
    auto transaction = redis().transaction(false);
    std::string csv_rows;
    for (; first != last; ++first) {
      queue_resource_output(transaction, project(*first), csv_rows);
    }
    transaction.append(key_prefix() + ":resources:csv", csv_rows).exec();
  }

  /**
   * Test whether resource-history output has been initialized.
   * @return `true` after start_resource_trace() has initialized the sink.
   */
  bool resource_trace_active() const;

  /** @return Redis namespace prefix owned by this output sink. */
  const std::string &key_prefix() const;

  /**
   * @brief Capture the committed Redis output boundary.
   * @return CSV sizes, resource count, and job IDs at checkpoint time.
   */
  RedisCheckpointBoundary checkpoint_boundary();

  /**
   * @brief Archive the canonical namespace and record its saved boundary.
   * @param[in] uri Redis++ connection URI.
   * @param[in] key_prefix Canonical namespace to archive.
   * @param[in] boundary Boundary captured in the loaded checkpoint.
   * @return Number assigned to the new pre-restart generation.
   */
  static std::uint64_t
  archive_checkpoint_namespace(const std::string &uri,
                               const std::string &key_prefix,
                               const RedisCheckpointBoundary &boundary);

private:
  /** @return Mutable Redis++ connection used to construct transactions. */
  sw::redis::Redis &redis();

  /**
   * Queue one job's commands and compatibility CSV row.
   * @param[in,out] transaction Transaction receiving Redis commands.
   * @param[in] job Typed finalized-job values to serialize.
   * @param[in,out] csv_rows Compatibility CSV batch under construction.
   */
  void queue_job_output(sw::redis::Transaction &transaction,
                        const RedisJobRecord &job, std::string &csv_rows);

  /**
   * Queue one resource sample's commands and compatibility CSV row.
   * @param[in,out] transaction Transaction receiving Redis commands.
   * @param[in] sample Typed resource values to serialize.
   * @param[in,out] csv_rows Compatibility CSV batch under construction.
   */
  void queue_resource_output(sw::redis::Transaction &transaction,
                             const RedisResourceRecord &sample,
                             std::string &csv_rows);

  class Impl;
  std::unique_ptr<Impl> m_impl;
};

} // namespace dr_evt

#endif // DR_EVT_UTILS_REDIS_OUTPUT_HPP
