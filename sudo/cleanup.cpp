// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "cleanup.hpp"
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <deque>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <string>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>
namespace emaki::sudo_channel {
namespace {
using Clock = std::chrono::steady_clock;
constexpr std::size_t MaxParents = 256, MaxKeys = 64, MaxJobs = 4096;
constexpr std::size_t MaxWorkerError = 511;
constexpr std::uint64_t Magic = 0x454d414b49545331ULL;
constexpr std::uint64_t ProgressMagic = 0x454d414b49545031ULL;
struct ProgressRecord {
  std::uint64_t magic, device, inode, next, end, count;
};
struct WorkerResult {
  std::uint64_t magic = Magic;
  TimestampBatch batch;
  std::array<char, MaxWorkerError + 1> error{};
};
static_assert(sizeof(WorkerResult) < PIPE_BUF);
bool validBatch(const TimestampBatch &batch) {
  const auto &cursor = batch.cursor;
  if (cursor.next < 0 || cursor.end < cursor.next ||
      batch.heldCount > batch.held.size() || batch.retry < -1 ||
      (batch.retry >= 0 && batch.retry >= cursor.end))
    return false;
  for (std::size_t i = 0; i < batch.heldCount; ++i)
    if (batch.held[i] < 0 || batch.held[i] >= cursor.end)
      return false;
  return true;
}
struct Header {
  std::uint64_t magic;
  std::uint32_t uid, count;
};
struct ParentRecord {
  std::int64_t ppid, sid;
  std::uint64_t start;
};
bool safe(int fd, mode_t kind, mode_t mode) {
  struct stat s{};
  if (fstat(fd, &s) < 0)
    return false;
  if ((s.st_mode & S_IFMT) == kind && s.st_uid == 0 &&
      (s.st_mode & 07777) == mode && (kind != S_IFREG || s.st_nlink == 1))
    return true;
  errno = EINVAL;
  return false;
}
void failure(const char *what) {
  std::fprintf(stderr, "sudo timestamp cleanup: %s: %s\n", what,
               std::strerror(errno));
}
bool writeAll(int fd, const void *buffer, std::size_t n) {
  const auto *p = static_cast<const char *>(buffer);
  while (n) {
    ssize_t count = write(fd, p, n);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      return false;
    p += count;
    n -= static_cast<std::size_t>(count);
  }
  return true;
}
bool readAll(int fd, void *buffer, std::size_t n) {
  auto *p = static_cast<char *>(buffer);
  while (n) {
    ssize_t count = read(fd, p, n);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      return false;
    p += count;
    n -= static_cast<std::size_t>(count);
  }
  return true;
}
bool journalName(const std::string &name) {
  return name.size() == 32 && std::all_of(name.begin(), name.end(), [](char c) {
           return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
         });
}
} // namespace
struct TimestampCleanup::State {
  struct Job {
    std::uint64_t key = 0;
    uid_t uid = 0;
    std::string name;
    std::vector<TimestampParent> parents;
    bool ended = true;
    pid_t worker = -1;
    int errorRead = -1;
    unsigned failures = 0;
    TimestampCursor scan;
    std::deque<off_t> held;
    off_t workerRetry = -1;
    Clock::time_point retry{}, deadline{};
  };
  int directory = -1, lock = -1;
  bool valid = false, busy = false;
  std::string timestamps;
  std::vector<Job> jobs;
  std::size_t cursor = 0;
  ~State() {
    const auto until = Clock::now() + std::chrono::seconds(2);
    for (auto &job : jobs)
      if (job.worker > 0)
        kill(job.worker, SIGKILL);
    for (auto &job : jobs)
      if (job.worker > 0) {
        // Wait for teardown: a just-forked worker may still hold our flock.
        // Use one shared deadline so even stalled kernel I/O cannot hang stop.
        // The journal remains authoritative if a worker cannot be reaped.
        for (;;) {
          const auto result = waitpid(job.worker, nullptr, WNOHANG);
          if (result == job.worker || (result < 0 && errno == ECHILD))
            break;
          if (result < 0 && errno != EINTR) {
            failure("cannot reap killed worker; journal retained");
            break;
          }
          if (Clock::now() >= until) {
            std::fprintf(stderr,
                         "sudo timestamp cleanup: worker %ld teardown deadline; "
                         "journal retained, retry required\n",
                         static_cast<long>(job.worker));
            break;
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
      }
    for (auto &job : jobs)
      if (job.errorRead >= 0)
        close(job.errorRead);
    if (lock >= 0)
      close(lock);
    if (directory >= 0)
      close(directory);
  }
  bool save(const Job &job) {
    std::string temporary = job.name + ".tmp";
    int fd = openat(directory, temporary.c_str(),
                    O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0)
      return false;
    Header h{Magic, job.uid, static_cast<std::uint32_t>(job.parents.size())};
    bool ok = safe(fd, S_IFREG, 0600) && writeAll(fd, &h, sizeof(h));
    for (const auto &parent : job.parents) {
      ParentRecord r{parent.ppid, parent.sid, parent.start};
      if (ok)
        ok = writeAll(fd, &r, sizeof(r));
    }
    if (ok && job.ended && job.scan.inode != 0) {
      ProgressRecord progress{ProgressMagic,
                              job.scan.device,
                              job.scan.inode,
                              static_cast<std::uint64_t>(job.scan.next),
                              static_cast<std::uint64_t>(job.scan.end),
                              job.held.size()};
      ok = writeAll(fd, &progress, sizeof(progress));
      for (auto offset : job.held) {
        const auto encoded = static_cast<std::uint64_t>(offset);
        if (ok)
          ok = writeAll(fd, &encoded, sizeof(encoded));
      }
    }
    if (ok)
      ok = fsync(fd) == 0;
    close(fd);
    if (ok)
      ok = renameat(directory, temporary.c_str(), directory,
                    job.name.c_str()) == 0;
    if (ok)
      ok = fsync(directory) == 0;
    if (!ok)
      unlinkat(directory, temporary.c_str(), 0);
    return ok;
  }
};
TimestampCleanup::TimestampCleanup() = default;
TimestampCleanup::~TimestampCleanup() = default;
bool TimestampCleanup::healthy() const { return state_ && state_->valid; }
bool TimestampCleanup::busy() const { return state_ && state_->busy; }
std::size_t TimestampCleanup::pending(uid_t uid) const {
  if (!state_)
    return 0;
  return std::count_if(
      state_->jobs.begin(), state_->jobs.end(),
      [uid](const auto &job) { return job.uid == uid && job.ended; });
}
bool TimestampCleanup::initialize(const char *runtimeDirectory,
                                  const char *timestampDirectory) {
  if (state_)
    return false;
  state_ = std::make_unique<State>();
  auto &s = *state_;
  s.timestamps = timestampDirectory;
  int runtime =
      ::open(runtimeDirectory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (runtime < 0 || !safe(runtime, S_IFDIR, 0755)) {
    if (runtime >= 0)
      close(runtime);
    failure("unsafe runtime directory");
    return false;
  }
  bool made = mkdirat(runtime, "cleanup", 0700) == 0 || errno == EEXIST;
  if (made)
    s.directory = openat(runtime, "cleanup",
                         O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  close(runtime);
  if (s.directory < 0 || !safe(s.directory, S_IFDIR, 0700)) {
    failure("unsafe journal directory");
    return false;
  }
  s.lock = openat(s.directory, "lock",
                  O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (s.lock < 0 || !safe(s.lock, S_IFREG, 0600)) {
    failure("journal lock unavailable");
    return false;
  }
  if (flock(s.lock, LOCK_EX | LOCK_NB) < 0) {
    s.busy = errno == EWOULDBLOCK || errno == EAGAIN;
    failure(s.busy ? "journal busy; another cleanup owner still holds the lock; "
                     "retry required"
                   : "journal lock unavailable");
    return false;
  }
  int copy = dup(s.directory);
  DIR *dir = copy >= 0 ? fdopendir(copy) : nullptr;
  if (!dir) {
    const int saved = errno;
    if (copy >= 0)
      close(copy);
    errno = saved;
    failure("cannot read journals");
    return false;
  }
  bool ok = true;
  std::size_t entries = 0;
  errno = 0;
  while (auto *entry = readdir(dir)) {
    std::string name(entry->d_name);
    if (name == "." || name == ".." || name == "lock")
      continue;
    if (++entries > MaxJobs * 2) {
      ok = false;
      break;
    }
    // Interrupted atomic writes cannot represent a grant: the preceding journal
    // remains authoritative. Validate and discard only our private temporary
    // file.
    bool temporary = name.size() == 36 && name.ends_with(".tmp") &&
                     journalName(name.substr(0, 32));
    if ((!journalName(name) && !temporary) || s.jobs.size() >= MaxJobs) {
      ok = false;
      break;
    }
    int fd = openat(s.directory, name.c_str(),
                    O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    struct stat st{};
    if (fd < 0 || !safe(fd, S_IFREG, 0600) || fstat(fd, &st) < 0) {
      if (fd >= 0)
        close(fd);
      ok = false;
      break;
    }
    if (temporary) {
      close(fd);
      if (unlinkat(s.directory, name.c_str(), 0) < 0) {
        ok = false;
        break;
      }
      continue;
    }
    Header h{};
    State::Job job;
    ok = readAll(fd, &h, sizeof(h)) && h.magic == Magic && h.count > 0 &&
         h.count <= MaxParents &&
         st.st_size >=
             static_cast<off_t>(sizeof(h) + h.count * sizeof(ParentRecord));
    job.uid = h.uid;
    job.name = name;
    for (std::uint32_t i = 0; ok && i < h.count; ++i) {
      ParentRecord r{};
      ok = readAll(fd, &r, sizeof(r)) && r.ppid > 0 && r.ppid <= INT32_MAX &&
           r.sid > 0 && r.sid <= INT32_MAX && r.start > 0;
      if (ok)
        job.parents.push_back(
            {static_cast<pid_t>(r.ppid), static_cast<pid_t>(r.sid), r.start});
    }
    const auto base =
        static_cast<off_t>(sizeof(h) + h.count * sizeof(ParentRecord));
    if (ok && st.st_size > base) {
      ProgressRecord progress{};
      const auto extra = static_cast<std::uint64_t>(st.st_size - base);
      ok = extra >= sizeof(progress) &&
           readAll(fd, &progress, sizeof(progress)) &&
           progress.magic == ProgressMagic && progress.inode != 0 &&
           progress.next <= progress.end && progress.end <= INT64_MAX &&
           progress.device == static_cast<dev_t>(progress.device) &&
           progress.inode == static_cast<ino_t>(progress.inode) &&
           (extra - sizeof(progress)) % sizeof(std::uint64_t) == 0 &&
           progress.count == (extra - sizeof(progress)) / sizeof(std::uint64_t);
      if (ok)
        job.scan = {static_cast<dev_t>(progress.device),
                    static_cast<ino_t>(progress.inode),
                    static_cast<off_t>(progress.next),
                    static_cast<off_t>(progress.end)};
      for (std::uint64_t i = 0; ok && i < progress.count; ++i) {
        std::uint64_t offset;
        ok = readAll(fd, &offset, sizeof(offset)) && offset < progress.end;
        if (ok)
          job.held.push_back(static_cast<off_t>(offset));
      }
    }
    close(fd);
    if (!ok)
      break;
    std::size_t parents = job.parents.size(), keys = 1;
    for (const auto &other : s.jobs)
      if (other.uid == job.uid) {
        parents += other.parents.size();
        ++keys;
      }
    if (parents > MaxParents || keys > MaxKeys) {
      ok = false;
      break;
    }
    s.jobs.push_back(std::move(job));
    errno = 0;
  }
  if (errno != 0)
    ok = false;
  closedir(dir);
  if (!ok) {
    if (errno == 0)
      errno = EINVAL;
    failure("invalid cleanup journal; grants disabled");
    return false;
  }
  s.valid = true;
  return true;
}
bool TimestampCleanup::remember(std::uint64_t key, uid_t uid,
                                TimestampParent parent) {
  if (!healthy() || parent.ppid <= 0 || parent.sid <= 0 || !parent.start)
    return false;
  auto &s = *state_;
  State::Job *found = nullptr;
  std::size_t parents = 0, keys = 0;
  for (auto &job : s.jobs) {
    if (job.uid != uid)
      continue;
    parents += job.parents.size();
    ++keys;
    if (job.ended && std::find(job.parents.begin(), job.parents.end(),
                               parent) != job.parents.end())
      return false;
    if (!job.ended && job.key == key)
      found = &job;
  }
  if (found && std::find(found->parents.begin(), found->parents.end(),
                         parent) != found->parents.end())
    return true;
  if (parents >= MaxParents ||
      (!found && (keys >= MaxKeys || s.jobs.size() >= MaxJobs))) {
    std::fprintf(stderr,
                 "sudo timestamp cleanup: uid %u journal quota reached\n", uid);
    return false;
  }
  State::Job candidate;
  if (found)
    candidate = *found;
  else {
    std::array<unsigned char, 16> random{};
    if (getrandom(random.data(), random.size(), 0) !=
        static_cast<ssize_t>(random.size())) {
      failure("journal name entropy");
      return false;
    }
    constexpr char hex[] = "0123456789abcdef";
    for (auto b : random) {
      candidate.name += hex[b >> 4];
      candidate.name += hex[b & 15];
    }
    struct stat st{};
    if (fstatat(s.directory, candidate.name.c_str(), &st,
                AT_SYMLINK_NOFOLLOW) == 0 ||
        errno != ENOENT)
      return false;
    candidate.key = key;
    candidate.uid = uid;
    candidate.ended = false;
  }
  candidate.parents.push_back(parent);
  if (!s.save(candidate)) {
    failure("cannot persist served parent");
    s.valid = false;
    return false;
  }
  if (found)
    *found = std::move(candidate);
  else
    s.jobs.push_back(std::move(candidate));
  return true;
}
void TimestampCleanup::end(std::uint64_t key) {
  if (!state_)
    return;
  for (auto &job : state_->jobs)
    if (!job.ended && job.key == key)
      job.ended = true;
}
void TimestampCleanup::endAll() {
  if (!state_)
    return;
  for (auto &job : state_->jobs)
    job.ended = true;
}
void TimestampCleanup::poll() {
  if (!state_)
    return;
  auto &s = *state_;
  const auto now = Clock::now();
  for (auto it = s.jobs.begin(); it != s.jobs.end();) {
    auto &job = *it;
    if (job.worker <= 0) {
      ++it;
      continue;
    }
    int status = 0;
    pid_t result = waitpid(job.worker, &status, WNOHANG);
    if (result == 0) {
      if (now >= job.deadline) {
        kill(job.worker, SIGKILL);
      }
      ++it;
      continue;
    }
    if (result < 0 && errno == EINTR) {
      ++it;
      continue;
    }
    const int waitError = errno;
    job.worker = -1;
    WorkerResult response{};
    ssize_t length;
    do {
      length = read(job.errorRead, &response, sizeof(response));
    } while (length < 0 && errno == EINTR);
    close(job.errorRead);
    job.errorRead = -1;
    std::string reason;
    if (result < 0)
      reason = std::string("cannot reap worker: ") + std::strerror(waitError);
    else if (WIFSIGNALED(status)) {
      const auto signal = WTERMSIG(status);
      if (signal == SIGALRM || (signal == SIGKILL && now >= job.deadline))
        reason = "worker deadline exceeded";
      else
        reason = "worker terminated by signal " + std::to_string(signal);
    } else if (!WIFEXITED(status))
      reason = "unexpected worker status";
    else if (length != static_cast<ssize_t>(sizeof(response)) ||
             response.magic != Magic || !validBatch(response.batch))
      reason = "invalid worker result";
    else {
      const auto &batch = response.batch;
      const auto &cursor = batch.cursor;
      // A failed batch returns its input cursor and retry without new offsets.
      // Only a completed bounded pass publishes progress across workers.
      // Offsets belong only to the scanned inode and captured extent. A
      // replacement, removal or truncation makes all old lock retries stale.
      bool reset = cursor.device != job.scan.device ||
                   cursor.inode != job.scan.inode ||
                   cursor.end < job.scan.end || cursor.next < job.scan.next;
      if (reset)
        job.held.clear();
      else if (job.workerRetry >= 0 && batch.retry == -1 && !job.held.empty())
        job.held.pop_front();
      job.scan = cursor;
      for (std::size_t i = 0; i < batch.heldCount; ++i)
        job.held.push_back(batch.held[i]);
      if (WEXITSTATUS(status) != 0)
        reason =
            response.error[0]
                ? std::string(response.error.data(),
                              strnlen(response.error.data(), MaxWorkerError))
                : "worker exited with status " +
                      std::to_string(WEXITSTATUS(status));
      else if (job.scan.next != job.scan.end || !job.held.empty()) {
        // A bounded scan is useful progress, not a failed cleanup attempt.
        if (s.save(job)) {
          job.failures = 0;
          job.retry = now;
          ++it;
          continue;
        }
        s.valid = false;
        reason = std::string("cannot persist cleanup progress: ") +
                 std::strerror(errno);
      } else if (unlinkat(s.directory, job.name.c_str(), 0) < 0)
        reason = std::string("cannot remove completed journal: ") +
                 std::strerror(errno);
      else {
        if (fsync(s.directory) < 0)
          failure("journal directory sync after cleanup");
        it = s.jobs.erase(it);
        continue;
      }
    }
    // Even a killed or failed worker may be waiting for just one lock. Give
    // every other held record its turn before retrying that record again.
    if (job.workerRetry >= 0 && !job.held.empty() &&
        job.held.front() == job.workerRetry) {
      job.held.push_back(job.held.front());
      job.held.pop_front();
    }
    if (!s.save(job)) {
      s.valid = false;
      reason += std::string("; cannot persist retry progress: ") +
                std::strerror(errno);
    }
    ++job.failures;
    job.retry = job.held.size() > 1
                    ? now
                    : now + std::chrono::milliseconds(std::min(
                                5000U, 100U << std::min(job.failures, 5U)));
    std::fprintf(stderr,
                 "sudo timestamp cleanup: uid %u attempt failed: %s; journal "
                 "retained, retry scheduled\n",
                 job.uid, reason.c_str());
    ++it;
  }
  std::size_t running = 0;
  for (const auto &job : s.jobs)
    if (job.worker > 0)
      ++running;
  if (s.jobs.empty())
    return;
  const auto count = s.jobs.size();
  const auto first = s.cursor % count;
  for (std::size_t i = 0; i < count && running < 4; ++i) {
    const auto index = (first + i) % count;
    auto &job = s.jobs[index];
    if (!job.ended || job.worker > 0 || now < job.retry)
      continue;
    if (std::any_of(s.jobs.begin(), s.jobs.end(), [&](const auto &other) {
          return other.uid == job.uid && other.worker > 0;
        }))
      continue;
    TimestampBatch batch;
    batch.cursor = job.scan;
    if (job.scan.next == job.scan.end && !job.held.empty())
      batch.retry = job.held.front();
    int errors[2];
    if (pipe2(errors, O_CLOEXEC | O_NONBLOCK) < 0) {
      failure("cannot create worker result pipe; retry scheduled");
      job.retry = now + std::chrono::seconds(1);
      continue;
    }
    pid_t child = fork();
    if (child < 0) {
      failure("cannot start worker; retry scheduled");
      close(errors[0]);
      close(errors[1]);
      job.retry = now + std::chrono::seconds(1);
      continue;
    }
    if (child == 0) {
      close(errors[0]);
      WorkerResult response;
      response.batch = batch;
      auto report = [&](int fd, const std::string &error) {
        std::memcpy(response.error.data(), error.data(),
                    std::min(error.size(), MaxWorkerError));
        return writeAll(fd, &response, sizeof(response));
      };
      // Keep only a bounded, nonblocking result pipe in addition to stdio.
      // The parent logs its message on the same line as the retry decision.
      if (errors[1] != 3 && dup2(errors[1], 3) < 0) {
        const std::string error =
            std::string("cannot preserve worker result pipe: ") +
            std::strerror(errno);
        report(errors[1], error);
        _exit(2);
      }
      // Never keep a listening socket, connected client, or journal lock alive.
      if (syscall(SYS_close_range, 4U, ~0U, 0U) < 0) {
        const std::string error =
            std::string("cannot close inherited worker descriptors: ") +
            std::strerror(errno);
        report(3, error);
        _exit(2);
      }
      alarm(2);
      std::string error;
      bool ok = disableTimestampBatch(s.timestamps.c_str(), job.uid,
                                      job.parents, response.batch,
                                      std::chrono::milliseconds(1800), error);
      if (!ok && error.empty())
        error = "timestamp invalidation failed without a reason";
      if (!report(3, error))
        _exit(2);
      _exit(ok ? 0 : 1);
    }
    close(errors[1]);
    job.worker = child;
    job.errorRead = errors[0];
    job.workerRetry = batch.retry;
    job.deadline = now + std::chrono::seconds(2);
    ++running;
    s.cursor = (index + 1) % count;
  }
}
bool TimestampCleanup::drain(std::chrono::milliseconds timeout) {
  if (!state_)
    return false;
  endAll();
  const auto until = Clock::now() + timeout;
  do {
    poll();
    if (state_->jobs.empty())
      return healthy();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  } while (Clock::now() < until);
  std::fprintf(stderr,
               "sudo timestamp cleanup: drain deadline; %zu journals retained "
               "for retry\n",
               state_->jobs.size());
  return false;
}
} // namespace emaki::sudo_channel
