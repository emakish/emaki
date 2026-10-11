// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "core.hpp"
#include <charconv>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace emaki::sudo_channel {
constexpr std::size_t MaxProcessMaps = 8 * 1024 * 1024;

inline bool validMappingRange(const std::string &range) {
  const auto dash = range.find('-');
  if (dash == std::string::npos || !dash || dash + 1 == range.size())
    return false;
  std::uint64_t start = 0, end = 0;
  const auto first = std::from_chars(range.data(), range.data() + dash, start, 16);
  const auto second = std::from_chars(range.data() + dash + 1,
                                    range.data() + range.size(), end, 16);
  return first.ec == std::errc{} && first.ptr == range.data() + dash &&
      second.ec == std::errc{} && second.ptr == range.data() + range.size() && start < end;
}

// Both descriptors use the filesystem's getattr operation. In particular,
// btrfs reports a subvolume device here, unlike the superblock device in maps.
// Following map_files requires CAP_CHECKPOINT_RESTORE in the initial user
// namespace; an unreadable or vanished mapping is never accepted by pathname.
inline bool mappedFileMatches(int trusted, pid_t pid, const std::string &range) {
  if (pid <= 0 || !validMappingRange(range))
    return false;
  const auto path = "/proc/" + std::to_string(pid) + "/map_files/" + range;
  Fd mapped(open(path.c_str(), O_PATH | O_CLOEXEC));
  struct stat expected{}, actual{};
  return mapped && !fstat(trusted, &expected) && !fstat(mapped.get(), &actual) &&
      S_ISREG(expected.st_mode) && S_ISREG(actual.st_mode) &&
      expected.st_dev == actual.st_dev && expected.st_ino == actual.st_ino;
}

// Resolve the installed path only in the broker's namespace. Check every
// component while holding its descriptor so user-controlled bind mounts in a
// peer's namespace cannot substitute the trusted reference file.
inline bool secureMappedFile(pid_t pid, const std::string &range,
                             const std::string &path) {
  if (path.empty() || path.front() != '/' || path.back() == '/')
    return false;
  Fd current(open("/", O_PATH | O_DIRECTORY | O_CLOEXEC));
  struct stat st{};
  if (!current || fstat(current.get(), &st) || st.st_uid || (st.st_mode & 0022))
    return false;
  for (std::size_t start = 1; start < path.size();) {
    auto end = path.find('/', start);
    const bool last = end == std::string::npos;
    const auto part = path.substr(start, last ? end : end - start);
    if (part.empty() || part == "." || part == "..")
      return false;
    Fd next(openat(current.get(), part.c_str(), O_PATH | O_NOFOLLOW | O_CLOEXEC));
    if (!next || fstat(next.get(), &st) || st.st_uid || (st.st_mode & 0022) ||
        (last ? !S_ISREG(st.st_mode) : !S_ISDIR(st.st_mode)))
      return false;
    current = std::move(next);
    if (last)
      return mappedFileMatches(current.get(), pid, range);
    start = end + 1;
  }
  return false;
}
}
