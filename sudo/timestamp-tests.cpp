// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "cleanup.hpp"
#include "timestamps.hpp"
#include <cassert>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <iostream>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
using namespace emaki::sudo_channel;
using namespace std::chrono_literals;
namespace {
bool normalize = false;
bool wrongFileOwner = false;
bool wrongRootOwner = false;
std::string temporary;
dev_t rootDevice;
ino_t rootInode;
struct Record {
  std::uint16_t version = 2, size = 56, type = 3, flags = 0;
  uid_t auth = 1000;
  pid_t sid = 10;
  timespec start{123, 450000000}, stamp{999, 42};
  union {
    dev_t tty;
    pid_t ppid;
  } identity{};
};
static_assert(sizeof(Record) == 56);
void adjust(struct stat *st) {
  if (!normalize)
    return;
  // Map the fixture owner, preserving every real permission bit.
  if (st->st_uid == getuid())
    st->st_uid = 0;
  if (wrongRootOwner && st->st_dev == rootDevice && st->st_ino == rootInode)
    st->st_uid = 1;
  if (wrongFileOwner && S_ISREG(st->st_mode))
    st->st_uid = 1;
}
std::string file(uid_t uid = 1000) {
  return temporary + "/" + std::to_string(uid);
}
void put(const std::vector<Record> &records, uid_t uid = 1000) {
  const int fd = open(file(uid).c_str(), O_CREAT | O_TRUNC | O_WRONLY, 0600);
  assert(fd >= 0);
  const auto size = records.size() * sizeof(Record);
  assert(write(fd, records.data(), size) == static_cast<ssize_t>(size));
  close(fd);
}
std::vector<Record> get(uid_t uid = 1000) {
  const int fd = open(file(uid).c_str(), O_RDONLY);
  assert(fd >= 0);
  struct stat st{};
  assert(fstat(fd, &st) == 0);
  std::vector<Record> out(st.st_size / sizeof(Record));
  assert(read(fd, out.data(), st.st_size) == st.st_size);
  close(fd);
  return out;
}
bool same(const std::vector<Record> &a, const std::vector<Record> &b) {
  return a.size() == b.size() &&
         std::memcmp(a.data(), b.data(), a.size() * sizeof(Record)) == 0;
}
} // namespace
extern "C" int __real_fstat(int, struct stat *);
extern "C" int __wrap_fstat(int fd, struct stat *st) {
  const int ret = __real_fstat(fd, st);
  if (ret == 0)
    adjust(st);
  return ret;
}
extern "C" int __real_fstatat(int, const char *, struct stat *, int);
extern "C" int __wrap_fstatat(int fd, const char *path, struct stat *st,
                              int flags) {
  const int ret = __real_fstatat(fd, path, st, flags);
  if (ret == 0)
    adjust(st);
  return ret;
}
int main() {
  auto pattern =
      (std::filesystem::temp_directory_path() / "emaki-timestamps-XXXXXX")
          .string();
  assert(mkdtemp(pattern.data()));
  const auto fixture = pattern;
  assert(chmod(fixture.c_str(), 01777) == 0);
  temporary = fixture + "/timestamps";
  assert(mkdir(temporary.c_str(), 0700) == 0);
  struct stat root{};
  assert(stat(temporary.c_str(), &root) == 0);
  rootDevice = root.st_dev;
  rootInode = root.st_ino;
  std::vector<Record> records(8);
  records[0].type = 4;
  records[1].type = 2;
  for (std::size_t i = 1; i < records.size(); ++i)
    records[i].identity.ppid = 42;
  records[3].identity.ppid = 43;
  records[4].sid = 11;
  records[5].start.tv_nsec = 460000000;
  records[6].type = 1;
  records[7].auth =
      0; // targetpw/rootpw records still belong to this invoking uid.
  put(records);
  put(records, 1001);
  const auto ticks = static_cast<std::uint64_t>(sysconf(_SC_CLK_TCK));
  const std::vector<TimestampParent> parents{
      {42, 10, 123 * ticks + 45 * ticks / 100}};
  std::string error;
  normalize = true;
  auto refusesUnchanged = [&](const std::string &path, const char *reason) {
    assert(!disableTimestamps(path.c_str(), 1000, parents, 1s, error));
    assert(error == reason);
    assert(same(get(), records) && same(get(1001), records));
  };
  wrongRootOwner = true;
  refusesUnchanged(temporary, "unsafe timestamp directory ownership or mode");
  wrongRootOwner = false;
  // The private root is safe beneath the genuinely sticky writable parent.
  assert(stat(fixture.c_str(), &root) == 0);
  assert((root.st_mode & 07777) == 01777);
  if (!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error)) {
    std::cerr << error << "\n";
    assert(false);
  }
  auto expected = records;
  expected[2].flags = expected[7].flags = 1;
  assert(same(get(), expected) && same(get(1001), records));
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  assert(same(get(), expected));
  put(records);
  assert(disableTimestamps(
      temporary.c_str(), 1000,
      {{42, 10, parents[0].start}, {43, 10, parents[0].start}}, 1s, error));
  auto multiple = expected;
  multiple[3].flags = 1;
  assert(same(get(), multiple));
  // Every malformed file is rejected before any otherwise matching record
  // changes.
  for (int variant = 0; variant < 5; ++variant) {
    auto odd = records;
    if (variant == 0)
      odd.back().version = 1;
    if (variant == 1)
      odd.back().size = 0;
    if (variant == 2)
      odd.back().type = 9;
    if (variant == 3)
      odd.back().stamp.tv_nsec = 1000000000;
    if (variant == 4)
      odd[0].type = 3;
    put(odd);
    assert(!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
    assert(same(get(), odd));
  }
  put(records);
  wrongFileOwner = true;
  refusesUnchanged(temporary, "unsafe timestamp file ownership, mode or size");
  wrongFileOwner = false;
  const int truncated = open(file().c_str(), O_WRONLY);
  assert(truncated >= 0 &&
         ftruncate(truncated, records.size() * sizeof(Record) - 1) == 0);
  close(truncated);
  assert(!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  put(records);
  // Valid timestamp files can grow without a sudo-imposed record limit.
  // Put served records on both sides of the old limit and at the final offset.
  std::vector<Record> large(8193, records[3]);
  large[0] = records[0];
  large[2] = large[4096] = large.back() = records[2];
  auto largeExpected = large;
  largeExpected[2].flags = largeExpected[4096].flags =
      largeExpected.back().flags = 1;
  put(large);
  TimestampBatch batch;
  std::size_t passes = 0;
  do {
    const auto before = batch.cursor.next;
    assert(disableTimestampBatch(temporary.c_str(), 1000, parents, batch, 1s,
                                 error));
    assert(batch.cursor.next > before);
    assert(batch.cursor.next - before <=
           static_cast<off_t>(TimestampBatchSize * sizeof(Record)));
    ++passes;
  } while (batch.cursor.next < batch.cursor.end);
  assert(passes > 16 && same(get(), largeExpected));
  assert(same(get(1001), records));
  put(large);
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 2s, error));
  assert(same(get(), largeExpected));
  put(records);
  const auto alias = fixture + "/alias";
  assert(symlink(temporary.c_str(), alias.c_str()) == 0);
  refusesUnchanged(alias, "cannot open timestamp directory");
  assert(unlink(alias.c_str()) == 0);
  assert(symlink(fixture.c_str(), alias.c_str()) == 0);
  refusesUnchanged(alias + "/timestamps", "cannot open timestamp directory");
  assert(unlink(alias.c_str()) == 0);
  for (const auto &path :
       {fixture + "/./timestamps", temporary + "/../timestamps",
        fixture + "//timestamps"})
    refusesUnchanged(path, "invalid timestamp directory component");
  for (mode_t mode : {0770, 0702, 01700, 02700, 04700}) {
    assert(chmod(temporary.c_str(), mode) == 0);
    refusesUnchanged(temporary, "unsafe timestamp directory ownership or mode");
  }
  assert(chmod(temporary.c_str(), 0700) == 0);
  for (mode_t mode : {0640, 0602, 0700, 01600, 02600, 04600}) {
    assert(chmod(file().c_str(), mode) == 0);
    refusesUnchanged(temporary,
                     "unsafe timestamp file ownership, mode or size");
  }
  assert(chmod(file().c_str(), 0600) == 0);
  const auto link = temporary + "/hardlink";
  assert(::link(file().c_str(), link.c_str()) == 0);
  assert(!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  assert(unlink(link.c_str()) == 0);
  assert(unlink(file().c_str()) == 0);
  assert(symlink("1001", file().c_str()) == 0);
  assert(!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  assert(same(get(1001), records));
  assert(unlink(file().c_str()) == 0);
  assert(mkfifo(file().c_str(), 0600) == 0);
  assert(!disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  assert(unlink(file().c_str()) == 0);
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  put(records);
  // A separate process holds sudo's record lock across an authentication delay,
  // then writes its private cookie (clearing disabled), exactly before unlock.
  int ready[2];
  assert(pipe(ready) == 0);
  const pid_t child = fork();
  assert(child >= 0);
  if (child == 0) {
    close(ready[0]);
    const int fd = open(file().c_str(), O_RDWR);
    assert(fd >= 0);
    assert(lseek(fd, 2 * sizeof(Record), SEEK_SET) >= 0);
    assert(lockf(fd, F_LOCK, sizeof(Record)) == 0);
    assert(write(ready[1], "r", 1) == 1);
    std::this_thread::sleep_for(250ms);
    auto cookie = records[2];
    cookie.stamp = {1234, 567};
    assert(pwrite(fd, &cookie, sizeof(cookie), 2 * sizeof(Record)) ==
           sizeof(cookie));
    close(fd);
    _exit(0);
  }
  close(ready[1]);
  char byte;
  assert(read(ready[0], &byte, 1) == 1);
  close(ready[0]);
  auto began = std::chrono::steady_clock::now();
  assert(!disableTimestamps(temporary.c_str(), 1000, parents, 40ms, error));
  assert(std::chrono::steady_clock::now() - began < 200ms);
  auto partlyDisabled = records;
  partlyDisabled[7].flags = 1;
  assert(same(get(), partlyDisabled));
  assert(error == "timestamp lock deadline exceeded");
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 2s, error));
  int status;
  assert(waitpid(child, &status, 0) == child && WIFEXITED(status) &&
         WEXITSTATUS(status) == 0);
  expected[2].stamp = {1234, 567};
  assert(same(get(), expected));
  assert(same(get(1001), records));
  // Appending unrelated records cannot invalidate the captured scan boundary.
  put(records);
  assert(pipe(ready) == 0);
  const pid_t appender = fork();
  assert(appender >= 0);
  if (appender == 0) {
    close(ready[0]);
    const int fd = open(file().c_str(), O_RDWR);
    assert(fd >= 0);
    assert(lseek(fd, 2 * sizeof(Record), SEEK_SET) >= 0);
    assert(lockf(fd, F_LOCK, sizeof(Record)) == 0);
    assert(write(ready[1], "r", 1) == 1);
    std::this_thread::sleep_for(80ms);
    auto extra = records[3];
    assert(pwrite(fd, &extra, sizeof(extra), records.size() * sizeof(Record)) ==
           sizeof(extra));
    close(fd);
    _exit(0);
  }
  close(ready[1]);
  assert(read(ready[0], &byte, 1) == 1);
  close(ready[0]);
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 2s, error));
  assert(waitpid(appender, &status, 0) == appender && WIFEXITED(status) &&
         WEXITSTATUS(status) == 0);
  auto appended = records;
  appended.push_back(records[3]);
  appended[2].flags = appended[7].flags = 1;
  assert(same(get(), appended));
  assert(disableTimestamps(temporary.c_str(), 1000, parents, 1s, error));
  assert(same(get(), appended));
  // Exercise the durable queue and its actual forked cleanup worker together.
  const auto runtime = temporary + "/runtime";
  assert(mkdir(runtime.c_str(), 0755) == 0);
  assert(chmod(runtime.c_str(), 0755) == 0);
  const auto journals = runtime + "/cleanup";
  auto journalCount = [&] {
    std::size_t count = 0;
    for (const auto &entry : std::filesystem::directory_iterator(journals))
      if (entry.path().filename() != "lock")
        ++count;
    return count;
  };
  put(records);
  auto integrated = records;
  integrated[2].flags = integrated[7].flags = 1;
  {
    TimestampCleanup cleanup;
    assert(cleanup.initialize(runtime.c_str(), temporary.c_str()));
    assert(cleanup.healthy());
    assert(cleanup.remember(11, 1000, parents[0]));
    assert(journalCount() == 1);
    assert(cleanup.pending(1000) == 0);
    cleanup.poll();
    assert(same(get(), records));
    cleanup.end(11);
    assert(cleanup.pending(1000) == 1);
    assert(cleanup.drain(3s));
    assert(cleanup.pending(1000) == 0 && journalCount() == 0);
    assert(same(get(), integrated));
    assert(same(get(1001), records));
  }
  put(large);
  {
    TimestampCleanup cleanup;
    assert(cleanup.initialize(runtime.c_str(), temporary.c_str()));
    assert(cleanup.remember(13, 1000, parents[0]));
    cleanup.end(13);
    assert(cleanup.drain(3s));
    assert(cleanup.pending(1000) == 0 && journalCount() == 0);
    assert(same(get(), largeExpected));
  }
  // A bounded cleanup helper may exit between batches. Its replacement must
  // resume, otherwise a file beyond one helper's lifetime can never finish.
  put(large);
  {
    TimestampCleanup cleanup;
    assert(cleanup.initialize(runtime.c_str(), temporary.c_str()));
    assert(cleanup.remember(15, 1000, parents[0]));
    std::filesystem::path journal;
    for (const auto &entry : std::filesystem::directory_iterator(journals))
      if (entry.path().filename().string().size() == 32)
        journal = entry.path();
    assert(!journal.empty());
    const auto initialSize = std::filesystem::file_size(journal);
    cleanup.end(15);
    const auto until = std::chrono::steady_clock::now() + 2s;
    do {
      cleanup.poll();
      if (std::filesystem::file_size(journal) > initialSize)
        break;
      assert(std::chrono::steady_clock::now() < until);
      std::this_thread::sleep_for(5ms);
    } while (true);
    assert(get()[2].flags == 1);
  }
  assert(journalCount() == 1);
  // Teardown must reap even the next worker forked just before scope exit.
  // Otherwise its inherited journal lock can race the replacement below.
  assert(waitpid(-1, nullptr, WNOHANG) == -1 && errno == ECHILD);
  // Renew the completed early record; recovery must leave it alone while it
  // finishes records beyond the saved scan cursor.
  const int renewed = open(file().c_str(), O_WRONLY);
  assert(renewed >= 0);
  assert(pwrite(renewed, &large[2], sizeof(Record), 2 * sizeof(Record)) ==
         sizeof(Record));
  close(renewed);
  {
    TimestampCleanup resumed;
    assert(resumed.initialize(runtime.c_str(), temporary.c_str()));
    assert(resumed.pending(1000) == 1);
    assert(resumed.drain(3s));
    auto finished = largeExpected;
    finished[2] = large[2];
    assert(same(get(), finished));
    assert(journalCount() == 0);
  }
  // A held early record cannot protect served records in later batches.
  // Keep it held until the real worker queue has disabled the distant records.
  put(large);
  int release[2];
  assert(pipe(ready) == 0 && pipe(release) == 0);
  const pid_t holder = fork();
  assert(holder >= 0);
  if (holder == 0) {
    close(ready[0]);
    close(release[1]);
    const int fd = open(file().c_str(), O_RDWR);
    assert(fd >= 0 && lseek(fd, 2 * sizeof(Record), SEEK_SET) >= 0);
    assert(lockf(fd, F_LOCK, sizeof(Record)) == 0);
    assert(write(ready[1], "r", 1) == 1);
    char done;
    assert(read(release[0], &done, 1) == 1);
    auto cookie = large[2];
    cookie.stamp = {1234, 789};
    assert(pwrite(fd, &cookie, sizeof(cookie), 2 * sizeof(Record)) ==
           sizeof(cookie));
    close(fd);
    _exit(0);
  }
  close(ready[1]);
  close(release[0]);
  assert(read(ready[0], &byte, 1) == 1);
  close(ready[0]);
  {
    TimestampCleanup cleanup;
    assert(cleanup.initialize(runtime.c_str(), temporary.c_str()));
    assert(cleanup.remember(14, 1000, parents[0]));
    cleanup.end(14);
    const auto until = std::chrono::steady_clock::now() + 2s;
    std::vector<Record> current;
    do {
      cleanup.poll();
      current = get();
      if (current[4096].flags == 1 && current.back().flags == 1)
        break;
      assert(std::chrono::steady_clock::now() < until);
      std::this_thread::sleep_for(5ms);
    } while (true);
    assert(current[2].flags == 0);
    assert(cleanup.pending(1000) == 1 && journalCount() == 1);
    // A completed record may subsequently be renewed through ordinary PAM.
    // Retrying the other offset must not disable that completed record again.
    const int fd = open(file().c_str(), O_WRONLY);
    assert(fd >= 0);
    const auto position = static_cast<off_t>((large.size() - 1) * sizeof(Record));
    assert(pwrite(fd, &large.back(), sizeof(Record), position) == sizeof(Record));
    close(fd);
    assert(write(release[1], "r", 1) == 1);
    close(release[1]);
    assert(waitpid(holder, &status, 0) == holder && WIFEXITED(status) &&
           WEXITSTATUS(status) == 0);
    assert(cleanup.drain(3s));
    auto finished = largeExpected;
    finished[2].stamp = {1234, 789};
    finished.back() = large.back();
    assert(same(get(), finished));
    assert(cleanup.pending(1000) == 0 && journalCount() == 0);
  }
  // A remembered key survives destruction without end(), as on broker loss.
  // Reinitialization treats the durable identity as ended and runs the same
  // real file cleaner, preserving all unrelated records and the other uid.
  put(records);
  {
    TimestampCleanup interrupted;
    assert(interrupted.initialize(runtime.c_str(), temporary.c_str()));
    assert(interrupted.remember(12, 1000, parents[0]));
    assert(journalCount() == 1);
    assert(interrupted.pending(1000) == 0);
  }
  assert(same(get(), records));
  assert(journalCount() == 1);
  {
    TimestampCleanup replay;
    assert(replay.initialize(runtime.c_str(), temporary.c_str()));
    assert(replay.pending(1000) == 1);
    assert(replay.drain(3s));
    assert(replay.healthy() && replay.pending(1000) == 0);
    assert(journalCount() == 0);
    assert(same(get(), integrated));
    assert(same(get(1001), records));
  }
  normalize = false;
  std::filesystem::remove_all(fixture);
  std::cout
      << "timestamp records, selective flags, metadata refusal, "
         "cross-process PAM write, timeout, durable queue and replay: PASS\n";
}
