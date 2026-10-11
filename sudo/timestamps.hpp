// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <chrono>
#include <cstdint>
#include <string>
#include <sys/types.h>
#include <vector>
namespace emaki::sudo_channel {
struct TimestampParent {
  pid_t ppid;
  pid_t sid;
  std::uint64_t start;
  bool operator==(const TimestampParent &) const = default;
};
// A worker examines at most this many records before returning its cursor.
constexpr std::size_t TimestampBatchSize = 256;
struct TimestampCursor {
  dev_t device = 0;
  ino_t inode = 0;
  off_t next = 0, end = 0;
};
struct TimestampBatch {
  TimestampCursor cursor;
  std::array<off_t, TimestampBatchSize> held{};
  std::size_t heldCount = 0;
  // A retry waits for this one record; -1 scans the next bounded batch.
  off_t retry = -1;
};
// True means this batch succeeded, not necessarily that the file is finished.
// Keep held offsets for independent retries; preserve the cursor between workers.
// Failure preserves the input cursor/retry and returns no new held offsets.
bool disableTimestampBatch(const char *directory, uid_t uid,
                           const std::vector<TimestampParent> &parents,
                           TimestampBatch &batch,
                           std::chrono::milliseconds timeout,
                           std::string &error);
// Run in a separate worker process: POSIX record locks belong to the process.
// False requires a logged retry; timeout bounds all lock waits together.
bool disableTimestamps(const char *directory, uid_t uid,
                       const std::vector<TimestampParent> &parents,
                       std::chrono::milliseconds timeout, std::string &error);
} // namespace emaki::sudo_channel
