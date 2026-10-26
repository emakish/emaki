// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise production broker transitions with synthetic host observations only.
#include "core.hpp"
#include "cleanup.hpp"
#include "warning.hpp"
#include <algorithm>
#include <cassert>
#include <charconv>
#include <cstring>
#include <fcntl.h>
#include <map>
#include <limits>
#include <optional>
#include <set>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <systemd/sd-login.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
namespace {
struct FixtureTimestampCleanup {
  struct Entry { uid_t uid; std::vector<TimestampParent> parents; };
  std::map<std::uint64_t, Entry> served;
  std::vector<std::uint64_t> ended;
  bool available = true;
  bool initialize(const char *, const char *) { return available; }
  bool healthy() const { return available; }
  bool busy() const { return false; }
  bool idle() const { return served.empty(); }
  std::size_t pending(uid_t) const { return ended.size(); }
  bool remember(std::uint64_t key, uid_t uid, TimestampParent parent) {
    if (!available || parent.ppid <= 0 || parent.sid <= 0 || !parent.start)
      return false;
    auto &entry = served[key];
    entry.uid = uid;
    if (std::find(entry.parents.begin(), entry.parents.end(), parent) == entry.parents.end())
      entry.parents.push_back(parent);
    return true;
  }
  void end(std::uint64_t key) {
    ended.push_back(key);
    served.erase(key);
  }
  void endAll() {
    while (!served.empty())
      end(served.begin()->first);
  }
  void poll() {}
  bool drain(std::chrono::milliseconds) { endAll(); return true; }
};
std::uint64_t fixtureTime = 100;
bool fixtureInstalled = true, fixtureBlocked = false, fixtureActive = true;
unsigned fixtureInstallation = 1;
bool fixtureWarningWritable = true;
std::map<uid_t, unsigned> fixtureWarnings;
struct FixtureWarningAcceptance {
  bool accepted(uid_t uid) const {
    return fixtureInstalled && fixtureWarnings.contains(uid) &&
           fixtureWarnings.at(uid) == fixtureInstallation;
  }
  bool acknowledge(uid_t uid) const {
    if (!fixtureInstalled || !fixtureWarningWritable)
      return false;
    fixtureWarnings[uid] = fixtureInstallation;
    return true;
  }
};
bool fixtureDiscoveryUnavailable = false;
bool fixtureNiriReplaced = false, fixtureRunningReadable = true;
std::string fixtureRuntime;
unsigned fixtureMarkerDelay = 0;
const char *fixtureSessionState = "active";
const char *fixtureSessionType = "wayland";
bool fixtureParentSession = true;
bool fixtureUserManager = false;
const char *fixtureUnit = "emaki-shell.service";
pid_t fixtureControllerPid = 0;
std::uint64_t fixtureControllerStart = 0;
bool fixtureControllerAvailable = true;
std::map<pid_t, std::shared_ptr<Process>> fixtureProcesses;
std::set<pid_t> fixtureDead;
uid_t fixtureUid = getuid();
bool fixtureSameProcess(const Process &p) {
  auto found = fixtureProcesses.find(p.pid);
  if (fixtureDead.contains(p.pid) || found == fixtureProcesses.end())
    return false;
  const auto &now = *found->second;
  return now.start == p.start && now.ppid == p.ppid && now.dev == p.dev &&
         now.ino == p.ino && now.uid == p.uid && now.euid == p.euid &&
         now.tty == p.tty && now.sid == p.sid;
}
bool fixtureTrustedExecutable(const Process &p, const char *path) {
  if (std::string_view(path) == EMAKI_NIRI_PATH && fixtureNiriReplaced)
    return false;
  const std::map<std::string, std::string> installed = {
      {EMAKI_QUICKSHELL_PATH, "/fixture/qs"},
      {EMAKI_SYSTEMD_PATH, "/fixture/systemd"},
      {EMAKI_NIRI_PATH, "/fixture/niri-emaki"},
      {EMAKI_SUDO_PATH, "/fixture/sudo"},
      {std::string(EMAKI_COMMAND_BINDIR) + "/claude", "/fixture/claude"},
      {std::string(EMAKI_COMMAND_BINDIR) + "/codex", "/fixture/codex"},
      {std::string(EMAKI_COMMAND_BINDIR) + "/bash", "/fixture/bash"}};
  if (std::string(path) == std::string(EMAKI_COMMAND_BINDIR) + "/claude" &&
      p.executable == "/fixture/native")
    return true;
  auto expected = installed.find(path);
  return expected != installed.end() && expected->second == p.executable;
}
bool fixtureTrustedRunningExecutable(const Process &p, const char *path) {
  return fixtureRunningReadable && std::string_view(path) == EMAKI_NIRI_PATH &&
      (p.executable == "/fixture/niri-emaki" || p.executable == EMAKI_NIRI_PATH ||
       p.executable == std::string(EMAKI_NIRI_PATH) + " (deleted)") &&
      fixtureSameProcess(p);
}
struct Controller;
enum class IdentityResult;
bool fixtureResolveSessionController(uid_t, const std::string &, Controller &);
std::optional<std::string> fixtureControllerState(const std::string &);
IdentityResult fixtureTrustedShellIdentity(const Process &, const Process &, uid_t);
bool fixtureSamePinnedProcess(const Process &a, const Process &b) {
  return a.pid == b.pid && a.start == b.start && fixtureSameProcess(a) &&
         fixtureSameProcess(b);
}
pid_t fixtureTrustedPid = 5000;
std::uint64_t fixtureTrustedStart = 5000;
unsigned fixtureControllerQueries = 0, fixtureIdentityQueries = 0;
unsigned fixtureIdentityCost = 1;
unsigned fixtureIdentityDelay = 0;
bool fixtureSafeRootFile(const char *) { return fixtureInstalled; }
bool fixtureValidateIdentity(const Identity &identity, bool = false) {
  for (std::size_t n = 0; n < identity.chain.size(); ++n)
    if (!fixtureSameProcess(*identity.chain[n]) ||
        (n && identity.chain[n - 1]->ppid != identity.chain[n]->pid))
      return false;
  return true;
}
std::shared_ptr<Process> fixtureInspectProcess(pid_t pid) {
  auto it = fixtureProcesses.find(pid);
  return it == fixtureProcesses.end() ? nullptr : it->second;
}
std::shared_ptr<Process> fixtureInspectPeer(int, pid_t pid, uid_t uid) {
  auto process = fixtureInspectProcess(pid);
  return process && process->euid == uid ? process : nullptr;
}
// The run loop reads the clock after its stop-flag check and before poll(), so a
// stop raised here lands exactly in the window the stop pipe closes.
bool fixtureStopAtClock = false;
std::uint64_t fixtureClock() {
  if (fixtureStopAtClock) {
    fixtureStopAtClock = false;
    raise(SIGTERM);
  }
  return fixtureTime;
}
bool fixtureAuthenticationBlocked(int, uid_t, bool *unavailable = nullptr) {
  if (fixtureMarkerDelay)
    usleep(fixtureMarkerDelay);
  if (!fixtureRuntime.empty()) {
    Fd runtime(open(fixtureRuntime.c_str(), O_DIRECTORY | O_CLOEXEC));
    return authenticationBlocked(runtime.get(), fixtureUid, unavailable);
  }
  if (unavailable)
    *unavailable = fixtureDiscoveryUnavailable;
  return fixtureBlocked;
}
std::map<int, std::string> fixtureFiles;
char *fixtureRealpath(const char *path, char *) { return strdup(path); }
int fixtureOpen(const char *path, int) {
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  fixtureFiles[fd] = path ? path : "";
  return fd;
}
int fixtureOpenAt(int, const char *path, int) { return fixtureOpen(path, 0); }
int fixtureFstat(int fd, struct stat *st) {
  *st = {};
  st->st_mode = (fixtureFiles[fd].ends_with(".so") ||
                 fixtureFiles[fd].find("/map_files/") != std::string::npos)
                    ? S_IFREG | 0644 : S_IFDIR | 0755;
  st->st_ino = 1;
  return 0;
}
[[maybe_unused]] int fixtureStat(const char *, struct stat *st) {
  *st = {};
  st->st_dev = 0;
  st->st_ino = 1;
  return 0;
}
std::string fixtureReadBounded(const std::string &path, std::size_t) {
  if (path.ends_with("/maps"))
    return "0-1 r-xp 0 00:00 1 " EMAKI_PAM_MODULE "\n"
           "1-2 r-xp 0 00:00 1 " EMAKI_SHELL_PATH "/Emaki/Sudo/libemakisudoplugin.so\n";
  if (path == "/proc/5000/cmdline" || path == "/proc/5100/cmdline" ||
      path == "/proc/6000/cmdline")
    return std::string("qs\0-p\0/fixture\0", 15);
  if (path == "/proc/5001/cmdline" || path == "/proc/7001/cmdline")
    return std::string("systemd\0--user\0", 15);
  if (path == "/proc/4000/cmdline")
    return std::string("sudo\0true\0", 10);
  return std::string("requester\0", 10);
}
}
extern "C" {
int sd_pid_get_session(pid_t pid, char **out) {
  if (fixtureUserManager)
    return -ENODATA;
  *out = strdup(pid == 6000 || pid == 6001 ? "second" :
                pid == 5001 && !fixtureParentSession ? "other" : "fixture");
  return 0;
}
int sd_pid_get_owner_uid(pid_t, uid_t *out) { *out = fixtureUid; return 0; }
int sd_uid_get_display(uid_t, char **out) { *out = strdup("fixture"); return 0; }
int sd_uid_get_sessions(uid_t, int active, char ***out) {
  if (active && !fixtureActive)
    return 0;
  *out = static_cast<char **>(calloc(2, sizeof(char *)));
  (*out)[0] = strdup("fixture");
  return 1;
}
int sd_session_get_type(const char *, char **out) { *out = strdup(fixtureSessionType); return 0; }
int sd_session_get_uid(const char *login, uid_t *out) {
  *out = fixtureUid + (std::string_view(login) == "second" ? 1 : 0);
  return 0;
}
int sd_session_is_active(const char *) { return fixtureActive; }
int sd_session_get_state(const char *, char **out) {
  if (!fixtureSessionState)
    return -ENODATA;
  *out = strdup(fixtureSessionState);
  return 0;
}
int sd_pid_get_user_unit(pid_t, char **out) {
  if (!fixtureUnit)
    return -ENODATA;
  *out = strdup(fixtureUnit);
  return 0;
}
}
#define realpath fixtureRealpath
#define TimestampCleanup FixtureTimestampCleanup
#define WarningAcceptance FixtureWarningAcceptance
#define EMAKI_SUDO_LIFECYCLE_TEST
#define main brokerProductionMain
#define sameProcess fixtureSameProcess
#define trustedExecutable fixtureTrustedExecutable
#define trustedRunningExecutable fixtureTrustedRunningExecutable
#define resolveSessionController fixtureResolveSessionController
#define controllerState fixtureControllerState
#define trustedShellIdentity fixtureTrustedShellIdentity
#define samePinnedProcess fixtureSamePinnedProcess
#define safeRootFile fixtureSafeRootFile
#define validateIdentity fixtureValidateIdentity
#define inspectProcess fixtureInspectProcess
#define inspectPeer fixtureInspectPeer
#define boottimeSeconds fixtureClock
#define authenticationBlocked fixtureAuthenticationBlocked
#define readBounded fixtureReadBounded
#define open(...) fixtureOpen(__VA_ARGS__)
#define openat(...) fixtureOpenAt(__VA_ARGS__)
#define fstat(...) fixtureFstat(__VA_ARGS__)
#define stat(...) fixtureStat(__VA_ARGS__)
#include "broker.cpp"
#undef main
#undef TimestampCleanup
#undef WarningAcceptance
#undef open
#undef openat
#undef fstat
#undef stat
namespace {
int fixtureCliSocket = -1;
Fd fixtureConnectBroker() { return Fd(dup(fixtureCliSocket)); }
}
#define main cliProductionMain
#define connectBroker fixtureConnectBroker
#include "cli.cpp"
#undef connectBroker
#undef main
namespace {
std::optional<std::string> fixtureControllerState(const std::string &login) {
  if (login == "second")
    return ":1.60016001";
  if (!fixtureControllerAvailable)
    return {};
  if (!fixtureControllerPid)
    return std::string{};
  return ":1." + std::to_string(fixtureControllerPid) +
         std::to_string(fixtureControllerStart);
}
bool fixtureResolveSessionController(uid_t uid, const std::string &login,
                                     Controller &result) {
  ++fixtureControllerQueries;
  auto state = fixtureControllerState(login);
  if (state && uid == fixtureUid + 1 && login == "second") {
    result = {true, *state, fixtureInspectProcess(6001)};
    return true;
  }
  if (!state || uid != fixtureUid || login != "fixture")
    return false;
  auto p = fixtureControllerPid ? fixtureInspectProcess(fixtureControllerPid) : nullptr;
  if (fixtureControllerPid && (!p || p->start != fixtureControllerStart))
    return false;
  result = {true, *state, p};
  return true;
}
IdentityResult fixtureTrustedShellIdentity(const Process &provider, const Process &, uid_t uid) {
  ++fixtureIdentityQueries;
  (void)uid;
  if (fixtureIdentityDelay)
    usleep(fixtureIdentityDelay);
  if (fixtureIdentityCost > 1024)
    return IdentityResult::Refused;
  return ((provider.pid == fixtureTrustedPid && provider.start == fixtureTrustedStart) ||
          (provider.pid == 6000 && provider.start == 6000)) &&
         fixtureSameProcess(provider) ? IdentityResult::Trusted : IdentityResult::Refused;
}
struct BrokerFixture {
  Broker broker;
  std::map<int, Fd> peers;
  std::shared_ptr<Process> process(pid_t pid, pid_t parent, uid_t uid,
                                  const char *exe) {
    auto p = std::make_shared<Process>();
    p->pid = pid;
    p->ppid = parent;
    p->sid = 3900;
    p->uid = p->euid = uid;
    p->start = pid;
    p->dev = p->ino = pid;
    p->executable = exe;
    fixtureProcesses[pid] = p;
    return p;
  }
  BrokerFixture() {
    fixtureProcesses.clear();
    fixtureDead.clear();
    fixtureTime = 100;
    fixtureInstalled = fixtureActive = true;
    fixtureInstallation = 1;
    fixtureWarningWritable = true;
    fixtureWarnings.clear();
    fixtureBlocked = fixtureUserManager = fixtureDiscoveryUnavailable = false;
    fixtureNiriReplaced = false;
    fixtureRunningReadable = true;
    fixtureRuntime.clear();
    fixtureMarkerDelay = 0;
    fixtureSessionState = "active";
    fixtureSessionType = "wayland";
    fixtureParentSession = true;
    fixtureUnit = "emaki-shell.service";
    fixtureControllerPid = fixtureControllerStart = 0;
    fixtureControllerAvailable = true;
    fixtureTrustedPid = fixtureTrustedStart = 5000;
    fixtureControllerQueries = fixtureIdentityQueries = 0;
    fixtureIdentityCost = 1;
    fixtureIdentityDelay = 0;
    process(5001, 1, fixtureUid, "/fixture/systemd");
    process(5000, 5001, fixtureUid, "/fixture/qs");
    process(4001, 1, fixtureUid, "/fixture/claude");
    process(4000, 4001, 0, "/fixture/sudo");
    broker.hadMarker = true;
    compositor();
  }
  int client(uid_t uid, pid_t pid) {
    int pair[2];
    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0);
    peers.insert_or_assign(pair[0], Fd(pair[1]));
    broker.clients.emplace(pair[0], Client{Fd(pair[0]), uid, fixtureTime,
                                           false, false, fixtureProcesses.at(pid), {}, {}, {}});
    return pair[0];
  }
  void drain(int fd) {
    Packet p;
    while (receivePacket(peers.at(fd).get(), p)) {}
  }
  void send(int fd, Type type, std::uint64_t id = 0,
            std::uint32_t option = 0, const std::string &body = {}) {
    assert(sendPacket(peers.at(fd).get(), type, id, option, body.data(), body.size()));
    broker.handle(fd);
    finish(fd);
  }
  void finish(int fd) {
    const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (broker.clients.contains(fd) && broker.clients.at(fd).check) {
      assert(std::chrono::steady_clock::now() < limit);
      broker.completeChecks();
      usleep(1000);
    }
  }
  int connectUi() {
    int fd = client(fixtureUid, 5000);
    send(fd, Type::HelloUi);
    assert(broker.clients.at(fd).ui);
    drain(fd);
    send(fd, Type::Unlock);
    send(fd, Type::AcknowledgeWarning);
    drain(fd);
    return fd;
  }
  Type request() {
    int fd = client(0, 4000);
    auto *pw = getpwuid(fixtureUid);
    assert(pw);
    send(fd, Type::Authenticate, 0, 0, pw->pw_name);
    Packet reply;
    assert(receivePacket(peers.at(fd).get(), reply));
    Type type = reply.header.type;
    if (type == Type::Waiting)
      pendingFd = fd;
    return type;
  }
  int pendingFd = -1;
  void grant(int ui, std::uint32_t duration) {
    assert(request() == Type::Waiting);
    assert(broker.pending.size() == 1);
    auto id = broker.pending.begin()->first;
    send(ui, Type::Submit, id, duration, "fixture-password");
    drain(pendingFd);
    send(pendingFd, Type::Authenticated, id);
    drain(ui);
    assert(broker.pending.empty());
    assert(broker.keys.size() == 1);
  }
  void reportingTree(const char *executable = "/fixture/claude") {
    process(4100, 1, fixtureUid, executable);
    process(4001, 4100, fixtureUid, "/fixture/bash");
    process(4101, 4100, fixtureUid, "/fixture/report");
  }
  void report(Type type, const std::string &token = "fixture-session",
              pid_t pid = 4101, uid_t uid = fixtureUid) {
    int fd = client(uid, pid);
    send(fd, type, 0, 0, token);
    assert(!broker.clients.contains(fd));
    drain(fd);
  }
  void matureReporter() {
    report(Type::TurnStart);
    report(Type::TurnEnd);
    report(Type::TurnStart);
  }
  void remaining(unsigned count) {
    assert(broker.keys.size() == 1);
    assert(broker.keys.begin()->second.remainingTurns == count);
    assert(broker.status(fixtureUid).find("\"kind\":\"turns\"") != std::string::npos);
    assert(broker.status(fixtureUid).find("\"remainingTurns\":" +
                                       std::to_string(count)) != std::string::npos);
  }
  std::uint64_t submitTurn(int ui) {
    assert(request() == Type::Waiting);
    auto id = broker.pending.begin()->first;
    send(ui, Type::Submit, id, TurnsFlag | 3, "fixture-password");
    drain(pendingFd);
    return id;
  }
  void compositor() {
    process(5001, 1, fixtureUid, "/fixture/niri-emaki");
    fixtureUnit = "niri-emaki.service";
    fixtureControllerPid = 5001;
    fixtureControllerStart = 5001;
  }
  void deniedUi(pid_t pid = 5000) {
    int fd = client(fixtureUid, pid);
    send(fd, Type::HelloUi);
    Packet reply;
    assert(receivePacket(peers.at(fd).get(), reply));
    assert(reply.header.type == Type::Error);
    assert(std::string(reinterpret_cast<const char *>(reply.body.data()), reply.body.size()) == "{\"error\":\"frontend-unverified\"}");
    assert(!broker.clients.contains(fd));
    assert(broker.status(fixtureUid).find("\"promptAvailable\":false") != std::string::npos);
  }
  static void capacityLimits();
  static void revocationAdmission();
  static void ownerIdentity();
  static void providerIdentity();
  static void identityScheduling();
  static void identityIdleBoundary(bool authentication);
  static void statusTransport(bool oversized);
  static void amountValidation();
  static void turnLifecycle();
  static void logoutLifecycle();
  static void reporterLimits();
  static void timestampLifecycle();
  static void warningLifecycle();
  static void wakeSchedule();
  static void statusRefresh();
  static void idleLoop();
  static void stopBeforePoll(bool pipe);
  void run() {
    int ui = connectUi();
    grant(ui, 420);
    assert(request() == Type::Granted);
    send(ui, Type::Lock);
    fixtureBlocked = true;
    broker.reap();
    assert(broker.keys.size() == 1);
    assert(request() == Type::Granted);
    send(ui, Type::RevokeAll);
    assert(broker.keys.empty());
    assert(request() == Type::Fallback);
    fixtureBlocked = false;
    send(ui, Type::Unlock);
    assert(request() == Type::Waiting);
    auto id = broker.pending.begin()->first;
    send(ui, Type::Submit, id, 420, "fixture-password");
    send(ui, Type::Lock);
    assert(broker.pending.empty());
    assert(broker.keys.empty());
    send(ui, Type::Unlock);
    grant(ui, Session);
    fixtureTime += 4;
    broker.reap();
    assert(broker.users.at(fixtureUid).ui == -1);
    assert(broker.keys.size() == 1);
    assert(request() == Type::Granted);
    assert(broker.status(fixtureUid).find("\"promptAvailable\":false") != std::string::npos);
    ui = connectUi();
    assert(broker.keys.size() == 1);
    assert(broker.status(fixtureUid).find("\"ownerPid\":4001") != std::string::npos);
    fixtureActive = false;
    fixtureTime += 3600;
    broker.reap();
    assert(broker.keys.size() == 1);
    assert(request() == Type::Granted);
    fixtureActive = true;
    ui = connectUi();
    send(ui, Type::RevokeAll);
    grant(ui, 420);
    fixtureTime += 420;
    broker.reap();
    assert(broker.keys.empty());
    assert(request() == Type::Fallback);
    ui = connectUi();
    grant(ui, Session);
    fixtureDead.insert(4001);
    broker.reap();
    assert(broker.keys.empty());
    fixtureDead.clear();
    assert(request() == Type::Waiting);
    id = broker.pending.begin()->first;
    send(ui, Type::Submit, id, Session, "fixture-password");
    broker.drop(ui);
    assert(broker.pending.empty());
    assert(broker.keys.empty());
    assert(request() == Type::Fallback);
    ui = connectUi();
    grant(ui, Session);
    fixtureInstalled = false;
    broker.reap();
    assert(broker.keys.empty());
    puts("Production broker lifecycle checks passed");
  }
};
void BrokerFixture::capacityLimits() {
  BrokerFixture f;
  const int ui = f.connectUi();
  for (uid_t uid : {fixtureUid, fixtureUid + 1}) {
    for (unsigned index = 0; index < 8; ++index) {
      const pid_t pid = 8000 + (uid - fixtureUid) * 100 + index;
      f.process(pid, 1, uid, "/fixture/idle");
      assert(f.broker.connectionAvailable(uid, pid));
      f.client(uid, pid);
    }
    assert(!f.broker.connectionAvailable(uid, 8900));
  }
  assert(f.broker.clients.at(ui).ui);
  assert(f.broker.connectionAvailable(fixtureUid, 5000));
  const int reconnect = f.client(fixtureUid, 5000);
  assert(!f.broker.connectionAvailable(fixtureUid, 5000));
  f.broker.drop(reconnect);
  assert(f.broker.connectionAvailable(0, 4000));
  for (unsigned index = 0; index < 32; ++index)
    f.client(0, 4000);
  assert(!f.broker.connectionAvailable(0, 4000));
  fixtureTime += 1;
  f.broker.reap();
  assert(f.broker.clients.size() == 1 && f.broker.clients.contains(ui));
  for (uid_t uid : {fixtureUid, fixtureUid + 1}) {
    for (unsigned index = 0; index < 64; ++index) {
      assert(f.broker.keyCapacity(uid));
      const auto id = 1000 + (uid - fixtureUid) * 100 + index;
      f.broker.keys.emplace(id, Key{id, 0, uid, "fixture",
          fixtureProcesses.at(4001), "", {}, 0});
    }
    assert(!f.broker.keyCapacity(uid));
  }
  puts("Production broker independent connection and key quotas passed");
}
void BrokerFixture::revocationAdmission() {
  for (unsigned mode = 0; mode < 3; ++mode) {
    BrokerFixture f;
    const int ui = f.connectUi();
    f.grant(ui, Session);
    const auto firstKey = f.broker.keys.begin()->first;
    const auto secondKey = firstKey + 100;
    auto other = f.broker.keys.begin()->second;
    other.id = secondKey;
    f.broker.keys.emplace(secondKey, std::move(other));
    f.broker.drop(ui);
    for (unsigned index = 0; index < 8; ++index) {
      f.process(8000 + index, 1, fixtureUid, "/fixture/idle");
      f.client(fixtureUid, 8000 + index);
    }
    f.process(getpid(), 1, fixtureUid, "/fixture/client");
    assert(!f.broker.connectionAvailable(fixtureUid, getpid()));
    f.broker.listener.reset(socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
    assert(f.broker.listener);
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    const auto name = "emaki-revoke-test-" + std::to_string(getpid()) + "-" + std::to_string(mode);
    memcpy(address.sun_path + 1, name.data(), name.size());
    const auto length = static_cast<socklen_t>(offsetof(sockaddr_un, sun_path) + 1 + name.size());
    assert(bind(f.broker.listener.get(), reinterpret_cast<sockaddr *>(&address), length) == 0);
    assert(listen(f.broker.listener.get(), 16) == 0);
    Fd client(socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0));
    assert(client && connect(client.get(), reinterpret_cast<sockaddr *>(&address), length) == 0);
    pid_t child = -1;
    if (mode == 1) {
      // Force accept between connect and send, rather than depending on timing.
      f.broker.acceptClient();
      assert(f.broker.clients.size() == 8);
      assert(sendPacket(client.get(), Type::Revoke, firstKey));
    } else if (mode == 2) {
      fixtureCliSocket = client.get();
      child = fork();
      assert(child >= 0);
      if (!child) {
        char name[] = "emaki-sudo", action[] = "revoke-all";
        char *argv[] = {name, action, nullptr};
        _exit(cliProductionMain(2, argv));
      }
    } else {
      assert(sendPacket(client.get(), Type::RevokeAll));
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (f.broker.keys.contains(firstKey)) {
      assert(std::chrono::steady_clock::now() < deadline);
      auto inputs = f.broker.pollInputs();
      assert(poll(inputs.descriptors.data(), inputs.descriptors.size(), 20) >= 0);
      f.broker.dispatchReady(inputs.descriptors);
    }
    assert(std::find(f.broker.timestamps.ended.begin(), f.broker.timestamps.ended.end(), firstKey) !=
           f.broker.timestamps.ended.end());
    if (mode == 1) {
      assert(f.broker.keys.size() == 1 && f.broker.keys.contains(secondKey));
    } else {
      assert(f.broker.keys.empty());
      assert(std::find(f.broker.timestamps.ended.begin(), f.broker.timestamps.ended.end(), secondKey) !=
             f.broker.timestamps.ended.end());
    }
    if (child > 0) {
      int status = 0;
      assert(waitpid(child, &status, 0) == child);
      assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    } else {
      Packet reply;
      assert(receivePacket(client.get(), reply) && reply.header.type == Type::Response);
    }
    assert(f.broker.clients.size() <= 8);
  }
  puts("Production broker revocation bypasses idle same-uid quotas");
}
void BrokerFixture::ownerIdentity() {
  for (const auto *executable : {"/fixture/unknown", "/untrusted/claude", "/untrusted/bash"}) {
    BrokerFixture f;
    f.reportingTree();
    f.process(4001, 4100, fixtureUid, executable);
    int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    auto &request = f.broker.pending.begin()->second;
    assert(request.identity->owner->pid == 4001);
    assert(!keyOwnerEligible(*request.identity));
    f.send(ui, Type::Submit, request.id, Session, "fixture-password");
    assert(!request.candidate.submitted());
    f.send(ui, Type::Submit, request.id, 0, "fixture-password");
    assert(request.candidate.submitted());
  }
  {
    BrokerFixture f;
    f.reportingTree();
    const int ui = f.connectUi();
    f.grant(ui, Session);
    assert(f.broker.keys.begin()->second.owner->pid == 4100);
    // Existing descendant scope remains unchanged after a grant.
    f.process(4001, 4100, fixtureUid, "/fixture/unknown");
    assert(f.request() == Type::Granted);
  }
  {
    BrokerFixture f;
    f.reportingTree("/fixture/native");
    f.process(4200, 1, fixtureUid, "/fixture/codex");
    f.process(4100, 4200, fixtureUid, "/fixture/native");
    f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto &identity = *f.broker.pending.begin()->second.identity;
    assert(keyOwnerEligible(identity));
    assert(requesterLabel(identity) == "Claude Code");
  }
  puts("Production broker pinned owner and wrapper checks passed");
}
void BrokerFixture::providerIdentity() {
  for (bool compositorPath : {true}) {
    {
      BrokerFixture f;
      if (compositorPath)
        f.compositor();
      int ui = f.connectUi();
      assert(f.broker.status(fixtureUid).find("\"promptAvailable\":true") != std::string::npos);
      f.grant(ui, 420);
      assert(f.request() == Type::Granted);
    }
    for (unsigned change = 0; change < 8; ++change) {
      BrokerFixture f;
      if (compositorPath)
        f.compositor();
      int ui = f.connectUi();
      auto parent = f.process(5001, 1, fixtureUid,
          compositorPath ? "/fixture/niri-emaki" : "/fixture/systemd");
      switch (change) {
      case 0: ++parent->start; break;
      case 1: ++parent->uid; break;
      case 2: ++parent->euid; break;
      case 3: ++parent->ino; break;
      case 4: fixtureDead.insert(5001); break;
      case 5: ++f.process(5000, 5001, fixtureUid, "/fixture/qs")->start; break;
      case 6: f.process(5000, 4100, fixtureUid, "/fixture/qs"); break;
      case 7: ++f.process(5000, 5001, fixtureUid, "/fixture/other")->ino; break;
      }
      f.send(ui, Type::Status);
      assert(!f.broker.clients.contains(ui));
      assert(f.broker.status(fixtureUid).find("\"promptAvailable\":false") != std::string::npos);
    }
  }
  {
    BrokerFixture f;
    // A user-manager shell is refused before any controller bus query.
    f.process(5001, 1, fixtureUid, "/fixture/systemd");
    f.deniedUi();
    assert(fixtureControllerQueries == 0);
  }
  for (unsigned impostor = 0; impostor < 15; ++impostor) {
    BrokerFixture f;
    f.compositor();
    switch (impostor) {
    case 0: f.process(5001, 1, fixtureUid, "/fixture/terminal"); break;
    case 1: f.process(5001, 1, fixtureUid, "/fixture/fake-niri-emaki"); break;
    case 2: fixtureControllerPid = 6001; break;
    case 3: ++fixtureControllerStart; break;
    case 4: fixtureControllerAvailable = false; break;
    case 5: ++fixtureProcesses.at(5001)->uid; break;
    case 6: f.process(5000, 5001, fixtureUid, "/fixture/fake-qs"); break;
    case 7: fixtureActive = false; break;
    case 8: fixtureParentSession = false; break;
    case 9: fixtureSessionType = "tty"; break;
    // Ordinary compositor spawning double-forks into an application unit.
    case 10:
      f.process(5001, 1, fixtureUid, "/fixture/systemd");
      fixtureUnit = "app-shell.scope";
      break;
    case 11:
      f.process(5001, 1, fixtureUid, "/fixture/fake-niri-emaki");
      fixtureUnit = "emaki-shell.service";
      break;
    // A configured satellite script execs the installed qs. PID, direct
    // parent, inode, arguments and session pass, but the private client differs.
    case 12: fixtureTrustedPid = 5100; break;
    case 13: ++fixtureTrustedStart; break;
    // The valid legacy service cannot downgrade an installed niri session.
    case 14:
      f.process(6001, 1, fixtureUid, "/fixture/niri-emaki");
      fixtureControllerPid = fixtureControllerStart = 6001;
      f.process(5001, 1, fixtureUid, "/fixture/systemd");
      fixtureUnit = "emaki-shell.service";
      break;
    }
    f.deniedUi();
  }
  for (bool changedController : {false, true}) {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    if (changedController)
      ++fixtureControllerPid;
    else
      fixtureControllerAvailable = false;
    f.broker.reap();
    assert(!f.broker.clients.contains(ui));
  }
  for (bool alreadyRegistered : {false, true}) {
    BrokerFixture f;
    f.compositor();
    int ui = alreadyRegistered ? f.connectUi() : -1;
    // Package replacement changes the installed inode, not the live controller.
    fixtureNiriReplaced = true;
    fixtureProcesses.at(5001)->executable = std::string(EMAKI_NIRI_PATH) + " (deleted)";
    assert(!fixtureTrustedExecutable(*fixtureProcesses.at(5001), EMAKI_NIRI_PATH));
    if (!alreadyRegistered)
      ui = f.connectUi();
    f.send(ui, Type::Status);
    assert(f.broker.clients.at(ui).ui);
    f.grant(ui, 300);
    // The upgraded controller still has to identify its private shell over IPC.
    fixtureTrustedPid = 0;
    f.send(ui, Type::Status);
    assert(!f.broker.clients.contains(ui));
    f.process(7001, 1, fixtureUid, "/fixture/systemd");
    f.process(5100, 7001, fixtureUid, "/fixture/qs");
    fixtureUnit = "emaki-shell.service";
    f.deniedUi(5100);
    // Failure to read the running inode must not downgrade a known niri session.
    fixtureRunningReadable = false;
    f.deniedUi(5100);
  }
  {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    assert(fixtureControllerQueries == 1);
    f.send(ui, Type::Status);
    f.broker.reap();
    assert(fixtureControllerQueries == 1);
    // SIGSTOP leaves the genuine shell pinned and connected, but heartbeats
    // expire. A satellite-path script exec preserves its own direct parent.
    fixtureTime += 4;
    f.broker.reap();
    assert(!f.broker.clients.contains(ui));
    f.process(5100, 5001, fixtureUid, "/fixture/qs");
    f.deniedUi(5100);
  }
  {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    fixtureTrustedPid = 0; // private Wayland connection revoked
    f.send(ui, Type::Status);
    assert(!f.broker.clients.contains(ui));
  }
  puts("Production broker provider identity checks passed");
}
void BrokerFixture::identityIdleBoundary(bool authentication) {
  for (bool expired : {false, true}) {
    BrokerFixture f;
    if (authentication)
      f.connectUi();
    const int fd = f.client(authentication ? 0 : fixtureUid,
                            authentication ? 4000 : 5000);
    const int idle = f.client(fixtureUid, 4001);
    fixtureIdentityDelay = 200000;
    const std::string name = authentication ? getpwuid(fixtureUid)->pw_name : "";
    assert(sendPacket(f.peers.at(fd).get(),
                      authentication ? Type::Authenticate : Type::HelloUi,
                      0, 0, name.data(), name.size()));
    f.broker.handle(fd);
    assert(f.broker.clients.at(fd).check);
    auto &check = *f.broker.clients.at(fd).check;
    assert(IdentityCheck::Clock::now() < check.deadline);
    // Exercise a deadline crossing after collection but before the idle sweep.
    f.broker.completeChecks();
    assert(f.broker.clients.at(fd).check);
    if (expired)
      check.deadline = IdentityCheck::Clock::now() - std::chrono::milliseconds(1);
    // The seconds clock can tick immediately after acceptance while the
    // independent monotonic identity deadline still has time remaining.
    ++fixtureTime;
    f.broker.reap();
    assert(!f.broker.clients.contains(idle));
    assert(f.broker.clients.contains(fd));
    assert(f.broker.clients.at(fd).check);
    if (expired) {
      f.broker.completeChecks();
      assert(!f.broker.clients.contains(fd));
      Packet reply;
      assert(receivePacket(f.peers.at(fd).get(), reply));
      assert(reply.header.type == (authentication ? Type::Fallback : Type::Error));
      continue;
    }
    assert(f.broker.clients.contains(fd));
    assert(f.broker.clients.at(fd).check);
    f.finish(fd);
    assert(f.broker.clients.contains(fd));
    assert(!f.broker.clients.at(fd).check);
    Packet reply;
    assert(receivePacket(f.peers.at(fd).get(), reply));
    // Registration may publish the new session's empty key list before its
    // acknowledgement; initial authentication replies directly with Waiting.
    if (!authentication && reply.header.type == Type::Keys)
      assert(receivePacket(f.peers.at(fd).get(), reply));
    assert(reply.header.type == (authentication ? Type::Waiting : Type::Response));
    assert(authentication ? f.broker.clients.at(fd).pending
                          : f.broker.clients.at(fd).ui);
  }
  puts("Identity checks survive idle reaping until their outcome is collected");
}
void BrokerFixture::statusTransport(bool oversized) {
  BrokerFixture f;
  const int ui = f.connectUi();
  auto owner = fixtureProcesses.at(4001);
  const std::string label = pinnedRequesterLabel(*owner);
  assert(!label.empty());
  owner = f.process(std::numeric_limits<pid_t>::max(), 1, fixtureUid,
                    owner->executable.c_str());
  for (std::uint64_t index = 0; index < 64; ++index) {
    const auto id = std::numeric_limits<std::uint64_t>::max() - index;
    f.broker.keys.emplace(id, Key{id, std::numeric_limits<std::uint64_t>::max(),
        fixtureUid, "fixture", owner, label, {},
        std::numeric_limits<std::uint32_t>::max()});
  }
  assert(!f.broker.keyCapacity(fixtureUid));
  if (oversized)
    f.broker.keys.begin()->second.requester.assign(MaxBody + 1, 'x');
  const std::string expected = oversized ? "{\"error\":\"status-too-large\"}"
                                         : f.broker.status(fixtureUid);
  if (!oversized) {
    assert(expected.size() > 8192);
    std::printf("64-key status body: %zu bytes\n", expected.size());
    std::fflush(stdout);
  }
  auto received = [&](int fd, Type type) {
    Packet reply;
    assert(receivePacket(f.peers.at(fd).get(), reply));
    assert(reply.header.type == (oversized ? Type::Error : type));
    assert(std::string(reinterpret_cast<const char *>(reply.body.data()),
                       reply.body.size()) == expected);
  };
  // An ordinary status connection exercises request and response framing.
  const int status = f.client(fixtureUid, 4001);
  f.send(status, Type::Status);
  received(status, Type::Response);
  assert(!f.broker.clients.contains(status));
  // The registered UI receives the same complete snapshot through framing.
  f.broker.broadcast(fixtureUid);
  received(ui, Type::Keys);
  if (oversized) {
    assert(!f.broker.clients.contains(ui));
  } else {
    // A registered UI's explicit status path must use the same bound.
    f.send(ui, Type::Status);
    received(ui, Type::Response);
  }
  puts("Maximum-capacity status and oversized errors pass transport framing");
}
void BrokerFixture::identityScheduling() {
  auto start = [](BrokerFixture &f, int fd, Type type, std::uint64_t id = 0) {
    assert(sendPacket(f.peers.at(fd).get(), type, id));
    f.broker.handle(fd);
    assert(f.broker.clients.contains(fd) && f.broker.clients.at(fd).check);
    char byte;
    assert(recv(fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT) == -1 && errno == EAGAIN);
  };
  {
    BrokerFixture f;
    f.compositor();
    fixtureIdentityCost = 300;
    int ui = f.client(fixtureUid, 5000);
    start(f, ui, Type::HelloUi);
    f.finish(ui);
    assert(f.broker.clients.at(ui).ui);
    f.drain(ui);
    start(f, ui, Type::Status);
    f.finish(ui);
    Packet reply;
    assert(receivePacket(f.peers.at(ui).get(), reply));
    assert(reply.header.type == Type::Response);
  }
  {
    BrokerFixture f;
    f.compositor();
    fixtureIdentityCost = 1025;
    int ui = f.client(fixtureUid, 5000);
    start(f, ui, Type::HelloUi);
    f.finish(ui);
    assert(!f.broker.clients.contains(ui));
    Packet reply;
    assert(receivePacket(f.peers.at(ui).get(), reply));
    assert(reply.header.type == Type::Error);
  }
  {
    BrokerFixture f;
    f.compositor();
    fixtureIdentityDelay = 2000000;
    int slow = f.client(fixtureUid, 5000);
    const auto begun = std::chrono::steady_clock::now();
    start(f, slow, Type::HelloUi);
    // A pending packet is consumed once; level-triggered polling cannot spin
    // on it while the child is still discovering the compositor socket.
    pollfd idle{slow, POLLIN, 0};
    assert(poll(&idle, 1, 20) == 0);
    const auto child = f.broker.clients.at(slow).check->pid;
    assert(sendPacket(f.peers.at(slow).get(), Type::Status));
    // The clear marker report wakes once; drain it before checking idle IPC.
    pollfd marker{f.broker.clients.at(slow).check->result.get(), POLLIN, 0};
    assert(poll(&marker, 1, 1000) == 1);
    f.broker.completeChecks();
    assert(f.broker.clients.at(slow).check->lockClear);
    // The production poll set must suppress a second queued client packet,
    // otherwise it wakes continuously throughout the helper's timeout.
    auto waiting = f.broker.pollInputs();
    assert(waiting.timeout > 0 && waiting.timeout <= 250);
    assert(waiting.descriptors.size() == waiting.clientCount + 1);
    assert(poll(waiting.descriptors.data(), waiting.descriptors.size(), 20) == 0);
    assert(f.broker.clients.at(slow).check->pid == child);
    fixtureIdentityDelay = 0;
    f.process(6001, 1, fixtureUid + 1, "/fixture/niri-emaki");
    f.process(6000, 6001, fixtureUid + 1, "/fixture/qs");
    int fast = f.client(fixtureUid + 1, 6000);
    start(f, fast, Type::HelloUi);
    f.finish(fast);
    assert(f.broker.clients.at(fast).ui);
    assert(f.broker.clients.at(slow).check);
    f.finish(slow);
    assert(!f.broker.clients.contains(slow));
    assert(f.broker.clients.at(fast).ui);
    assert(std::chrono::steady_clock::now() - begun < std::chrono::seconds(1));
    pollfd closed{f.peers.at(slow).get(), POLLIN, 0};
    assert(poll(&closed, 1, 0) == 1 && (closed.revents & POLLHUP));
    // Completion must dispose of the child and its result descriptor, without
    // retrying the original registration or the packet queued behind it.
    const auto collected = std::chrono::steady_clock::now() + std::chrono::seconds(1);
    while (f.broker.identityChildren.contains(child)) {
      assert(std::chrono::steady_clock::now() < collected);
      f.broker.completeChecks();
      usleep(1000);
    }
    assert(waitpid(child, nullptr, WNOHANG) == -1 && errno == ECHILD);
    f.drain(fast);
    auto finished = f.broker.pollInputs();
    assert(finished.descriptors.size() == finished.clientCount);
    assert(poll(finished.descriptors.data(), finished.descriptors.size(), 20) == 0);
  }
  {
    BrokerFixture f;
    f.compositor();
    fixtureIdentityDelay = 550000;
    const int ui = f.client(fixtureUid, 5000);
    start(f, ui, Type::HelloUi);
    pollfd result{f.broker.clients.at(ui).check->result.get(), POLLIN, 0};
    assert(poll(&result, 1, 1000) == 1);
    assert(usleep(600000) == 0);
    f.broker.completeChecks();
    // A reply that itself missed the deadline remains refused even if it
    // completed before the parent next collected results.
    assert(!f.broker.clients.contains(ui));
  }
  for (Type denial : {Type::Cancel, Type::Lock}) {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    fixtureIdentityDelay = 2000000;
    start(f, ui, Type::Status);
    f.send(ui, denial, denial == Type::Cancel ? id : 0);
    assert(f.broker.pending.empty());
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Denied);
  }
  {
    BrokerFixture f;
    f.compositor();
    const auto ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    start(f, ui, Type::Status);
    fixtureTime += 4;
    f.finish(ui);
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Fallback);
    assert(f.broker.pending.empty() && f.broker.keys.empty());
  }
  for (bool arrivedInTime : {false, true}) {
    BrokerFixture f;
    f.compositor();
    const auto ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    fixtureTime = arrivedInTime ? 219 : 220;
    fixtureIdentityCost = 1025;
    // Keep a live provider while the prompt deadline passes during discovery.
    f.broker.clients.at(ui).since = fixtureTime;
    assert(sendPacket(f.peers.at(f.pendingFd).get(), Type::Authenticated, id));
    f.broker.handle(f.pendingFd);
    fixtureTime = 220;
    f.broker.reap();
    f.finish(f.pendingFd);
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == (arrivedInTime ? Type::Response : Type::Denied));
    assert(f.broker.pending.empty() && f.broker.keys.empty());
  }
  for (unsigned cost : {300U, 1025U}) {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    fixtureIdentityCost = cost;
    f.send(f.pendingFd, Type::Authenticated, id);
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Response);
    assert(f.broker.pending.empty());
    assert(f.broker.keys.size() == (cost <= 1024 ? 1 : 0));
  }
  {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    fixtureIdentityDelay = 2000000;
    start(f, f.pendingFd, Type::Authenticated, id);
    f.finish(f.pendingFd);
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Response);
    assert(f.broker.pending.empty() && f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    f.grant(ui, 300);
    f.send(ui, Type::Lock);
    fixtureIdentityCost = 1025;
    assert(f.request() == Type::Granted);
    assert(f.broker.keys.size() == 1);
  }
  for (bool compositorPath : {true}) {
    for (unsigned duration : {0U, 300U}) {
      for (bool marker : {false, true}) {
        BrokerFixture f;
        if (compositorPath)
          f.compositor();
        int ui = f.connectUi();
        assert(f.request() == Type::Waiting);
        const auto id = f.broker.pending.begin()->first;
        f.send(ui, Type::Submit, id, duration, "fixture-password");
        f.drain(f.pendingFd);
        // Both unknown lock state and an observed lock deny confirmation.
        fixtureDiscoveryUnavailable = !marker;
        fixtureBlocked = marker;
        f.send(f.pendingFd, Type::Authenticated, id);
        Packet reply;
        assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
        assert(reply.header.type == Type::Denied);
        assert(f.broker.pending.empty() && f.broker.keys.empty());
      }
    }
  }
  for (bool stalled : {false, true}) {
    BrokerFixture f;
    f.compositor();
    const int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    char path[] = "/tmp/emaki-lock-confirmation-XXXXXX";
    assert(mkdtemp(path));
    const std::string directory = std::string(path) + "/emaki-lock-fixture";
    assert(!mkdir(directory.c_str(), 0700));
    const std::string marker = directory + "/authentication-blocked";
    Fd file(open(marker.c_str(), O_CREAT | O_WRONLY | O_CLOEXEC, 0600));
    assert(file);
    // The real scan cannot observe the marker inside this malformed directory.
    assert(!chmod(directory.c_str(), 0755));
    fixtureRuntime = path;
    fixtureMarkerDelay = stalled ? 2000000 : 0;
    f.send(f.pendingFd, Type::Authenticated, id);
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Denied);
    assert(f.broker.pending.empty() && f.broker.keys.empty());
    assert(!unlink(marker.c_str()));
    assert(!rmdir(directory.c_str()));
    assert(!rmdir(path));
  }
  {
    BrokerFixture f;
    f.compositor();
    int ui = f.connectUi();
    assert(f.request() == Type::Waiting);
    const auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 300, "fixture-password");
    f.drain(f.pendingFd);
    fixtureIdentityDelay = 2000000;
    start(f, f.pendingFd, Type::Authenticated, id);
    fixtureTime += 4;
    f.broker.reap();
    Packet reply;
    assert(receivePacket(f.peers.at(f.pendingFd).get(), reply));
    assert(reply.header.type == Type::Fallback);
    assert(f.broker.pending.empty() && f.broker.keys.empty());
  }
  for (bool sameProcessReconnect : {false, true}) {
    BrokerFixture f;
    f.compositor();
    int original = f.connectUi();
    const int requester = f.client(0, 4000);
    const std::string name(getpwuid(fixtureUid)->pw_name);
    fixtureIdentityDelay = 200000;
    assert(sendPacket(f.peers.at(requester).get(), Type::Authenticate,
                      0, 0, name.data(), name.size()));
    f.broker.handle(requester);
    assert(f.broker.clients.at(requester).check);
    f.broker.drop(original);
    fixtureIdentityDelay = 0;
    const auto replacementPid = sameProcessReconnect ? 5000 : 5100;
    if (!sameProcessReconnect)
      f.process(replacementPid, 5001, fixtureUid, "/fixture/qs");
    fixtureTrustedPid = fixtureTrustedStart = replacementPid;
    const int replacement = f.client(fixtureUid, replacementPid);
    assert(replacement == original);
    f.send(replacement, Type::HelloUi);
    f.send(replacement, Type::Unlock);
    f.send(replacement, Type::AcknowledgeWarning);
    f.drain(replacement);
    assert(f.broker.clients.at(replacement).ui);
    // The clear-scan report may already have invalidated the old connection.
    f.finish(requester);
    Packet reply;
    assert(receivePacket(f.peers.at(requester).get(), reply));
    assert(reply.header.type == Type::Fallback);
    assert(f.broker.pending.empty() && f.broker.keys.empty());
    assert(!receivePacket(f.peers.at(replacement).get(), reply));
  }
  {
    BrokerFixture f;
    f.compositor();
    const int ui = f.client(fixtureUid, 5000);
    start(f, ui, Type::HelloUi);
    pollfd ready{f.broker.clients.at(ui).check->result.get(), POLLIN, 0};
    assert(poll(&ready, 1, 1000) == 1 && (ready.revents & POLLIN));
    // Parent work after a timely child reply cannot change its answer.
    assert(usleep(550000) == 0);
    f.broker.completeChecks();
    assert(f.broker.clients.at(ui).ui && !f.broker.clients.at(ui).check);
  }
  puts("Production broker identity progress checks passed");
}
void BrokerFixture::amountValidation() {
  for (auto duration : {300U, 420U, 86400U, Session,
                        TurnsFlag | 1U, TurnsFlag | 47U, TurnsFlag | 1000U}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, duration);
    const auto &key = f.broker.keys.begin()->second;
    assert(key.remainingTurns == turnCount(duration));
    assert(key.expires == (duration == Session ? 0 : fixtureTime +
                          (turnCount(duration) ? TurnHardCap : duration)));
  }
  for (auto duration : {1U, 240U, 299U, 301U, 86401U, 86460U,
                        TurnsFlag, TurnsFlag | 1001U, UINT32_MAX - 1}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.report(Type::TurnStart);
    assert(f.request() == Type::Waiting);
    auto id = f.broker.pending.begin()->first;
    f.drain(ui);
    f.send(ui, Type::Submit, id, duration, "fixture-password");
    Packet reply;
    assert(receivePacket(f.peers.at(ui).get(), reply));
    assert(reply.header.type == Type::Error);
    assert(!f.broker.pending.at(id).candidate.submitted());
    assert(f.broker.keys.empty());
    // An invalid amount leaves the prompt available for ordinary approval.
    f.send(ui, Type::Submit, id, 0, "fixture-password");
    f.drain(f.pendingFd);
    f.send(f.pendingFd, Type::Authenticated, id);
    assert(f.broker.pending.empty());
    assert(f.broker.keys.empty());
  }
  puts("Production broker typed amount validation checks passed");
}
void BrokerFixture::logoutLifecycle() {
  for (bool manager : {false, true}) {
    for (auto duration : {420U, Session, TurnsFlag | 3}) {
      for (const char *state : {"closing", static_cast<const char *>(nullptr)}) {
        BrokerFixture f;
        fixtureUserManager = manager;
        f.reportingTree();
        int ui = f.connectUi();
        f.matureReporter();
        f.grant(ui, duration);
        fixtureActive = false;
        fixtureSessionState = "online";
        f.broker.reap();
        assert(f.broker.keys.size() == 1);
        assert(f.request() == Type::Granted);
        // The process and session ID linger after logout. Reuse must stop
        // immediately, even before the periodic reap notices the state.
        fixtureSessionState = state;
        assert(fixtureSameProcess(*fixtureProcesses.at(4100)));
        assert(f.request() == Type::Fallback);
        f.broker.reap();
        assert(f.broker.keys.empty());
        assert(f.broker.reporters.empty());
        f.report(Type::TurnStart);
        assert(f.broker.reporters.empty());
      }
    }
  }
  puts("Production broker logout lifecycle checks passed");
}
void BrokerFixture::reporterLimits() {
  BrokerFixture f;
  const uid_t originalUid = fixtureUid;
  for (unsigned user = 0; user < 2; ++user) {
    fixtureUid = originalUid + user;
    const pid_t first = 10000 + user * 1000;
    for (pid_t n = 0; n < 257; ++n) {
      const pid_t pid = first + n;
      f.process(pid, 1, fixtureUid, "/fixture/claude");
      f.report(Type::TurnStart, "fixture-session", pid, fixtureUid);
      assert(f.broker.reporters.contains(pid) == (n < 256));
    }
    assert(f.broker.reporters.size() == (user + 1) * 256);
    // At capacity, an existing reporter still finishes its current turn.
    f.report(Type::TurnEnd, "fixture-session", first, fixtureUid);
    assert(f.broker.reporters.at(first)->completed == 1);
  }
  fixtureUid = originalUid;
  puts("Production broker per-user reporter limits passed");
}
void BrokerFixture::turnLifecycle() {
  for (bool lateCycle : {false, true}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.report(Type::TurnStart);
    assert(f.broker.reporters.at(4100)->completed == 0);
    f.drain(ui);
    assert(f.request() == Type::Waiting);
    auto id = f.broker.pending.begin()->first;
    Packet request;
    assert(receivePacket(f.peers.at(ui).get(), request));
    assert(request.header.type == Type::Request);
    std::string metadata(reinterpret_cast<const char *>(request.body.data()), request.body.size());
    assert(metadata.find("\"turnEligible\":false") != std::string::npos);
    if (lateCycle) {
      f.report(Type::TurnEnd);
      f.report(Type::TurnStart);
    }
    f.send(ui, Type::Submit, id, TurnsFlag | 3, "fixture-password");
    assert(!f.broker.pending.at(id).candidate.submitted());
    assert(f.broker.keys.empty());
    // A reporter becoming mature after the request cannot change its choices.
    f.send(ui, Type::Submit, id, 0, "fixture-password");
    f.drain(f.pendingFd);
    f.send(f.pendingFd, Type::Authenticated, id);
    assert(f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    fixtureInstalled = false;
    f.report(Type::TurnStart);
    assert(f.broker.reporters.empty());
    fixtureInstalled = true;
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    fixtureInstalled = false;
    f.report(Type::TurnEnd);
    assert(f.broker.reporters.at(4100)->completed == 1);
    f.broker.reap();
    assert(f.broker.keys.empty());
    assert(f.broker.reporters.empty());
    fixtureInstalled = true;
    f.report(Type::TurnStart);
    assert(f.broker.reporters.size() == 1);
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    assert(f.broker.keys.begin()->second.owner->pid == 4100);
    f.remaining(3);
    f.report(Type::TurnEnd);
    f.remaining(2);
    f.report(Type::TurnEnd);
    f.remaining(2);
    f.report(Type::TurnStart);
    f.report(Type::TurnStart);
    f.report(Type::TurnEnd);
    f.remaining(1);
    assert(f.request() == Type::Granted);
    f.report(Type::TurnStart);
    f.report(Type::TurnEnd);
    assert(f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.reportingTree("/fixture/codex");
    int ui = f.connectUi();
    f.report(Type::TurnComplete, "session:turn-0");
    f.report(Type::TurnComplete, "session:turn-1");
    const auto id = f.submitTurn(ui);
    assert(!f.broker.pending.at(id).candidate.submitted());
    assert(f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    f.process(4200, 1, fixtureUid, "/fixture/claude");
    f.process(4201, 4200, fixtureUid, "/fixture/report");
    f.report(Type::TurnStart, "fixture-session", 4201);
    f.report(Type::TurnEnd, "fixture-session", 4201);
    f.remaining(3);
    f.process(4202, 1, fixtureUid, "/fixture/report");
    f.report(Type::TurnEnd, "fixture-session", 4202);
    f.remaining(3);
    f.process(4203, 4100, fixtureUid + 1, "/fixture/report");
    f.report(Type::TurnEnd, "fixture-session", 4203, fixtureUid + 1);
    f.remaining(3);
    // A report descriptor retained across a reparenting loses its authority.
    f.process(4301, 4300, fixtureUid, "/fixture/report");
    f.process(4300, 4100, fixtureUid, "/fixture/runner");
    int reportFd = f.client(fixtureUid, 4301);
    f.process(4301, 4200, fixtureUid, "/fixture/report");
    f.send(reportFd, Type::TurnEnd, 0, 0, "fixture-session");
    f.remaining(3);
    // A helper whose intermediate parent has left the granted tree is foreign.
    f.process(4301, 4300, fixtureUid, "/fixture/report");
    f.process(4300, 4200, fixtureUid, "/fixture/runner");
    f.report(Type::TurnEnd, "fixture-session", 4301);
    f.remaining(3);
    // The same account cannot spend the key through a sibling requester.
    f.process(4001, 4200, fixtureUid, "/fixture/requester");
    assert(f.request() == Type::Waiting);
    f.send(ui, Type::RevokeAll);
    assert(f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    auto id = f.submitTurn(ui);
    assert(!f.broker.pending.at(id).candidate.submitted());
    assert(f.broker.keys.empty());
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    auto expires = f.broker.keys.begin()->second.expires;
    assert(expires == fixtureTime + TurnHardCap);
    fixtureTime = expires - 1;
    f.broker.reap();
    f.remaining(3);
    // Another start never moves the hard cap, even when an end was lost.
    f.report(Type::TurnStart);
    assert(f.broker.keys.begin()->second.expires == expires);
    fixtureTime = expires;
    f.broker.reap();
    assert(f.broker.keys.empty());
  }
  for (bool reuse : {false, true}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    if (reuse)
      ++f.process(4100, 1, fixtureUid, "/fixture/claude")->start;
    else
      fixtureDead.insert(4100);
    f.broker.reap();
    assert(f.broker.keys.empty());
    assert(f.broker.reporters.empty());
  }
  for (auto duration : {420U, Session}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, duration);
    auto expires = f.broker.keys.begin()->second.expires;
    f.report(Type::TurnEnd);
    assert(f.broker.keys.size() == 1);
    assert(f.broker.keys.begin()->second.expires == expires);
    assert(!f.broker.keys.begin()->second.reporter);
  }
  for (auto type : {Type::TurnEnd, Type::TurnStart, Type::TurnComplete}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    auto id = f.submitTurn(ui);
    f.report(type, type == Type::TurnEnd ? "fixture-session" :
                   type == Type::TurnStart ? "replacement-session" :
                                             "fixture-session:turn-1");
    f.send(f.pendingFd, Type::Authenticated, id);
    assert(f.broker.pending.empty());
    assert(f.broker.keys.empty());
  }
  for (auto type : {Type::TurnStart, Type::TurnComplete}) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, TurnsFlag | 3);
    f.report(type, type == Type::TurnStart ? "replacement-session" :
                                           "fixture-session:turn-1");
    assert(f.broker.keys.empty());
  }
  puts("Production broker turn lifecycle checks passed");
}

void BrokerFixture::timestampLifecycle() {
  // Every path that removes a key must end its recorded timestamp identities.
  for (unsigned cause = 0; cause < 10; ++cause) {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.matureReporter();
    f.grant(ui, cause == 3 || cause == 4 || cause == 9 ? TurnsFlag | 1 : 420);
    const auto key = f.broker.keys.begin()->first;
    const auto &served = f.broker.timestamps.served.at(key);
    assert(served.uid == fixtureUid && served.parents.size() == 1);
    assert((served.parents.front() == TimestampParent{4001, 3900, 4001}));
    assert(f.request() == Type::Granted);
    assert(f.broker.timestamps.served.at(key).parents.size() == 1);
    switch (cause) {
    case 0: f.send(ui, Type::Revoke, key); break;
    case 1: f.send(ui, Type::RevokeAll); break;
    case 2: fixtureTime += 420; f.broker.reap(); break;
    case 3: f.report(Type::TurnEnd); break;
    case 4: fixtureTime += TurnHardCap; f.broker.reap(); break;
    case 5: fixtureDead.insert(4100); f.broker.reap(); break;
    case 6: fixtureSessionState = "closing"; f.broker.reap(); break;
    case 7: fixtureInstalled = false; f.broker.reap(); break;
    case 8: f.broker.endKeys(); break;
    case 9: f.report(Type::TurnStart, "replacement-session"); break;
    }
    assert(f.broker.keys.empty());
    assert(f.broker.timestamps.served.empty());
    assert(f.broker.timestamps.ended == std::vector<std::uint64_t>{key});
  }
  {
    BrokerFixture f;
    f.reportingTree();
    int ui = f.connectUi();
    f.grant(ui, 420);
    auto key = f.broker.keys.begin()->first;
    f.process(4002, 4100, fixtureUid, "/fixture/bash");
    fixtureProcesses.at(4000)->ppid = 4002;
    assert(f.request() == Type::Granted);
    assert(f.broker.timestamps.served.at(key).parents.size() == 2);
    f.broker.timestamps.available = false;
    assert(f.request() == Type::Waiting);
    assert(f.broker.keys.empty());
    assert(f.broker.timestamps.ended == std::vector<std::uint64_t>{key});
    // An ordinary password can still succeed, but cannot create an untracked key.
    auto id = f.broker.pending.begin()->first;
    f.send(ui, Type::Submit, id, 420, "fixture-password");
    f.drain(f.pendingFd);
    f.send(f.pendingFd, Type::Authenticated, id);
    assert(f.broker.keys.empty() && f.broker.timestamps.served.empty());
  }
  puts("Production broker timestamp lifecycle checks passed");
}
void BrokerFixture::warningLifecycle() {
  {
    BrokerFixture f;
    int ui = f.connectUi();
    assert(f.broker.users.at(fixtureUid).warning);
    f.broker.drop(ui);
    // The old login has ended: its grants are gone, acceptance survives.
    f.broker.users.at(fixtureUid).session = "previous-login";
    ui = f.client(fixtureUid, 5000);
    f.send(ui, Type::HelloUi);
    assert(f.broker.users.at(fixtureUid).warning);
    f.send(ui, Type::Unlock);
    assert(!f.broker.warnings.accepted(fixtureUid + 1));
    fixtureWarnings.erase(fixtureUid);
    f.broker.reap();
    assert(!f.broker.users.at(fixtureUid).warning);
    fixtureWarningWritable = false;
    f.send(ui, Type::AcknowledgeWarning);
    assert(!f.broker.users.at(fixtureUid).warning);
    fixtureWarningWritable = true;
    f.send(ui, Type::AcknowledgeWarning);
    assert(f.broker.users.at(fixtureUid).warning);
    ++fixtureInstallation;
    f.broker.reap();
    assert(!f.broker.users.at(fixtureUid).warning);
    f.send(ui, Type::AcknowledgeWarning);
    assert(f.broker.users.at(fixtureUid).warning);
    // A fresh broker restores the same root-owned acceptance record.
    Broker restarted;
    assert(restarted.warnings.accepted(fixtureUid));
  }
  puts("Production broker warning acceptance checks passed");
}
void BrokerFixture::wakeSchedule() {
  // Deadlines arrive rounded up; an expired one polls without waiting.
  assert(earlierTimeout(-1, 1200) == 1200);
  assert(earlierTimeout(-1, 0) == 0);
  assert(earlierTimeout(-1, -40) == 0);
  assert(earlierTimeout(RevalidationTick, 1200) == RevalidationTick);
  assert(earlierTimeout(RevalidationTick, 3) == 3);
  assert(earlierTimeout(-1, std::numeric_limits<long long>::max()) ==
         std::numeric_limits<int>::max());
  BrokerFixture f;
  // Nothing held: no tick, the next descriptor event is the next work.
  assert(f.broker.pollInputs().timeout == -1);
  int ui = f.connectUi();
  assert(f.broker.pollInputs().timeout == RevalidationTick);
  f.grant(ui, Session);
  f.broker.drop(ui);
  assert(f.broker.clients.empty() && f.broker.keys.size() == 1);
  // A key's owner, session and package are re-read on every tick.
  assert(f.broker.pollInputs().timeout == RevalidationTick);
  fixtureDead.insert(4001);
  f.broker.reap();
  fixtureDead.clear();
  assert(f.broker.keys.empty());
  const auto limit = std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (!f.broker.identityChildren.empty()) {
    // An uncollected helper is reaped only by a later tick.
    assert(f.broker.pollInputs().timeout == RevalidationTick);
    assert(std::chrono::steady_clock::now() < limit);
    f.broker.collectChildren();
    usleep(1000);
  }
  assert(f.broker.pollInputs().timeout == -1);
  // Timestamp journals wait for worker and retry deadlines.
  f.broker.timestamps.served[99] = {fixtureUid, {}};
  assert(f.broker.pollInputs().timeout == RevalidationTick);
  f.broker.timestamps.served.clear();
  // A reporter's process, session and silence deadline are re-read too.
  f.reportingTree();
  f.matureReporter();
  assert(f.broker.clients.empty() && !f.broker.reporters.empty());
  assert(f.broker.pollInputs().timeout == RevalidationTick);
  fixtureTime += TurnHardCap;
  f.broker.reap();
  assert(f.broker.reporters.empty());
  while (!f.broker.identityChildren.empty()) {
    assert(std::chrono::steady_clock::now() < limit);
    f.broker.collectChildren();
    usleep(1000);
  }
  assert(f.broker.pollInputs().timeout == -1);
  puts("Production broker sleeps without held state and ticks with it");
}
void BrokerFixture::statusRefresh() {
  BrokerFixture f;
  int ui = f.connectUi();
  auto quiet = [&] {
    pollfd item{f.peers.at(ui).get(), POLLIN, 0};
    return poll(&item, 1, 0) == 0;
  };
  auto refreshed = [&](const char *expected) {
    Packet packet;
    assert(receivePacket(f.peers.at(ui).get(), packet));
    assert(packet.header.type == Type::Keys);
    const std::string body(reinterpret_cast<const char *>(packet.body.data()),
                           packet.body.size());
    assert(body == f.broker.status(fixtureUid));
    assert(body.find(expected) != std::string::npos);
    assert(quiet());
  };
  assert(quiet());
  // The provider already shows this snapshot.
  f.broker.refreshStatus();
  assert(quiet());
  f.grant(ui, 420);
  f.broker.refreshStatus();
  assert(quiet());
  // A visible countdown changes every whole second.
  ++fixtureTime;
  f.broker.refreshStatus();
  refreshed("\"remainingSeconds\":419");
  f.broker.refreshStatus();
  assert(quiet());
  // A lock is delivered by the next refresh, as before.
  f.send(ui, Type::Lock);
  assert(quiet());
  f.broker.refreshStatus();
  refreshed("\"promptAvailable\":false");
  // A heartbeat reply is a snapshot the provider shows.
  f.send(ui, Type::Unlock);
  f.drain(ui);
  f.send(ui, Type::Status);
  f.drain(ui);
  f.broker.refreshStatus();
  assert(quiet());
  // Request metadata overwrites part of the provider's view: resend in full.
  f.send(ui, Type::RevokeAll);
  f.drain(ui);
  assert(f.request() == Type::Waiting);
  f.drain(ui);
  f.broker.refreshStatus();
  refreshed("\"promptAvailable\":true");
  // A new provider starts from an unknown view.
  f.broker.drop(ui);
  ui = f.connectUi();
  f.broker.refreshStatus();
  assert(quiet());
  f.broker.users.at(fixtureUid).shown.clear();
  f.broker.refreshStatus();
  refreshed("\"keys\":[]");
  puts("Production broker refreshes status only when the provider's view changes");
}
void BrokerFixture::idleLoop() {
  BrokerFixture f;
  int pair[2];
  assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0);
  f.broker.listener.reset(pair[0]);
  Fd peer(pair[1]);
  const pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    installStopHandlers();
    _exit(f.broker.run());
  }
  // Field 3 counts the times the task was scheduled in, i.e. its wake-ups.
  auto wakes = [&] {
    const auto path = "/proc/" + std::to_string(child) + "/schedstat";
    unsigned long long count = 0;
    FILE *file = fopen(path.c_str(), "re");
    if (file && fscanf(file, "%*u %*u %llu", &count) != 1)
      count = 0;
    if (file)
      fclose(file);
    return count;
  };
  usleep(200000);
  const auto before = wakes();
  usleep(1000000);
  const auto after = wakes();
  const bool alive = waitpid(child, nullptr, WNOHANG) == 0;
  // A signal must still end an unbounded wait promptly.
  const auto signalled = std::chrono::steady_clock::now();
  kill(child, SIGTERM);
  int status = 0;
  pid_t reaped = 0;
  while (!(reaped = waitpid(child, &status, WNOHANG)) &&
         std::chrono::steady_clock::now() - signalled < std::chrono::seconds(2))
    usleep(1000);
  if (!reaped) {
    kill(child, SIGKILL);
    waitpid(child, nullptr, 0);
  }
  const auto stopped = std::chrono::steady_clock::now() - signalled;
  std::printf("Idle broker loop: %llu wake-ups in 1 s, stopped in %lld ms\n",
              after - before,
              static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(stopped).count()));
  assert(alive && before > 0);
  assert(after - before <= 1);
  assert(reaped == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
  assert(stopped < std::chrono::milliseconds(500));
}
// SIGTERM after the flag check and before an unbounded poll(): the stop pipe must end
// the wait; without the pipe the loop must fall back to the bounded revalidation tick.
void BrokerFixture::stopBeforePoll(bool pipe) {
  BrokerFixture f;
  int pair[2];
  assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0);
  f.broker.listener.reset(pair[0]);
  Fd peer(pair[1]);
  const auto started = std::chrono::steady_clock::now();
  const pid_t child = fork();
  assert(child >= 0);
  if (!child) {
    installStopHandlers();
    if (!pipe) {
      close(stopRead);
      close(stopWrite);
      stopRead = stopWrite = -1;
    }
    fixtureStopAtClock = true;
    _exit(f.broker.run());
  }
  int status = 0;
  pid_t reaped = 0;
  while (!(reaped = waitpid(child, &status, WNOHANG)) &&
         std::chrono::steady_clock::now() - started < std::chrono::seconds(2))
    usleep(1000);
  if (!reaped) {
    kill(child, SIGKILL);
    waitpid(child, nullptr, 0);
  }
  const auto stopped = std::chrono::steady_clock::now() - started;
  std::printf("Stop before poll (%s): stopped in %lld ms\n", pipe ? "stop pipe" : "no pipe, tick",
              static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(stopped).count()));
  assert(reaped == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
  assert(stopped < std::chrono::milliseconds(pipe ? 100 : 500));
}
}
int main(int argc, char **argv) {
  assert(fixtureUid != 0);
  if (argc == 2) {
    const std::string_view test = argv[1];
    if (test == "idle-registration")
      BrokerFixture::identityIdleBoundary(false);
    else if (test == "idle-authentication")
      BrokerFixture::identityIdleBoundary(true);
    else if (test == "status-capacity")
      BrokerFixture::statusTransport(false);
    else if (test == "status-oversize")
      BrokerFixture::statusTransport(true);
    else
      return 2;
    return 0;
  }
  assert(argc == 1);
  BrokerFixture::identityIdleBoundary(false);
  BrokerFixture::identityIdleBoundary(true);
  BrokerFixture::statusTransport(false);
  BrokerFixture::statusTransport(true);
  BrokerFixture::warningLifecycle();
  BrokerFixture fixture;
  fixture.run();
  BrokerFixture::capacityLimits();
  BrokerFixture::revocationAdmission();
  BrokerFixture::ownerIdentity();
  BrokerFixture::providerIdentity();
  BrokerFixture::identityScheduling();
  BrokerFixture::amountValidation();
  BrokerFixture::turnLifecycle();
  BrokerFixture::logoutLifecycle();
  BrokerFixture::reporterLimits();
  BrokerFixture::timestampLifecycle();
  BrokerFixture::wakeSchedule();
  BrokerFixture::statusRefresh();
  BrokerFixture::idleLoop();
  BrokerFixture::stopBeforePoll(true);
  BrokerFixture::stopBeforePoll(false);
}
