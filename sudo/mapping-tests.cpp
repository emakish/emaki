// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <cassert>
#include <charconv>
#include <fcntl.h>
#include <map>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
namespace {
std::map<int, std::string> paths;
std::string unsafePath;
unsigned mutation = 0;
unsigned mappedMutation = 0;
int mappingOpen(const char *path, int flags, ...) {
  if (std::string(path).find("/map_files/") != std::string::npos) {
    assert((flags & O_PATH) && !(flags & O_NOFOLLOW));
    if (mappedMutation == 3) {
      errno = EPERM;
      return -1;
    }
  }
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(fd >= 0);
  paths[fd] = path;
  return fd;
}
int mappingOpenAt(int parent, const char *path, int flags, ...) {
  assert(flags & O_NOFOLLOW);
  const auto full = paths.at(parent) + (paths.at(parent) == "/" ? "" : "/") + path;
  return mappingOpen(full.c_str(), flags);
}
int mappingFstat(int fd, struct stat *st) {
  const auto &path = paths.at(fd);
  *st = {};
  const bool mapped = path.find("/map_files/") != std::string::npos;
  st->st_mode = path.ends_with(".so") || mapped ? S_IFREG | 0644 : S_IFDIR | 0755;
  st->st_uid = 0;
  st->st_dev = makedev(8, 1);
  st->st_ino = 42;
  if (mapped) {
    if (mappedMutation == 1) st->st_dev = makedev(8, 2);
    if (mappedMutation == 2) st->st_ino = 43;
    if (mappedMutation == 4) st->st_mode = S_IFDIR | 0755;
  }
  if (path == unsafePath) {
    if (mutation == 0) st->st_uid = 1000;
    if (mutation == 1) st->st_mode |= 0020;
    if (mutation == 2) st->st_mode |= 0002;
    if (mutation == 3) st->st_mode = S_IFLNK | 0777;
  }
  return 0;
}
}
#define main brokerProductionMain
#define open(...) mappingOpen(__VA_ARGS__)
#define openat(...) mappingOpenAt(__VA_ARGS__)
#define fstat(...) mappingFstat(__VA_ARGS__)
#include "broker.cpp"
#undef main
#undef open
#undef openat
#undef fstat
int main() {
  const auto installed = std::string(EMAKI_SHELL_PATH) + "/Emaki/Sudo/libemakisudoplugin.so";
  const std::string generation = "/var/lib/emaki/shell-generations/23/shell/Emaki/Sudo/libemakisudoplugin.so";
  auto mapping = [](const std::string &path, const std::string &device = "08:01",
                    const std::string &inode = "42") {
    return "1000-2000 r-xp 0 " + device + " " + inode + " " + path + "\n";
  };
  assert(pluginMappings(123, mapping(installed)));
  assert(pluginMappings(123, mapping(generation)));
  assert(pluginMappings(123, mapping(installed) + mapping(generation)));
  assert(!pluginMappings(123, ""));
  assert(!pluginMappings(123, mapping("/tmp/libemakisudoplugin.so")));
  assert(!pluginMappings(123, mapping(installed) + mapping("/tmp/libemakisudoplugin.so")));
  // maps uses the superblock device on btrfs; descriptor identity is decisive.
  assert(pluginMappings(123, mapping(installed, "00:1c")));
  for (mappedMutation = 1; mappedMutation <= 4; ++mappedMutation)
    assert(!pluginMappings(123, mapping(installed)));
  mappedMutation = 0;
  for (const auto *range : {"", "../maps", "0-0", "2000-1000", "1-2/3", "1-2junk",
                           "10000000000000000-20000000000000000"})
    assert(!secureMappedFile(123, range, installed));
  assert(!pluginMappings(123, mapping(installed + " (deleted)")));
  assert(!pluginMappings(123, mapping(std::string(EMAKI_SHELL_PATH) + "/../libemakisudoplugin.so")));
  for (const auto &path : {std::string("/"), std::string("/var"),
                          std::string("/var/lib/emaki/shell-generations"), generation}) {
    unsafePath = path;
    for (mutation = 0; mutation < 4; ++mutation)
      assert(!pluginMappings(123, mapping(generation)));
  }
  unsafePath.clear();
  // The PAM module uses the same descriptor comparison as the provider.
  assert(secureMappedFile(123, "1000-2000", EMAKI_PAM_MODULE));
  mappedMutation = 2;
  assert(!secureMappedFile(123, "1000-2000", EMAKI_PAM_MODULE));
  puts("Provider and module mapping descriptor provenance checks passed");
}
