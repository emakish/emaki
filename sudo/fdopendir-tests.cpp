// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "cleanup.hpp"
#include "core.hpp"
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <dirent.h>
#include <fcntl.h>
#include <filesystem>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

using namespace emaki::sudo_channel;
namespace {
bool failDirectory = false;
bool rootFixture = false;
unsigned failures = 0;
}

extern "C" DIR *__real_fdopendir(int);
extern "C" DIR *__wrap_fdopendir(int fd) {
  if (failDirectory) {
    ++failures;
    errno = ENOMEM;
    return nullptr;
  }
  return __real_fdopendir(fd);
}
extern "C" int __real_fstat(int, struct stat *);
extern "C" int __wrap_fstat(int fd, struct stat *st) {
  int result = __real_fstat(fd, st);
  // Cleanup fixtures belong to the unprivileged runner; production checks root.
  if (result == 0 && rootFixture && st->st_uid == getuid())
    st->st_uid = 0;
  return result;
}

namespace {
std::size_t descriptorCount() {
  DIR *directory = opendir("/proc/self/fd");
  assert(directory);
  std::size_t count = 0;
  while (auto *entry = readdir(directory))
    if (entry->d_name[0] != '.')
      ++count;
  assert(closedir(directory) == 0);
  return count;
}

bool check(const std::filesystem::path &root, bool cleanup) {
  assert(chmod(root.c_str(), cleanup ? 0755 : 0700) == 0);
  Fd runtime(open(root.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
  assert(runtime);
  const auto before = descriptorCount();
  const auto injectedBefore = failures;
  for (unsigned attempt = 0; attempt < 5; ++attempt) {
    failDirectory = true;
    if (cleanup) {
      rootFixture = true;
      TimestampCleanup queue;
      assert(!queue.initialize(root.c_str(), root.c_str()));
      assert(!queue.healthy());
      rootFixture = false;
    } else {
      bool unavailable = false;
      assert(authenticationBlocked(runtime.get(), getuid(), &unavailable));
      assert(unavailable);
    }
    failDirectory = false;
  }
  assert(failures == injectedBefore + 5);
  const auto after = descriptorCount();
  std::printf("%s ENOMEM descriptor count: %zu -> %zu\n",
              cleanup ? "cleanup" : "authentication", before, after);
  return before == after;
}
}

int main(int argc, char **argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  assert(mode == "authentication" || mode == "cleanup");
  std::string pattern =
      (std::filesystem::temp_directory_path() / "emaki-fdopendir-XXXXXX").string();
  char *created = mkdtemp(pattern.data());
  assert(created);
  const std::filesystem::path root(created);
  const bool ok = check(root, mode == "cleanup");
  std::filesystem::remove_all(root);
  return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
