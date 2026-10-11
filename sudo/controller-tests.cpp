// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the production controller adapter without a host bus or login.
#include "core.hpp"
#include <cassert>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <map>
#include <csignal>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <systemd/sd-bus.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
namespace {
struct Observations {
  bool directoryMissing = false, fileMissing = false, statFailure = false;
  uid_t directoryOwner = 0, fileOwner = 0;
  mode_t directoryMode = S_IFDIR | 0755, fileMode = S_IFREG | 0644;
  off_t fileSize = 32;
  bool readFailure = false, readInterrupted = false;
  std::string state = "CONTROLLER=:1.42\n", replacement;
  unsigned fileOpens = 0, busQueries = 0;
  bool busFailure = false, timeoutFailure = false, credentialsMissing = false;
  bool pidMissing = false, uidMissing = false, pidfdMissing = false;
  bool parentAlive = true, inspectFailure = false, recycled = false;
  bool exitDuringQuery = false;
  std::uint64_t augmented = 0;
  pid_t pid = 5001;
  uid_t uid = 1000;
  std::map<int, bool> directory;
  std::map<int, std::size_t> offsets;
} observed;
bool nativeIpc = false;
std::shared_ptr<Process> nativeProcess(pid_t pid) { return inspectProcess(pid); }
int fixtureOpen(const char *path, int flags, ...) {
  if (nativeIpc)
    return open(path, flags);
  assert(std::string(path) == "/run/systemd/sessions");
  assert((flags & (O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)) ==
         (O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  if (observed.directoryMissing)
    return -1;
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(fd >= 0);
  observed.directory[fd] = true;
  return fd;
}
int fixtureOpenAt(int dir, const char *path, int flags, ...) {
  assert(observed.directory.at(dir));
  assert(std::string(path) == "fixture");
  assert((flags & (O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)) ==
         (O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
  if (observed.fileMissing)
    return -1;
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(fd >= 0);
  observed.directory[fd] = false;
  observed.offsets[fd] = 0;
  ++observed.fileOpens;
  return fd;
}
int fixtureFstat(int fd, struct stat *st) {
  if (nativeIpc)
    return fstat(fd, st);
  if (observed.statFailure)
    return -1;
  *st = {};
  bool dir = observed.directory.at(fd);
  st->st_uid = dir ? observed.directoryOwner : observed.fileOwner;
  st->st_mode = dir ? observed.directoryMode : observed.fileMode;
  st->st_size = observed.fileSize;
  return 0;
}
ssize_t fixtureRead(int fd, void *buffer, size_t size) {
  if (observed.readFailure) {
    errno = EIO;
    return -1;
  }
  if (observed.readInterrupted) {
    observed.readInterrupted = false;
    errno = EINTR;
    return -1;
  }
  const auto &state = observed.fileOpens > 1 && !observed.replacement.empty()
                          ? observed.replacement : observed.state;
  auto &offset = observed.offsets.at(fd);
  size = std::min(size, state.size() - offset);
  memcpy(buffer, state.data() + offset, size);
  offset += size;
  return static_cast<ssize_t>(size);
}
bool fixtureSameProcess(const Process &p) {
  return nativeIpc ? sameProcess(p) : observed.parentAlive;
}
bool fixtureSamePinnedProcess(const Process &a, const Process &b) {
  if (nativeIpc)
    return samePinnedProcess(a, b);
  return observed.parentAlive && a.pid == b.pid && a.start == b.start;
}
std::shared_ptr<Process> fixtureInspectProcess(pid_t, Fd fd) {
  assert(fd);
  if (observed.inspectFailure)
    return nullptr;
  auto p = std::make_shared<Process>();
  p->pid = observed.pid;
  p->start = observed.recycled ? 124 : 123;
  p->uid = p->euid = observed.uid;
  return p;
}
std::shared_ptr<Process> fixtureInspectProcess(pid_t) { return nullptr; }
int fixtureBusOpen(sd_bus **out) {
  if (observed.busFailure)
    return -EIO;
  *out = reinterpret_cast<sd_bus *>(&observed);
  return 0;
}
sd_bus *fixtureBusUnref(sd_bus *) { return nullptr; }
int fixtureBusTimeout(sd_bus *, uint64_t timeout) {
  assert(timeout == 250000);
  return observed.timeoutFailure ? -EIO : 0;
}
int fixtureBusCredentials(sd_bus *, const char *name, uint64_t mask,
                          sd_bus_creds **out) {
  ++observed.busQueries;
  if (observed.exitDuringQuery)
    observed.parentAlive = false;
  assert(std::string(name) == ":1.42");
  assert(mask == (SD_BUS_CREDS_PID | SD_BUS_CREDS_EUID | SD_BUS_CREDS_PIDFD));
  if (observed.credentialsMissing)
    return -ENODATA;
  *out = reinterpret_cast<sd_bus_creds *>(&observed);
  return 0;
}
sd_bus_creds *fixtureCredentialsUnref(sd_bus_creds *) { return nullptr; }
uint64_t fixtureAugmented(const sd_bus_creds *) { return observed.augmented; }
int fixturePid(sd_bus_creds *, pid_t *out) {
  *out = observed.pid;
  return observed.pidMissing ? -ENODATA : 0;
}
int fixtureUid(sd_bus_creds *, uid_t *out) {
  *out = observed.uid;
  return observed.uidMissing ? -ENODATA : 0;
}
int fixturePidfd(sd_bus_creds *, int *out) {
  if (observed.pidfdMissing)
    return -ENODATA;
  *out = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(*out >= 0);
  return 0;
}
}
#define main brokerProductionMain
#define open(...) fixtureOpen(__VA_ARGS__)
#define openat(...) fixtureOpenAt(__VA_ARGS__)
#define fstat(...) fixtureFstat(__VA_ARGS__)
#define read(...) fixtureRead(__VA_ARGS__)
#define sameProcess fixtureSameProcess
#define samePinnedProcess fixtureSamePinnedProcess
#define inspectProcess fixtureInspectProcess
#define sd_bus_open_system fixtureBusOpen
#define sd_bus_unref fixtureBusUnref
#define sd_bus_set_method_call_timeout fixtureBusTimeout
#define sd_bus_get_name_creds fixtureBusCredentials
#define sd_bus_creds_unref fixtureCredentialsUnref
#define sd_bus_creds_get_augmented_mask fixtureAugmented
#define sd_bus_creds_get_pid fixturePid
#define sd_bus_creds_get_euid fixtureUid
#define sd_bus_creds_get_pidfd_dup fixturePidfd
#include "broker.cpp"
#undef main
#undef open
#undef openat
#undef fstat
#undef read
void ipcAdapter() {
  nativeIpc = true;
  auto provider = nativeProcess(getpid());
  assert(provider);
  const auto valid = "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":" +
      std::to_string(provider->pid) + ",\"start_time\":" +
      std::to_string(provider->start) + "}}}\n";
  for (unsigned variant = 0; variant < 13; ++variant) {
    char temporary[] = "/tmp/emaki-sudo-ipc-XXXXXX";
    assert(mkdtemp(temporary));
    const auto path = std::string(temporary) + "/niri.fixture.sock";
    const auto target = variant == 7 ? std::string(temporary) + "/controller.sock" : path;
    const auto alias = std::string(temporary) + "-alias";
    if (variant == 7)
      assert(symlink(target.c_str(), path.c_str()) == 0);
    if (variant == 8)
      assert(symlink(temporary, alias.c_str()) == 0);
    if (variant == 9)
      assert(chmod(temporary, 0755) == 0);
    if (variant == 12) {
      for (unsigned index = 0; index < 1100; ++index) {
        const auto filler = std::string(temporary) + "/application-" + std::to_string(index);
        Fd entry(open(filler.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600));
        assert(entry);
      }
    }
    int ready[2];
    assert(pipe2(ready, O_CLOEXEC) == 0);
    auto server = fork();
    assert(server >= 0);
    if (!server) {
      close(ready[0]);
      Fd listener(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
      assert(listener);
      sockaddr_un address{};
      address.sun_family = AF_UNIX;
      assert(target.size() < sizeof(address.sun_path));
      memcpy(address.sun_path, target.c_str(), target.size() + 1);
      assert(bind(listener.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
      if (variant == 11) {
        // Bind all endpoints first, then listen only on the last endpoint in
        // the actual directory order. This reproduces 300 entries ahead of the
        // genuine socket on both hashed directories and reverse-order tmpfs.
        std::map<std::string, Fd> endpoints;
        endpoints.emplace("niri.fixture.sock", std::move(listener));
        for (unsigned index = 0; index < 300; ++index) {
          const auto name = "niri.filler-" + std::to_string(index) + ".sock";
          const auto filler = std::string(temporary) + "/" + name;
          Fd endpoint(socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0));
          assert(endpoint);
          memcpy(address.sun_path, filler.c_str(), filler.size() + 1);
          assert(bind(endpoint.get(), reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
          endpoints.emplace(name, std::move(endpoint));
        }
        DIR *directory = opendir(temporary);
        assert(directory);
        std::string last;
        unsigned entries = 0;
        while (auto *entry = readdir(directory)) {
          if (endpoints.count(entry->d_name)) {
            last = entry->d_name;
            ++entries;
          }
        }
        assert(closedir(directory) == 0 && entries == 301);
        listener = std::move(endpoints.at(last));
      }
      assert(listen(listener.get(), 1) == 0);
      assert(write(ready[1], "r", 1) == 1);
      close(ready[1]);
      if ((variant >= 7 && variant <= 9) || variant == 12) {
        // Even the genuine controller must never be reached through a symlink
        // or an unsafe runtime directory. Observe accept readiness on the wire.
        pollfd pending{listener.get(), POLLIN, 0};
        assert(poll(&pending, 1, 200) == 0);
        _exit(0);
      }
      Fd connected(accept4(listener.get(), nullptr, nullptr, SOCK_CLOEXEC));
      assert(connected);
      std::string request;
      char buffer[64]{};
      while (request.find('\n') == std::string::npos) {
        auto length = recv(connected.get(), buffer, sizeof(buffer), 0);
        if (length <= 0)
          break;
        request.append(buffer, static_cast<std::size_t>(length));
        assert(request.size() <= sizeof(buffer));
      }
      if (variant != 1) {
        assert(request == "\"TrustedShellIdentity\"\n");
        auto reply = valid;
        if (variant == 2)
          reply = "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":" +
              std::to_string(getpid()) + ",\"start_time\":" +
              std::to_string(provider->start) + "}}}\n";
        if (variant == 3)
          reply = "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":" +
              std::to_string(provider->pid) + ",\"start_time\":" +
              std::to_string(provider->start + 1) + "}}}\n";
        if (variant == 4)
          reply = "{\"Ok\":{\"TrustedShellIdentity\":null}}\n";
        if (variant == 5)
          reply += "{}\n";
        if (variant == 6)
          pause();
        if (variant == 10)
          usleep(30000);
        assert(send(connected.get(), reply.data(), reply.size(), MSG_NOSIGNAL) ==
               static_cast<ssize_t>(reply.size()));
        // Keep the authenticated peer alive through the broker's final
        // pidfd recheck, then exit when the client closes the connection.
        assert(recv(connected.get(), buffer, sizeof(buffer), 0) == 0);
      }
      _exit(0);
    }
    close(ready[1]);
    char value = 0;
    assert(read(ready[0], &value, 1) == 1);
    close(ready[0]);
    auto controller = nativeProcess(variant == 1 ? getpid() : server);
    assert(controller);
    const auto started = std::chrono::steady_clock::now();
    const auto fsuid = setfsuid(static_cast<uid_t>(-1));
    const auto fsgid = setfsgid(static_cast<gid_t>(-1));
    assert(trustedShellIdentityAt(*provider, *controller, getuid(),
                                 variant == 8 ? alias : temporary) ==
           (variant == 0 || variant == 10 || variant == 11 ?
                IdentityResult::Trusted : IdentityResult::Refused));
    assert(setfsuid(static_cast<uid_t>(-1)) == fsuid);
    assert(setfsgid(static_cast<gid_t>(-1)) == fsgid);
    assert(std::chrono::steady_clock::now() - started < std::chrono::seconds(1));
    if (variant == 6)
      kill(server, SIGKILL);
    int status = 0;
    assert(waitpid(server, &status, 0) == server);
    assert(variant == 6 ? WIFSIGNALED(status) : WIFEXITED(status) && WEXITSTATUS(status) == 0);
    unlink(path.c_str());
    if (variant == 7)
      unlink(target.c_str());
    if (variant == 8)
      unlink(alias.c_str());
    std::filesystem::remove_all(temporary);
  }
  nativeIpc = false;
  puts("Production trusted IPC socket adapter checks passed");
}
int main() {
  Process parent;
  parent.pid = 5001;
  parent.start = 123;
  auto accepted = [&] {
    Controller result;
    return resolveSessionController(1000, "fixture", result) && result.process &&
           samePinnedProcess(parent, *result.process);
  };
  assert(accepted());
  assert(observed.fileOpens == 2 && observed.busQueries == 1);
  for (const auto &login : {std::string(), std::string("../fixture"),
                           std::string("fixture/name"), std::string(256, 'a')}) {
    observed = {};
    Controller result;
    assert(!resolveSessionController(1000, login, result));
    assert(observed.fileOpens == 0 && observed.busQueries == 0);
  }
  for (unsigned change = 0; change < 30; ++change) {
    observed = {};
    switch (change) {
    case 0: observed.directoryMissing = true; break;
    case 1: observed.fileMissing = true; break;
    case 2: observed.statFailure = true; break;
    case 3: observed.directoryOwner = 1000; break;
    case 4: observed.fileOwner = 1000; break;
    case 5: observed.directoryMode |= 0020; break;
    case 6: observed.fileMode |= 0002; break;
    case 7: observed.fileMode = S_IFIFO | 0600; break;
    case 8: observed.fileSize = 16385; break;
    case 9: observed.readFailure = true; break;
    case 10: observed.state = std::string(16385, 'x'); break;
    case 11: observed.state = "UID=1000\n"; break;
    case 12: observed.state = "CONTROLLER=org.example.Session\n"; break;
    case 13: observed.state = "CONTROLLER=:1.42\nCONTROLLER=:1.42\n"; break;
    case 14: observed.replacement = "CONTROLLER=:1.43\n"; break;
    case 15: observed.busFailure = true; break;
    case 16: observed.timeoutFailure = true; break;
    case 17: observed.credentialsMissing = true; break;
    case 18: observed.pidMissing = true; break;
    case 19: observed.uidMissing = true; break;
    case 20: ++observed.pid; break;
    case 21: ++observed.uid; break;
    case 22: observed.pidfdMissing = true; break;
    case 23: observed.augmented = SD_BUS_CREDS_PIDFD; break;
    case 24: observed.augmented = SD_BUS_CREDS_PID; break;
    case 25: observed.augmented = SD_BUS_CREDS_EUID; break;
    case 26: observed.parentAlive = false; break;
    case 27: observed.inspectFailure = true; break;
    case 28: observed.recycled = true; break;
    case 29: observed.exitDuringQuery = true; break;
    }
    assert(!accepted());
  }
  observed = {};
  observed.readInterrupted = true;
  assert(accepted());
  observed = {};
  Controller cached;
  assert(resolveSessionController(1000, "fixture", cached));
  assert(cached.resolved && cached.name == ":1.42" && cached.process);
  observed.busFailure = true;
  for (unsigned n = 0; n < 5; ++n) {
    assert(controllerState("fixture") == std::optional<std::string>(cached.name));
    assert(samePinnedProcess(parent, *cached.process));
  }
  assert(observed.busQueries == 1);
  observed.replacement = "CONTROLLER=:1.43\n";
  assert(controllerState("fixture") != std::optional<std::string>(cached.name));
  observed.fileMissing = true;
  assert(!controllerState("fixture"));
  observed = {};
  observed.state = "UID=1000\nACTIVE=1\n";
  Controller absent;
  assert(resolveSessionController(1000, "fixture", absent));
  assert(absent.resolved && absent.name.empty() && !absent.process);
  assert(observed.busQueries == 0);
  puts("Production session controller adapter checks passed");
  ipcAdapter();
}
