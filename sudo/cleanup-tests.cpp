// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "cleanup.hpp"
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
using namespace emaki::sudo_channel;
using namespace std::chrono_literals;
namespace fs = std::filesystem;
extern "C" int __real_fstat(int, struct stat *);
// Fixtures belong to the unprivileged test runner. Production always checks uid
// 0.
extern "C" int __wrap_fstat(int fd, struct stat *s) {
  int result = __real_fstat(fd, s);
  if (result == 0 && s->st_uid == getuid())
    s->st_uid = 0;
  return result;
}
namespace emaki::sudo_channel {
bool disableTimestampBatch(const char *directory, uid_t uid,
                           const std::vector<TimestampParent> &parents,
                           TimestampBatch &batch,
                           std::chrono::milliseconds timeout,
                           std::string &error) {
  assert(timeout <= 2s);
  assert(!parents.empty());
  fs::path path(directory);
  if (fs::remove(path / "slow"))
    std::this_thread::sleep_for(5s);
  if (fs::exists(path / "fail")) {
    error = "deliberate retry fixture";
    return false;
  }
  if (fs::exists(path / "batches")) {
    if (batch.cursor.inode == 0)
      batch.cursor = {1, 1, 0, 768};
    std::ofstream(path / "offsets", std::ios::app) << batch.cursor.next << '\n';
    if (batch.cursor.next == 256 && fs::exists(path / "pause")) {
      error = "paused between batches";
      return false;
    }
    batch.cursor.next = std::min(batch.cursor.end, batch.cursor.next + 256);
  }
  if (fs::exists(path / "held")) {
    if (fs::exists(path / "replace")) {
      batch.cursor = {1, 2, 1, 1};
      batch.retry = -1;
    } else if (batch.cursor.inode == 0) {
      batch.cursor = {1, 1, 3, 3};
      batch.held[0] = 0;
      batch.held[1] = 1;
      batch.heldCount = 2;
    } else {
      assert(batch.retry == 0 || batch.retry == 1);
      if (batch.retry == 0 && fs::exists(path / "blocked")) {
        error = "first record remains locked";
        return false;
      }
      std::ofstream(path / ("released-" + std::to_string(batch.retry)))
          .put('1');
      batch.retry = -1;
    }
  }
  std::ofstream out(path / (std::to_string(uid) + ".result"), std::ios::app);
  out << parents.size() << '\n';
  return out.good();
}
} // namespace emaki::sudo_channel
struct Fixture {
  fs::path root;
  Fixture() {
    std::string pattern =
        (fs::temp_directory_path() / "emaki-cleanup-XXXXXX").string();
    char *name = mkdtemp(pattern.data());
    assert(name);
    root = name;
    assert(chmod(root.c_str(), 0755) == 0);
  }
  ~Fixture() { fs::remove_all(root); }
  bool open(TimestampCleanup &queue) {
    return queue.initialize(root.c_str(), root.c_str());
  }
  std::size_t journals() {
    std::size_t count = 0;
    for (const auto &entry : fs::directory_iterator(root / "cleanup"))
      if (entry.path().filename() != "lock")
        ++count;
    return count;
  }
};
struct LogCapture {
  fs::path path;
  int saved;
  explicit LogCapture(const fs::path &root) : path(root / "diagnostics") {
    std::fflush(stderr);
    saved = dup(STDERR_FILENO);
    assert(saved >= 0);
    int output =
        open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    assert(output >= 0);
    assert(dup2(output, STDERR_FILENO) == STDERR_FILENO);
    close(output);
  }
  ~LogCapture() {
    std::fflush(stderr);
    assert(dup2(saved, STDERR_FILENO) == STDERR_FILENO);
    close(saved);
  }
  std::string text() const {
    std::fflush(stderr);
    std::ifstream input(path);
    return {std::istreambuf_iterator<char>(input),
            std::istreambuf_iterator<char>()};
  }
};
int main() {
  const TimestampParent parent{100, 99, 123};
  {
    Fixture f;
    std::ofstream(f.root / "batches").put('1');
    std::ofstream(f.root / "pause").put('1');
    LogCapture log(f.root);
    {
      TimestampCleanup queue;
      assert(f.open(queue));
      assert(queue.remember(1, 1000, parent));
      assert(!queue.drain(100ms));
      assert(f.journals() == 1 && queue.pending(1000) == 1);
    }
    fs::remove(f.root / "pause");
    TimestampCleanup queue;
    assert(f.open(queue));
    assert(queue.drain(2s));
    assert(f.journals() == 0);
    std::ifstream offsets(f.root / "offsets");
    std::vector<off_t> seen{std::istream_iterator<off_t>(offsets),
                            std::istream_iterator<off_t>()};
    assert(seen.front() == 0 && seen.back() == 512);
    assert(std::count(seen.begin(), seen.end(), 0) == 1);
    assert(std::find(seen.begin(), seen.end(), 256) != seen.end());
  }
  {
    Fixture f;
    TimestampCleanup queue;
    assert(f.open(queue));
    std::ofstream(f.root / "batches").put('1');
    LogCapture log(f.root);
    assert(queue.remember(1, 1000, parent));
    assert(queue.drain(2s));
    assert(log.text().empty());
    std::ifstream offsets(f.root / "offsets");
    std::vector<off_t> seen{std::istream_iterator<off_t>(offsets),
                            std::istream_iterator<off_t>()};
    assert((seen == std::vector<off_t>{0, 256, 512}));
  }
  for (bool replace : {false, true}) {
    Fixture f;
    std::ofstream(f.root / "held").put('1');
    std::ofstream(f.root / "blocked").put('1');
    LogCapture log(f.root);
    {
      TimestampCleanup queue;
      assert(f.open(queue));
      assert(queue.remember(1, 1000, parent));
      assert(!queue.drain(150ms));
      assert(f.journals() == 1);
    }
    assert(fs::exists(f.root / "released-1"));
    fs::remove(f.root / "released-1");
    assert(!fs::exists(f.root / "released-0"));
    if (replace)
      std::ofstream(f.root / "replace").put('1');
    else
      fs::remove(f.root / "blocked");
    TimestampCleanup queue;
    assert(f.open(queue));
    assert(queue.drain(2s));
    assert(f.journals() == 0);
    assert(fs::exists(f.root / "released-0") != replace);
    assert(!fs::exists(f.root / "released-1"));
  }
  {
    TimestampCleanup inactive;
    assert(!inactive.healthy());
    assert(!inactive.remember(1, 1000, parent));
  }
  {
    Fixture f;
    TimestampCleanup queue;
    assert(f.open(queue));
    assert(queue.healthy());
    TimestampCleanup other;
    assert(!f.open(other));
    assert(other.busy());
    assert(queue.remember(1, 1000, parent));
    assert(queue.remember(1, 1000, parent));
    assert(f.journals() == 1);
    queue.poll();
    assert(!fs::exists(f.root / "1000.result"));
    queue.end(1);
    assert(!queue.remember(2, 1000, parent));
    assert(queue.drain(2s));
    assert(f.journals() == 0);
    assert(queue.remember(2, 1000, parent));
    assert(queue.drain(2s));
  }
  {
    Fixture f;
    {
      TimestampCleanup queue;
      assert(f.open(queue));
      assert(queue.remember(1, 1000, parent));
    }
    TimestampCleanup recovered;
    assert(f.open(recovered));
    assert(recovered.pending(1000) == 1 && recovered.pending(1001) == 0);
    assert(!recovered.remember(1, 1000, parent));
    assert(recovered.remember(1, 1000, {101, 99, 124}));
    assert(recovered.pending(1000) == 1);
    assert(f.journals() == 2);
    assert(recovered.drain(2s));
    assert(recovered.pending(1000) == 0);
    assert(f.journals() == 0);
  }
  {
    Fixture f;
    TimestampCleanup queue;
    assert(f.open(queue));
    std::ofstream(f.root / "fail").put('1');
    LogCapture log(f.root);
    assert(queue.remember(1, 1000, parent));
    queue.endAll();
    assert(!queue.drain(350ms));
    assert(f.journals() == 1);
    assert(log.text().find("attempt failed: deliberate retry fixture; journal "
                           "retained, retry scheduled") != std::string::npos);
    fs::remove(f.root / "fail");
    assert(queue.drain(2s));
    assert(f.journals() == 0);
  }
  {
    Fixture f;
    TimestampCleanup queue;
    assert(f.open(queue));
    std::ofstream(f.root / "slow").put('1');
    LogCapture log(f.root);
    int pipes[2];
    assert(pipe2(pipes, O_NONBLOCK | O_CLOEXEC) == 0);
    assert(queue.remember(1, 1000, parent));
    queue.end(1);
    queue.poll();
    close(pipes[1]);
    char byte;
    bool closed = false;
    for (int i = 0; i < 100; ++i) {
      if (read(pipes[0], &byte, 1) == 0 && !fs::exists(f.root / "slow")) {
        closed = true;
        break;
      }
      std::this_thread::sleep_for(5ms);
    }
    assert(closed);
    close(pipes[0]);
    const auto start = std::chrono::steady_clock::now();
    // The first worker consumes the slow marker before sleeping. Later retries
    // cannot inherit the delay when the worker deadline races a drain deadline.
    assert(!queue.drain(350ms));
    assert(std::chrono::steady_clock::now() - start < 1s);
    assert(f.journals() == 1);
    const auto retryStart = std::chrono::steady_clock::now();
    assert(queue.drain(3s));
    assert(std::chrono::steady_clock::now() - retryStart < 3500ms);
    assert(f.journals() == 0);
    assert(log.text().find("attempt failed: worker deadline exceeded; journal "
                           "retained, retry scheduled") != std::string::npos);
  }
  {
    Fixture f;
    TimestampCleanup queue;
    assert(f.open(queue));
    for (int i = 0; i < 256; ++i)
      assert(queue.remember(1, 1000, {100 + i, 99, 123}));
    assert(!queue.remember(1, 1000, {500, 99, 123}));
    assert(queue.remember(2, 1001, parent));
    assert(queue.drain(2s));
    for (int i = 1; i <= 64; ++i)
      assert(queue.remember(i, 1000, parent));
    assert(!queue.remember(65, 1000, parent));
    assert(queue.drain(3s));
  }
  {
    Fixture f;
    {
      TimestampCleanup queue;
      assert(f.open(queue));
      assert(queue.remember(1, 1000, parent));
    }
    for (const auto &entry : fs::directory_iterator(f.root / "cleanup"))
      if (entry.path().filename() != "lock")
        fs::resize_file(entry.path(), 1);
    TimestampCleanup queue;
    assert(!f.open(queue));
    assert(!queue.healthy());
    assert(!queue.remember(2, 1000, parent));
  }
  {
    Fixture f;
    fs::create_directory(f.root / "cleanup");
    assert(chmod((f.root / "cleanup").c_str(), 0755) == 0);
    TimestampCleanup queue;
    assert(!f.open(queue));
  }
  {
    Fixture f;
    fs::create_directory(f.root / "elsewhere");
    fs::create_directory_symlink(f.root / "elsewhere", f.root / "cleanup");
    TimestampCleanup queue;
    assert(!f.open(queue));
  }
  {
    Fixture f;
    {
      TimestampCleanup queue;
      assert(f.open(queue));
    }
    std::ofstream(f.root / "cleanup" / std::string(32, 'a')).put('x');
    assert(chmod((f.root / "cleanup" / std::string(32, 'a')).c_str(), 0644) ==
           0);
    TimestampCleanup queue;
    assert(!f.open(queue));
  }
  std::puts("cleanup queue tests passed");
}
