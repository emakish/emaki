// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "timestamps.hpp"
#include <chrono>
#include <cstdint>
#include <memory>
namespace emaki::sudo_channel {
class TimestampCleanup {
  struct State;
  std::unique_ptr<State> state_;

public:
  TimestampCleanup();
  ~TimestampCleanup();
  TimestampCleanup(const TimestampCleanup &) = delete;
  TimestampCleanup &operator=(const TimestampCleanup &) = delete;
  bool initialize(const char *runtimeDirectory, const char *timestampDirectory);
  bool remember(std::uint64_t key, uid_t uid, TimestampParent parent);
  void end(std::uint64_t key);
  void endAll();
  void poll();
  bool drain(std::chrono::milliseconds timeout);
  bool healthy() const;
  bool busy() const;
  std::size_t pending(uid_t uid) const;
};
} // namespace emaki::sudo_channel
