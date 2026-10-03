/******************************************************************************
 *         Copyright 2023 Lawrence Livermore National Security, LLC           *
 *         See the top-level LICENSE file for details.                        *
 *                                                                            *
 *         SPDX-License-Identifier: MIT                                       *
 ******************************************************************************/

#include "utils/redis_output.hpp"

#include "trace/epoch.hpp"
#include "trace/parse_utils.hpp"

#include <algorithm>
#include <cmath>
#include <iterator>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <sw/redis++/redis++.h>

namespace dr_evt {

class RedisOutput::Impl {
public:
  Impl(const std::string &uri, std::string key_prefix,
       const std::string &csv_header, bool timestamps_in_msec)
      : redis(uri), prefix(std::move(key_prefix)),
        job_timestamps_in_msec(timestamps_in_msec) {
    if (prefix.empty()) {
      throw std::invalid_argument("Redis output key prefix cannot be empty");
    }

    std::vector<std::string> old_job_ids;
    redis.smembers(prefix + ":job_ids", std::back_inserter(old_job_ids));
    std::vector<std::string> old_resource_ids;
    redis.zrange(prefix + ":resources:by_time", 0, -1,
                 std::back_inserter(old_resource_ids));

    auto transaction = redis.transaction(false);
    for (const auto &job_id : old_job_ids) {
      transaction.del(prefix + ":job:" + job_id);
    }
    for (const auto &resource_id : old_resource_ids) {
      transaction.del(prefix + ":resource:" + resource_id);
    }
    transaction.del(prefix + ":job_ids")
        .del(prefix + ":by_submit")
        .del(prefix + ":by_start")
        .del(prefix + ":by_completion")
        .del(prefix + ":by_resources")
        .del(prefix + ":resources:by_time")
        .del(prefix + ":resources:csv")
        .set(prefix + ":csv", csv_header)
        .exec();
  }

