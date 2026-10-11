// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "warning.hpp"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
namespace fs = std::filesystem;
extern "C" int __real_fstat(int, struct stat *);
// Some unprivileged test namespaces expose the host's root directories as the
// overflow uid. Only translate that observed filesystem-root owner for fixtures.
static uid_t fixtureRootOwner = 0;
static ino_t fixtureMarkerInode = 0;
static bool remounted = false;
extern "C" int __wrap_fstat(int fd, struct stat *st) {
  const int result = __real_fstat(fd, st);
  if (!result && S_ISDIR(st->st_mode) && st->st_uid == fixtureRootOwner)
    st->st_uid = 0;
  if (!result && remounted && S_ISREG(st->st_mode) && st->st_ino == fixtureMarkerInode)
    ++st->st_dev;
  return result;
}

int main() {
  struct stat filesystemRoot{};
  assert(stat("/", &filesystemRoot) == 0);
  fixtureRootOwner = filesystemRoot.st_uid;
  std::string pattern = (fs::temp_directory_path() / "emaki-warning-XXXXXX").string();
  assert(mkdtemp(pattern.data()));
  const fs::path root(pattern), state = root / "state", marker = root / "marker";
  assert(mkdir(state.c_str(), 0700) == 0);
  std::ofstream(marker) << "installed\n";
  assert(chmod(marker.c_str(), 0644) == 0);
  const auto owner = getuid();
  const uid_t first = 1001, second = 1002;
  WarningAcceptance store(state, marker, owner);
  assert(!store.accepted(first));
  auto child = fork();
  assert(child >= 0);
  if (!child)
    _exit(store.acknowledge(first) ? 0 : 1);
  int status = 0;
  assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
  // A separately constructed broker sees acceptance written by an exited one.
  WarningAcceptance restarted(state, marker, owner);
  assert(restarted.accepted(first));
  // Btrfs may allocate a different anonymous device after a reboot/remount.
  struct stat markerIdentity{};
  assert(stat(marker.c_str(), &markerIdentity) == 0);
  fixtureMarkerInode = markerIdentity.st_ino;
  remounted = true;
  assert(restarted.accepted(first));
  remounted = false;
  assert(!restarted.accepted(second));
  assert(restarted.acknowledge(second));
  const auto record = state / "1001.warning";
  assert(fs::remove(record));
  assert(!restarted.accepted(first));
  assert(restarted.accepted(second));
  assert(restarted.acknowledge(first));
  // The first pre-upgrade scriptlet preserves the legacy epoch verbatim.
  const auto generation = state / "installation";
  fs::copy_file(record, generation);
  assert(chmod(generation.c_str(), 0600) == 0);
  std::ofstream(root / "upgrade") << "upgraded package\n";
  fs::rename(root / "upgrade", marker);
  assert(restarted.accepted(first) && restarted.accepted(second));
  // Upgrading again, even by overwriting the marker, keeps that generation.
  std::ofstream(marker) << "next package\n";
  assert(restarted.accepted(first) && restarted.accepted(second));
  assert(fs::remove(marker));
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  std::ofstream(marker) << "installed\n";
  assert(restarted.accepted(first));
  assert(chmod(generation.c_str(), 0666) == 0);
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  assert(chmod(generation.c_str(), 0600) == 0);
  fs::create_hard_link(generation, state / "generation-link");
  assert(!restarted.accepted(first));
  fs::remove(state / "generation-link");
  std::ofstream(generation) << "invalid\n";
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  fs::remove(generation);
  fs::create_symlink(record, generation);
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  fs::remove(generation);
  // Removal drops the generation; a fresh install uses the new marker identity.
  assert(!restarted.accepted(first) && !restarted.accepted(second));
  assert(restarted.acknowledge(first) && restarted.acknowledge(second));
  // Package extraction can replace the inode while preserving file timestamps.
  const auto stamp = fs::last_write_time(marker);
  std::ofstream(root / "replacement") << "installed\n";
  fs::last_write_time(root / "replacement", stamp);
  fs::rename(root / "replacement", marker);
  assert(!restarted.accepted(first) && !restarted.accepted(second));
  assert(restarted.acknowledge(first));
  // It can also truncate and overwrite the existing inode.
  struct stat before{}, after{};
  assert(stat(marker.c_str(), &before) == 0);
  bool changed = false;
  for (unsigned attempt = 0; attempt < 200 && !changed; ++attempt) {
    // Some filesystems update ctime only on a clock tick. Wait for an actual
    // metadata change instead of assuming that one millisecond is sufficient.
    usleep(10000);
    std::ofstream(marker) << "installed\n";
    fs::last_write_time(marker, stamp);
    assert(stat(marker.c_str(), &after) == 0);
    changed = before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
              before.st_ctim.tv_nsec != after.st_ctim.tv_nsec;
  }
  assert(changed && !restarted.accepted(first));
  assert(restarted.acknowledge(first));
  assert(fs::remove(marker));
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  std::ofstream(marker) << "installed\n";
  assert(restarted.acknowledge(first));
  assert(chmod(record.c_str(), 0666) == 0);
  assert(!restarted.accepted(first));
  assert(restarted.acknowledge(first));
  fs::create_hard_link(record, state / "linked");
  assert(!restarted.accepted(first));
  fs::remove(state / "linked");
  fs::remove(record);
  fs::create_symlink(marker, record);
  assert(!restarted.accepted(first));
  assert(restarted.acknowledge(first));
  assert(!fs::is_symlink(record));
  std::ofstream(record, std::ios::app) << "corrupt";
  assert(!restarted.accepted(first));
  assert(restarted.acknowledge(first));
  assert(chmod(state.c_str(), 0777) == 0);
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  assert(chmod(state.c_str(), 0700) == 0);
  fs::create_directory_symlink(state, root / "state-link");
  WarningAcceptance unsafe(root / "state-link", marker, owner);
  assert(!unsafe.accepted(first) && !unsafe.acknowledge(first));
  fs::create_directory_symlink(root, root / "parent-link");
  WarningAcceptance unsafeParent(root / "parent-link/state", marker, owner);
  assert(!unsafeParent.accepted(first) && !unsafeParent.acknowledge(first));
  WarningAcceptance wrongOwner(state, marker, owner == 0 ? 1 : 0);
  assert(!wrongOwner.accepted(first) && !wrongOwner.acknowledge(first));
  fs::rename(marker, root / "real-marker");
  fs::create_symlink(root / "real-marker", marker);
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  fs::remove(marker);
  assert(mkfifo(marker.c_str(), 0600) == 0);
  assert(!restarted.accepted(first) && !restarted.acknowledge(first));
  fs::remove_all(root);
  puts("Persistent warning acceptance checks passed");
}
