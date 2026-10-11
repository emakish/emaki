// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "timestamps.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstring>
#include <fcntl.h>
#include <deque>
#include <signal.h>
#include <time.h>
#include <sys/stat.h>
#include <unistd.h>

namespace emaki::sudo_channel {
namespace {
// Native sudo 1.9.17 timestamp v2 ABI, Linux LP64.
struct Record {
  std::uint16_t version, size, type, flags;
  uid_t authUid;
  pid_t sid;
  timespec start, stamp;
  union {
    dev_t tty;
    pid_t ppid;
  } identity;
};
static_assert(sizeof(Record) == 56 && offsetof(Record, flags) == 6 &&
              offsetof(Record, start) == 16 &&
              offsetof(Record, identity) == 48);
using Clock = std::chrono::steady_clock;
struct Descriptor {
  int fd;
  explicit Descriptor(int value) : fd(value) {}
  ~Descriptor() {
    if (fd >= 0)
      close(fd);
  }
};
bool fail(std::string &error, const char *message) {
  error = message;
  return false;
}
bool secureDirectory(int fd) {
  struct stat st{};
  return fstat(fd, &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == 0 &&
         !(st.st_mode & (S_IWGRP | S_IWOTH | S_ISUID | S_ISGID | S_ISVTX));
}
bool secureFile(const struct stat &st) {
  return S_ISREG(st.st_mode) && st.st_uid == 0 && st.st_nlink == 1 &&
         (st.st_mode & 07777) == 0600 && st.st_size >= 0;
}
void lockAlarm(int) {}
// Only used in the separate cleanup worker. A monotonic timer interrupts a
// blocking POSIX lock wait without SA_RESTART; the outer worker alarm remains.
class LockDeadline {
  sigset_t signalSet{}, oldMask{};
  struct sigaction oldAction{};
  timer_t timer{};
  bool maskSaved = false, actionSaved = false, created = false;

public:
  bool arm(std::chrono::milliseconds timeout, std::string &error) {
    sigemptyset(&signalSet);
    sigaddset(&signalSet, SIGALRM);
    if (sigprocmask(SIG_BLOCK, &signalSet, &oldMask) != 0)
      return fail(error, "cannot prepare timestamp lock deadline");
    maskSaved = true;
    struct sigaction action{};
    action.sa_handler = lockAlarm;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGALRM, &action, &oldAction) != 0)
      return fail(error, "cannot install timestamp lock deadline");
    actionSaved = true;
    struct sigevent event{};
    event.sigev_notify = SIGEV_SIGNAL;
    event.sigev_signo = SIGALRM;
    if (timer_create(CLOCK_MONOTONIC, &event, &timer) != 0)
      return fail(error, "cannot create timestamp lock deadline");
    created = true;
    const auto milliseconds = std::max<std::int64_t>(1, timeout.count());
    struct itimerspec interval{};
    interval.it_value = {milliseconds / 1000, (milliseconds % 1000) * 1000000};
    // Repeat only after expiry, closing the check-before-F_SETLKW signal race.
    interval.it_interval = {0, 10000000};
    if (timer_settime(timer, 0, &interval, nullptr) != 0 ||
        sigprocmask(SIG_UNBLOCK, &signalSet, nullptr) != 0)
      return fail(error, "cannot arm timestamp lock deadline");
    return true;
  }
  ~LockDeadline() {
    if (!maskSaved)
      return;
    sigprocmask(SIG_BLOCK, &signalSet, nullptr);
    if (created) {
      timer_delete(timer);
      const timespec immediate{};
      // Do not leave an expired timer signal for the restored disposition.
      while (sigtimedwait(&signalSet, nullptr, &immediate) >= 0) {}
    }
    if (actionSaved)
      sigaction(SIGALRM, &oldAction, nullptr);
    sigprocmask(SIG_SETMASK, &oldMask, nullptr);
  }
};
bool region(int fd, off_t offset, short type, Clock::time_point deadline,
            std::string &error, bool wait = true) {
  // Linux lockf uses these same process-owned fcntl byte-range locks.
  struct flock lock{};
  lock.l_type = type;
  lock.l_whence = SEEK_SET;
  lock.l_start = offset;
  lock.l_len = sizeof(Record);
  for (;;) {
    if (type != F_UNLCK && Clock::now() >= deadline)
      return fail(error, "timestamp lock deadline exceeded");
    if (fcntl(fd, wait && type != F_UNLCK ? F_SETLKW : F_SETLK, &lock) == 0)
      return true;
    if (errno == EINTR)
      continue;
    if (!wait && (errno == EACCES || errno == EAGAIN))
      return false;
    return fail(error, "timestamp record lock failed");
  }
}
bool valid(const Record &r, std::size_t index) {
  auto timeValid = [](const timespec &t) {
    return t.tv_sec >= 0 && t.tv_nsec >= 0 && t.tv_nsec < 1000000000;
  };
  return r.version == 2 && r.size == sizeof(Record) &&
         (index == 0 ? r.type == 4 : r.type >= 1 && r.type <= 3) &&
         !(r.flags & ~1U) && timeValid(r.start) && timeValid(r.stamp);
}
bool matches(const Record &r, const std::vector<TimestampParent> &parents,
             std::uint64_t ticks) {
  if (r.type != 3)
    return false;
  return std::any_of(parents.begin(), parents.end(), [&](const auto &p) {
    return p.ppid == r.identity.ppid && p.sid == r.sid &&
           p.start / ticks == static_cast<std::uint64_t>(r.start.tv_sec) &&
           (p.start % ticks) * (1000000000ULL / ticks) ==
               static_cast<std::uint64_t>(r.start.tv_nsec);
  });
}
} // namespace