  sw::redis::Redis redis;
  std::string prefix;
  bool job_timestamps_in_msec;
  bool resource_timestamps_in_msec = false;
  size_t next_resource_id = 0;
  bool resource_trace_initialized = false;
};

namespace {

double timestamp_score(sim_time_t value, bool msec) {
  if (!msec) {
    return static_cast<double>(static_cast<int64_t>(value));
  }
  return std::round(value * 1000.0) / 1000.0;
}

} // namespace

RedisOutput::RedisOutput(const std::string &uri, const std::string &key_prefix,
                         const std::string &csv_header, bool timestamps_in_msec)
    : m_impl(std::make_unique<Impl>(uri, key_prefix, csv_header,
                                    timestamps_in_msec)) {}

RedisOutput::~RedisOutput() = default;
RedisOutput::RedisOutput(RedisOutput &&) noexcept = default;
RedisOutput &RedisOutput::operator=(RedisOutput &&) noexcept = default;

sw::redis::Redis &RedisOutput::redis() { return m_impl->redis; }

void RedisOutput::queue_job_output(sw::redis::Transaction &transaction,
                                   const RedisJobRecord &job,
                                   std::string &csv_rows) {
  const std::string id = std::to_string(job.job_id);
  const std::string job_key = m_impl->prefix + ":job:" + id;
  const std::string submit_time =
      format_sim_time(job.submit_time, m_impl->job_timestamps_in_msec);
  const std::string start_time =
      format_sim_time(job.start_time, m_impl->job_timestamps_in_msec);
  const std::string completion_time =
      format_sim_time(job.completion_time, m_impl->job_timestamps_in_msec);
  std::string csv_line = submit_time + "," + start_time + "," +
                         completion_time + "," + std::to_string(job.num_nodes) +
                         "," + std::to_string(job.exit_status);
  if (job.queue) {
#if DR_EVT_LEGACY_QUEUE_INPUT
    csv_line += "," + dr_evt::to_string(*job.queue);
#else
    csv_line += "," + std::to_string(static_cast<unsigned>(*job.queue));
#endif
  }
  csv_line += "," +
              format_sim_time(job.time_limit, m_impl->job_timestamps_in_msec) +
              "\n";
  csv_rows += csv_line;

  transaction.hset(job_key, "job_id", id)
      .hset(job_key, "job_submit_time", submit_time)
      .hset(job_key, "begin_time", start_time)
      .hset(job_key, "end_time", completion_time)
      .hset(job_key, "num_nodes", std::to_string(job.num_nodes))
      .hset(job_key, "exit_status", std::to_string(job.exit_status));
  if (job.queue) {
#if DR_EVT_LEGACY_QUEUE_INPUT
    transaction.hset(job_key, "queue", dr_evt::to_string(*job.queue));
#else
    transaction.hset(job_key, "q_id",
                     std::to_string(static_cast<unsigned>(*job.queue)));
#endif
  }
  transaction
      .hset(job_key, "time_limit",
            format_sim_time(job.time_limit, m_impl->job_timestamps_in_msec))
      .sadd(m_impl->prefix + ":job_ids", id)
      .zadd(m_impl->prefix + ":by_submit", id,
            timestamp_score(job.submit_time, m_impl->job_timestamps_in_msec))
      .zadd(m_impl->prefix + ":by_start", id,
            timestamp_score(job.start_time, m_impl->job_timestamps_in_msec))
      .zadd(
          m_impl->prefix + ":by_completion", id,
          timestamp_score(job.completion_time, m_impl->job_timestamps_in_msec))
      .zadd(m_impl->prefix + ":by_resources", id,
            static_cast<double>(job.num_nodes));
}

void RedisOutput::start_resource_trace(const std::string &csv_header,
                                       bool timestamps_in_msec) {
  if (m_impl->resource_trace_initialized) {
    return;
  }
  m_impl->redis.set(m_impl->prefix + ":resources:csv", csv_header);
  m_impl->resource_timestamps_in_msec = timestamps_in_msec;
  m_impl->resource_trace_initialized = true;
}

void RedisOutput::queue_resource_output(sw::redis::Transaction &transaction,
                                        const RedisResourceRecord &sample,
                                        std::string &csv_rows) {
  const std::string id = std::to_string(m_impl->next_resource_id++);
  const std::string key = m_impl->prefix + ":resource:" + id;
  transaction.hset(key, "sample_id", id)
      .hset(key, "time",
            format_sim_time(sample.time, m_impl->resource_timestamps_in_msec))
      .hset(key, "free_nodes", std::to_string(sample.free_nodes))
      .hset(key, "allocated_nodes", std::to_string(sample.allocated_nodes));
  if (sample.pcon) {
    transaction.hset(key, "avgpcon", std::to_string(sample.pcon->average))
        .hset(key, "minpcon", std::to_string(sample.pcon->minimum))
        .hset(key, "maxpcon", std::to_string(sample.pcon->maximum));
  }
  transaction.zadd(
      m_impl->prefix + ":resources:by_time", id,
      timestamp_score(sample.time, m_impl->resource_timestamps_in_msec));
  csv_rows +=
      format_sim_time(sample.time, m_impl->resource_timestamps_in_msec) + "," +
      std::to_string(sample.free_nodes) + "," +
      std::to_string(sample.allocated_nodes);
  if (sample.pcon) {
    csv_rows += "," + std::to_string(sample.pcon->average) + "," +
                std::to_string(sample.pcon->minimum) + "," +
                std::to_string(sample.pcon->maximum);
  }
  csv_rows += "\n";
}

bool RedisOutput::resource_trace_active() const {
  return m_impl->resource_trace_initialized;
}

const std::string &RedisOutput::key_prefix() const { return m_impl->prefix; }

RedisCheckpointBoundary RedisOutput::checkpoint_boundary() {
  RedisCheckpointBoundary result;
  const auto job_bytes = m_impl->redis.strlen(m_impl->prefix + ":csv");
  result.job_csv_bytes = static_cast<std::uint64_t>(job_bytes);
  result.resource_trace_active = m_impl->resource_trace_initialized;
  if (result.resource_trace_active) {
    result.resource_csv_bytes = static_cast<std::uint64_t>(
        m_impl->redis.strlen(m_impl->prefix + ":resources:csv"));
    result.resource_count = m_impl->next_resource_id;
  }
  m_impl->redis.smembers(m_impl->prefix + ":job_ids",
                         std::back_inserter(result.job_ids));
  std::sort(result.job_ids.begin(), result.job_ids.end());
  return result;
}

std::uint64_t RedisOutput::archive_checkpoint_namespace(
    const std::string &uri, const std::string &key_prefix,
    const RedisCheckpointBoundary &boundary) {
  sw::redis::Redis redis(uri);
  std::uint64_t generation = 1;
  std::string archive;
  do {
    archive = key_prefix + ":pre-restart:" + std::to_string(generation++);
  } while (redis.exists(archive + ":checkpoint:job_csv_bytes") != 0);
  --generation;

  std::vector<std::string> job_ids;
  redis.smembers(key_prefix + ":job_ids", std::back_inserter(job_ids));
  std::vector<std::string> resource_ids;
  redis.zrange(key_prefix + ":resources:by_time", 0, -1,
               std::back_inserter(resource_ids));
  const std::array<std::string, 7> fixed_suffixes{
      ":csv",          ":job_ids",       ":by_submit", ":by_start",
      ":by_completion", ":by_resources", ":resources:csv"};
  for (const auto &suffix : fixed_suffixes) {
    if (redis.exists(key_prefix + suffix) != 0) {
      redis.rename(key_prefix + suffix, archive + suffix);
    }
  }
  if (redis.exists(key_prefix + ":resources:by_time") != 0) {
    redis.rename(key_prefix + ":resources:by_time",
                 archive + ":resources:by_time");
  }
  for (const auto &id : job_ids) {
    redis.rename(key_prefix + ":job:" + id, archive + ":job:" + id);
  }
  for (const auto &id : resource_ids) {
    redis.rename(key_prefix + ":resource:" + id,
                 archive + ":resource:" + id);
  }

  auto transaction = redis.transaction(false);
  transaction
      .set(archive + ":checkpoint:job_csv_bytes",
           std::to_string(boundary.job_csv_bytes))
      .set(archive + ":checkpoint:resource_csv_bytes",
           std::to_string(boundary.resource_csv_bytes))
      .set(archive + ":checkpoint:resource_count",
           std::to_string(boundary.resource_count));
  for (const auto &id : boundary.job_ids) {
    transaction.sadd(archive + ":checkpoint:job_ids", id);
  }
  transaction.exec();
  return generation;
}

} // namespace dr_evt
