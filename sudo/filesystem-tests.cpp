// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the elevated discovery path inside a disposable user namespace.
#include "core.hpp"
#include <cassert>
#include <cstdio>
#include <filesystem>
#include <sys/wait.h>
#define main brokerProductionMain
#include "broker.cpp"
#undef main
using namespace emaki::sudo_channel;

namespace {
unsigned effectiveCapabilities() {
  __user_cap_header_struct header{_LINUX_CAPABILITY_VERSION_3, 0};
  __user_cap_data_struct capabilities[2]{};
  assert(syscall(SYS_capget, &header, capabilities) == 0);
  return capabilities[0].effective;
}
Fd listenAt(const std::string &path) {
  Fd fd(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
  assert(fd);
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  assert(path.size() < sizeof(address.sun_path));
  memcpy(address.sun_path, path.c_str(), path.size() + 1);
  assert(bind(fd.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
  assert(listen(fd.get(), 8) == 0);
  return fd;
}
int connectError(const std::string &path) {
  Fd fd(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
  assert(fd);
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  assert(path.size() < sizeof(address.sun_path));
  memcpy(address.sun_path, path.c_str(), path.size() + 1);
  return connect(fd.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0
      ? 0 : errno;
}
}

int main() {
  assert(getuid() == 0 && geteuid() == 0);
  constexpr uid_t uid = 2;
  const auto *account = getpwuid(uid);
  if (!account) {
    puts("SKIP: namespace test requires a mapped non-root account with uid 2");
    return 77;
  }
  const gid_t gid = account->pw_gid;
  const gid_t root = 0;
  assert(setgroups(1, &root) == 0);
  char pattern[] = "/tmp/emaki-filesystem-XXXXXX";
  const char *created = mkdtemp(pattern);
  assert(created);
  const std::string base(created), runtime = base + "/runtime", secret = base + "/secret";
  assert(chmod(base.c_str(), 0755) == 0);
  assert(mkdir(runtime.c_str(), 0700) == 0);
  if (chown(runtime.c_str(), uid, gid) != 0) {
    std::filesystem::remove_all(base);
    puts("SKIP: namespace does not map the test account");
    return 77;
  }
  assert(mkdir(secret.c_str(), 0700) == 0);
  auto listener = listenAt(secret + "/socket");
  assert(chmod((secret + "/socket").c_str(), 0600) == 0);
  const auto link = runtime + "/niri.private.sock";
  assert(symlink((secret + "/socket").c_str(), link.c_str()) == 0);
  assert(lchown(link.c_str(), uid, gid) == 0);
  const auto capabilities = effectiveCapabilities();
  assert(capabilities & (1U << CAP_DAC_OVERRIDE));
  assert(connectError(secret + "/socket") == 0);
  { Fd connection(accept(listener.get(), nullptr, nullptr)); assert(connection); }
  {
    SessionFilesystem scope(uid);
    assert(scope);
    assert(static_cast<uid_t>(setfsuid(static_cast<uid_t>(-1))) == uid);
    assert(static_cast<gid_t>(setfsgid(static_cast<gid_t>(-1))) == gid);
    assert(getgroups(0, nullptr) == 0);
    assert(!(effectiveCapabilities() &
        ((1U << CAP_DAC_OVERRIDE) | (1U << CAP_DAC_READ_SEARCH))));
    assert(connectError(secret + "/socket") == EACCES);
    assert(connectError(link) == EACCES);
  }
  auto restored = [&] {
    assert(setfsuid(static_cast<uid_t>(-1)) == 0);
    assert(setfsgid(static_cast<gid_t>(-1)) == 0);
    gid_t group = static_cast<gid_t>(-1);
    assert(getgroups(1, &group) == 1 && group == root);
    assert(effectiveCapabilities() == capabilities);
  };
  restored();
  int ready[2];
  assert(pipe(ready) == 0);
  const pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    close(ready[0]);
    assert(setgroups(0, nullptr) == 0 && setgid(gid) == 0 && setuid(uid) == 0);
    auto controller = listenAt(runtime + "/niri.real.sock");
    assert(write(ready[1], "r", 1) == 1);
    close(ready[1]);
    auto self = inspectProcess(getpid());
    assert(self);
    const auto reply = "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":" +
        std::to_string(self->pid) + ",\"start_time\":" + std::to_string(self->start) + "}}}\n";
    for (unsigned requestIndex = 0; requestIndex < 2; ++requestIndex) {
      Fd connection(accept(controller.get(), nullptr, nullptr));
      assert(connection);
      char request[64];
      assert(recv(connection.get(), request, sizeof(request), 0) > 0);
      assert(send(connection.get(), reply.data(), reply.size(), MSG_NOSIGNAL) ==
          static_cast<ssize_t>(reply.size()));
      // Keep the pinned identity alive until the parent finishes checking it.
      assert(recv(connection.get(), request, sizeof(request), 0) == 0);
    }
    _exit(0);
  }
  close(ready[1]);
  char byte;
  assert(read(ready[0], &byte, 1) == 1);
  close(ready[0]);
  auto controller = inspectProcess(child);
  assert(controller);
  assert(trustedShellIdentityAt(*controller, *controller, uid, runtime) == IdentityResult::Trusted);
  restored();
  for (unsigned index = 0; index < 300; ++index) {
    const auto path = runtime + "/application-" + std::to_string(index);
    Fd file(open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
    assert(file && fchown(file.get(), uid, gid) == 0);
  }
  // An expensive refused check in another session has no shared allowance
  // to consume before this session checks its genuine controller.
  const auto costly = base + "/costly";
  assert(mkdir(costly.c_str(), 0700) == 0);
  constexpr uid_t otherUid = 1;
  const auto *otherAccount = getpwuid(otherUid);
  assert(otherAccount);
  assert(chown(costly.c_str(), otherUid, otherAccount->pw_gid) == 0);
  for (unsigned index = 0; index < 1100; ++index) {
    const auto path = costly + "/application-" + std::to_string(index);
    Fd file(open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
    assert(file);
  }
  auto other = inspectProcess(getpid());
  assert(other);
  const auto started = std::chrono::steady_clock::now();
  assert(trustedShellIdentityAt(*other, *other, otherUid, costly) == IdentityResult::Refused);
  assert(std::chrono::steady_clock::now() - started < std::chrono::seconds(1));
  restored();
  assert(trustedShellIdentityAt(*controller, *controller, uid, runtime) == IdentityResult::Trusted);
  restored();
  pollfd item{listener.get(), POLLIN, 0};
  assert(poll(&item, 1, 0) == 0);
  int status = 0;
  assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
  // An early refusal must restore the same elevated credentials too.
  assert(trustedShellIdentityAt(*controller, *controller, uid, secret) == IdentityResult::Refused);
  restored();
  std::filesystem::remove_all(base);
  puts("Elevated filesystem credentials, restoration, discovery and independent sessions passed");
}