static bool disableBatch(const char *directory, uid_t uid,
                           const std::vector<TimestampParent> &parents,
                           TimestampBatch &batch,
                           std::chrono::milliseconds timeout, std::string &error) {
  error.clear();
  batch.heldCount = 0;
  if (parents.empty()) {
    batch = {};
    return true;
  }
  const auto deadline = Clock::now() + timeout;
  LockDeadline timer;
  if (!timer.arm(timeout, error))
    return false;
  if (!directory || directory[0] != '/' || parents.size() > 4096)
    return fail(error, "invalid timestamp cleanup request");
  const long ticks = sysconf(_SC_CLK_TCK);
  if (ticks <= 0 || ticks > 1000000000)
    return fail(error, "unsupported process clock rate");
  Descriptor dir(open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
  if (dir.fd < 0)
    return fail(error, "cannot open filesystem root");
  // The configured timestamp root is the ownership boundary. Resolve it without
  // following symlinks, but do not impose its ownership/mode on ancestors (a
  // test root may live under a shared temporary directory).
  const std::string path(directory);
  std::size_t begin = 1;
  while (begin < path.size()) {
    const auto end = path.find('/', begin);
    const auto component = path.substr(begin, end - begin);
    if (component.empty() || component == "." || component == "..")
      return fail(error, "invalid timestamp directory component");
    const int next = openat(dir.fd, component.c_str(),
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (next < 0) {
      if (errno == ENOENT) {
        batch = {};
        return true;
      }
      return fail(error, "cannot open timestamp directory");
    }
    close(dir.fd);
    dir.fd = next;
    if (end == std::string::npos)
      break;
    begin = end + 1;
  }
  if (!secureDirectory(dir.fd))
    return fail(error, "unsafe timestamp directory ownership or mode");
  const auto name = std::to_string(uid);
  Descriptor file(openat(dir.fd, name.c_str(),
                         O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW));
  if (file.fd < 0) {
    if (errno == ENOENT) {
      batch = {};
      return true;
    }
    return fail(error, "cannot open timestamp file");
  }
  struct stat initial{};
  if (fstat(file.fd, &initial) != 0 || !secureFile(initial))
    return fail(error, "unsafe timestamp file ownership, mode or size");
  if (!region(file.fd, 0, F_WRLCK, deadline, error))
    return false;
  // Sudo can append while this operation waits for the creation lock.
  if (fstat(file.fd, &initial) != 0 || !secureFile(initial) ||
      initial.st_size % sizeof(Record) != 0)
    return fail(error, "malformed timestamp file size");
  auto &cursor = batch.cursor;
  if (cursor.inode != initial.st_ino || cursor.device != initial.st_dev ||
      cursor.end > initial.st_size) {
    cursor = {initial.st_dev, initial.st_ino, 0, initial.st_size};
    batch.retry = -1;
  }
  if (cursor.next < 0 || cursor.next > cursor.end ||
      cursor.next % sizeof(Record) != 0 || cursor.end % sizeof(Record) != 0)
    return fail(error, "invalid timestamp scan cursor");
  auto unchanged = [&] {
    struct stat current{}, named{};
    return fstat(file.fd, &current) == 0 && secureFile(current) &&
           current.st_size >= cursor.end &&
           fstatat(dir.fd, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0 &&
           secureFile(named) && named.st_ino == current.st_ino &&
           named.st_dev == current.st_dev;
  };
  if (!unchanged())
    return fail(error, "timestamp file replaced");
  if (batch.retry >= 0 &&
      (batch.retry >= cursor.end || batch.retry % sizeof(Record) != 0))
    return fail(error, "invalid timestamp retry offset");
  const bool retry = batch.retry >= 0;
  const auto offset = retry ? batch.retry : cursor.next;
  if (offset == cursor.end)
    return true;
  std::array<Record, TimestampBatchSize> records{};
  const auto bytes = retry ? sizeof(Record) : static_cast<std::size_t>(
      std::min<off_t>(cursor.end - cursor.next, sizeof(records)));
  const auto count = bytes / sizeof(Record);
  if (pread(file.fd, records.data(), bytes, offset) !=
      static_cast<ssize_t>(bytes))
    return fail(error, "cannot read timestamp batch");
  for (std::size_t i = 0; i < count; ++i)
    if (!valid(records[i], offset / sizeof(Record) + i))
      return fail(error, "malformed timestamp record");
  // As in timestamp_lock(), never wait for a record while holding lock zero.
  if (!region(file.fd, 0, F_UNLCK, deadline, error))
    return false;
  for (std::size_t i = 0; i < count; ++i) {
    if (!matches(records[i], parents, ticks))
      continue;
    const auto position = offset + static_cast<off_t>(i * sizeof(Record));
    // Scan every record before waiting. Only individually held offsets return
    // to the queue; one stalled PAM transaction cannot protect other records.
    if (!region(file.fd, position, F_WRLCK, deadline, error, retry)) {
      if (!error.empty())
        return false;
      batch.held[batch.heldCount++] = position;
      continue;
    }
    Record current{};
    if (!unchanged())
      return fail(error, "timestamp file changed during lock wait");
    if (pread(file.fd, &current, sizeof(current), position) != sizeof(current))
      return fail(error, "cannot reread timestamp record");
    if (!valid(current, position / sizeof(Record)))
      return fail(error, "timestamp record became malformed");
    // Sudo may rewrite its cookie after PAM while we wait for this lock.
    // Match the locked record again and alter only its disabled flag.
    if (matches(current, parents, ticks)) {
      if (Clock::now() >= deadline)
        return fail(error, "timestamp cleanup deadline exceeded");
      const auto flags = static_cast<std::uint16_t>(current.flags | 1U);
      if (pwrite(file.fd, &flags, sizeof(flags),
                 position + offsetof(Record, flags)) != sizeof(flags))
        return fail(error, "cannot disable timestamp record");
    }
    if (!region(file.fd, position, F_UNLCK, deadline, error))
      return false;
  }
  if (!unchanged())
    return fail(error, "timestamp file changed after cleanup");
  if (retry)
    batch.retry = -1;
  else
    cursor.next += bytes;
  return true;
}

bool disableTimestampBatch(const char *directory, uid_t uid,
                           const std::vector<TimestampParent> &parents,
                           TimestampBatch &batch,
                           std::chrono::milliseconds timeout, std::string &error) {
  auto next = batch;
  batch.heldCount = 0;
  if (!disableBatch(directory, uid, parents, next, timeout, error))
    return false;
  batch = next;
  return true;
}

bool disableTimestamps(const char *directory, uid_t uid,
                       const std::vector<TimestampParent> &parents,
                       std::chrono::milliseconds timeout, std::string &error) {
  TimestampBatch batch;
  std::deque<off_t> held;
  const auto deadline = Clock::now() + timeout;
  do {
    const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline - Clock::now());
    if (remaining.count() <= 0)
      return fail(error, "timestamp cleanup deadline exceeded");
    if (batch.cursor.next == batch.cursor.end && !held.empty())
      batch.retry = held.front();
    const auto previous = batch.cursor;
    const bool retry = batch.retry >= 0;
    if (!disableTimestampBatch(directory, uid, parents, batch, remaining, error))
      return false;
    if (batch.cursor.device != previous.device ||
        batch.cursor.inode != previous.inode ||
        batch.cursor.end < previous.end || batch.cursor.next < previous.next)
      held.clear();
    else if (retry)
      held.pop_front();
    for (std::size_t i = 0; i < batch.heldCount; ++i)
      held.push_back(batch.held[i]);
  } while (batch.cursor.next != batch.cursor.end || !held.empty());
  return true;
}
} // namespace emaki::sudo_channel
