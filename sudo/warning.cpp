// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "warning.hpp"
#include "core.hpp"
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace emaki::sudo_channel {
namespace {
Fd directory(const std::string &path, uid_t owner, bool privateLeaf) {
  if (path.empty() || path.front() != '/')
    return {};
  Fd current(open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  std::size_t begin = 1;
  while (current && begin < path.size()) {
    auto end = path.find('/', begin);
    auto component = path.substr(begin, end - begin);
    if (component.empty() || component == "." || component == "..")
      return {};
    current.reset(openat(current.get(), component.c_str(),
                         O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    struct stat st{};
    if (!current || fstat(current.get(), &st) ||
        (st.st_uid != 0 && st.st_uid != owner) ||
        ((st.st_mode & 0022) && !(st.st_uid == 0 && (st.st_mode & S_ISVTX))))
      return {};
    if (end == std::string::npos) {
      if (privateLeaf && (st.st_uid != owner || (st.st_mode & 0777) != 0700))
        return {};
      return current;
    }
    begin = end + 1;
  }
  return {};
}
bool record(int fd, uid_t owner) {
  struct stat st{};
  return fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) &&
         st.st_uid == owner && (st.st_mode & 0777) == 0600 && st.st_nlink == 1;
}
std::string contents(int fd) {
  std::array<char, 256> bytes{};
  std::size_t used = 0;
  for (;;) {
    const auto count = read(fd, bytes.data() + used, bytes.size() - used);
    if (count < 0 && errno == EINTR)
      continue;
    if (count < 0)
      return {};
    if (count == 0)
      return {bytes.data(), used};
    used += count;
    if (used == bytes.size())
      return {};
  }
}
}
WarningAcceptance::WarningAcceptance(std::string directory, std::string marker,
                                     uid_t owner)
    : directory_(std::move(directory)), marker_(std::move(marker)), owner_(owner) {}

std::string WarningAcceptance::epoch() const {
  const auto slash = marker_.rfind('/');
  if (slash == std::string::npos || slash + 1 == marker_.size())
    return {};
  auto parent = directory(marker_.substr(0, slash), owner_, false);
  if (!parent)
    return {};
  Fd file(openat(parent.get(), marker_.substr(slash + 1).c_str(),
                 O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
  struct stat st{};
  if (!file || fstat(file.get(), &st) || !S_ISREG(st.st_mode) ||
      st.st_uid != owner_ || (st.st_mode & 0022))
    return {};
  // The package scriptlet keeps this epoch across upgrades and seeds it from
  // the old marker before the first upgrade, preserving existing acceptance.
  auto state = directory(directory_, owner_, true);
  if (!state)
    return {};
  Fd generation(openat(state.get(), "installation",
                       O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
  if (generation) {
    if (!record(generation.get(), owner_))
      return {};
    const auto value = contents(generation.get());
    // Version one is the original marker identity. Refuse malformed state.
    if (!value.starts_with("1 ") || value.back() != '\n')
      return {};
    unsigned spaces = 0;
    bool digit = false;
    for (std::size_t i = 2; i + 1 < value.size(); ++i) {
      if (value[i] >= '0' && value[i] <= '9') {
        digit = true;
      } else if (value[i] == ' ' && digit) {
        ++spaces;
        digit = false;
      } else {
        return {};
      }
    }
    return digit && spaces == 2 ? value : std::string{};
  }
  if (errno != ENOENT)
    return {};
  // Legacy installations have no generation until their first upgrade.
  // ctime is set by the filesystem even when a reinstall preserves archive
  // timestamps or overwrites the same inode. Do not persist st_dev: btrfs
  // assigns anonymous subvolume devices at mount time, so it can change on boot.
  // The marker path is fixed and root-controlled, as is the acceptance record.
  return "1 " + std::to_string(st.st_ino) +
         " " + std::to_string(st.st_ctim.tv_sec) + " " +
         std::to_string(st.st_ctim.tv_nsec) + "\n";
}

bool WarningAcceptance::accepted(uid_t uid) const {
  const auto installed = epoch();
  auto state = directory(directory_, owner_, true);
  if (installed.empty() || !state)
    return false;
  Fd file(openat(state.get(), (std::to_string(uid) + ".warning").c_str(),
                 O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC));
  if (!record(file.get(), owner_))
    return false;
  return contents(file.get()) == installed && epoch() == installed;
}

bool WarningAcceptance::acknowledge(uid_t uid) const {
  const auto installed = epoch();
  auto state = directory(directory_, owner_, true);
  if (installed.empty() || !state)
    return false;
  const auto name = std::to_string(uid) + ".warning";
  const auto temporary = name + ".new";
  // A stale private staging file can survive a crash before rename.
  if (unlinkat(state.get(), temporary.c_str(), 0) && errno != ENOENT)
    return false;
  Fd file(openat(state.get(), temporary.c_str(),
                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
  if (!record(file.get(), owner_))
    return false;
  std::size_t written = 0;
  while (written < installed.size()) {
    auto count = write(file.get(), installed.data() + written, installed.size() - written);
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0) {
      unlinkat(state.get(), temporary.c_str(), 0);
      return false;
    }
    written += count;
  }
  if (fsync(file.get()) || epoch() != installed ||
      renameat(state.get(), temporary.c_str(), state.get(), name.c_str())) {
    unlinkat(state.get(), temporary.c_str(), 0);
    return false;
  }
  if (fsync(state.get())) {
    unlinkat(state.get(), name.c_str(), 0);
    return false;
  }
  return accepted(uid);
}
}
