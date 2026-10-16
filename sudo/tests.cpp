// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <poll.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
namespace {
int failures = 0;
void check(bool success, const char *name) {
  if (!success) {
    std::fprintf(stderr, "FAIL: %s\n", name);
    ++failures;
  }
}
volatile sig_atomic_t readSignals = 0;
void readSignal(int) { readSignals = 1; }
void boundedReads() {
  const char *tmp = std::getenv("TMPDIR");
  auto pattern = std::string(tmp && *tmp ? tmp : "/tmp") + "/emaki-bounded-XXXXXX";
  char *directory = mkdtemp(pattern.data());
  check(directory != nullptr, "create bounded-read fixture");
  if (!directory)
    return;
  const auto path = pattern + "/input";
  const std::string content(8192, 'x');
  {
    Fd output(open(path.c_str(), O_CREAT | O_WRONLY | O_CLOEXEC, 0600));
    check(output && write(output.get(), content.data(), content.size()) ==
                        static_cast<ssize_t>(content.size()),
          "write multi-page bounded-read fixture");
  }
  check(readBounded(path, content.size()) == content,
        "exact bound succeeds only after EOF");
  check(readBounded(path, content.size() - 1).empty(),
        "oversize input rejects without returning a prefix");
  check(readBounded(path, 0).empty(), "zero bound refuses nonempty input");
  check(readBounded(pattern, 8192).empty(), "read failure returns no content");
  unlink(path.c_str());

  check(!mkfifo(path.c_str(), 0600), "create short-read stream");
  struct sigaction handler {}, previous {};
  handler.sa_handler = readSignal;
  sigemptyset(&handler.sa_mask);
  check(!sigaction(SIGUSR1, &handler, &previous), "install read interruption handler");
  const auto reader = getpid();
  const auto writer = fork();
  if (!writer) {
    Fd output(open(path.c_str(), O_WRONLY | O_CLOEXEC));
    if (!output || write(output.get(), "first", 5) != 5)
      _exit(1);
    usleep(20000);
    kill(reader, SIGUSR1);
    usleep(20000);
    _exit(write(output.get(), "second", 6) == 6 ? 0 : 1);
  }
  check(writer > 0, "start short-read writer");
  if (writer > 0) {
    check(readBounded(path, 11) == "firstsecond",
          "short and interrupted reads continue through EOF");
    int status = 0;
    check(waitpid(writer, &status, 0) == writer && WIFEXITED(status) &&
              WEXITSTATUS(status) == 0 && readSignals,
          "short-read writer delivered both chunks and interruption");
  }
  sigaction(SIGUSR1, &previous, nullptr);
  unlink(path.c_str());
  rmdir(directory);

  const auto page = static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
  constexpr std::size_t slices = 256;
  auto *mapping = static_cast<char *>(mmap(nullptr, page * slices, PROT_NONE,
                                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
  check(mapping != MAP_FAILED, "reserve many procfs mappings");
  if (mapping == MAP_FAILED)
    return;
  for (std::size_t i = 0; i < slices; i += 2)
    check(!mprotect(mapping + i * page, page, PROT_READ), "split procfs mapping");
  const auto maps = readBounded("/proc/self/maps", 8 * 1024 * 1024);
  check(maps.size() > page, "read real procfs maps beyond one page");
  for (std::size_t i = 1; i + 1 < slices; ++i) {
    char range[64];
    std::snprintf(range, sizeof(range), "\n%lx-%lx ",
                  reinterpret_cast<unsigned long>(mapping + i * page),
                  reinterpret_cast<unsigned long>(mapping + (i + 1) * page));
    check(maps.find(range) != std::string::npos,
          "complete procfs read includes every interior mapping");
  }
  check(readBounded("/proc/self/maps", page).empty(),
        "oversized procfs maps reject instead of accepting one page");
  munmap(mapping, page * slices);
}
void decisions() {
  KeyDecision d{true, true, true, true, true, true, false, 1000, 999};
  check(mayReuse(d), "authorized live tree can reuse before expiry");
  d.now = 1000;
  check(!mayReuse(d), "expiry boundary rejects");
  d.now = 1001;
  check(!mayReuse(d), "after expiry rejects");
  d.expires = 0;
  check(mayReuse(d), "session lifetime has no wall timeout");
  auto disabled = d;
  disabled.marker = false;
  check(!mayReuse(disabled), "optional package removal blocks reuse");
  disabled = d;
  disabled.warning = false;
  check(!mayReuse(disabled), "warning must be acknowledged");
  disabled = d;
  disabled.ownerAlive = false;
  check(!mayReuse(disabled), "owner exit revokes");
  disabled = d;
  disabled.ancestry = false;
  check(!mayReuse(disabled), "changed ancestor rejects");
  disabled = d;
  disabled.sameUid = false;
  check(!mayReuse(disabled), "other uid rejects");
  disabled = d;
  disabled.sameOwner = false;
  check(!mayReuse(disabled), "unrelated tree rejects");
  disabled = d;
  disabled.retry = true;
  check(!mayReuse(disabled), "same sudo retry rejects reuse");
  for (auto n : {300U, 420U, 85800U, 86400U, Session})
    check(validDuration(n), "typed duration and session accepted");
  for (auto n : {0U, 1U, 240U, 299U, 301U, 86399U, 86401U, 86460U,
                 UINT32_MAX - 1})
    check(!validDuration(n), "out-of-range and fractional-minute durations rejected");
  for (auto n : {1U, 2U, 47U, 999U, 1000U}) {
    check(validDuration(TurnsFlag | n), "typed turn count accepted");
    check(turnCount(TurnsFlag | n) == n, "turn count decodes without time ambiguity");
  }
  for (auto n : {TurnsFlag, TurnsFlag | 1001U, TurnsFlag | 0x40000000U})
    check(!validDuration(n), "out-of-range turn count rejected");
  check(turnCount(Session) == 0 && turnCount(420) == 0,
        "time and session have no turn count");
}
void lockMarkers() {
  char path[] = "/tmp/emaki-sudo-lock-tests-XXXXXX";
  char *created = mkdtemp(path);
  check(created != nullptr, "create isolated runtime fixture");
  if (!created)
    return;
  Fd runtime(open(path, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  bool unavailable = true;
  check(!authenticationBlocked(runtime.get(), getuid(), &unavailable) && !unavailable,
        "no supervisor request permits authentication");
  check(authenticationBlocked(-1, getuid()),
        "missing runtime blocks authentication");
  check(authenticationBlocked(runtime.get(), getuid() + 1),
        "other runtime owner blocks authentication");
  check(!mkdirat(runtime.get(), "emaki-lock-fixture", 0700),
        "create lock fixture");
  Fd lock(openat(runtime.get(), "emaki-lock-fixture",
                 O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  check(!authenticationBlocked(runtime.get(), getuid()),
        "empty supervisor directory permits authentication");
  Fd marker(openat(lock.get(), "authentication-blocked",
                   O_CREAT | O_WRONLY | O_CLOEXEC, 0600));
  check(bool(marker), "create supervisor marker");
  check(authenticationBlocked(runtime.get(), getuid(), &unavailable) && !unavailable,
        "supervisor marker blocks independently of frontend");
  marker.reset();
  unlinkat(lock.get(), "authentication-blocked", 0);
  check(!symlinkat("/does-not-exist", lock.get(), "authentication-blocked"),
        "create symlink marker fixture");
  check(authenticationBlocked(runtime.get(), getuid()),
        "symlink marker blocks without following");
  unlinkat(lock.get(), "authentication-blocked", 0);
  fchmod(lock.get(), 0755);
  check(authenticationBlocked(runtime.get(), getuid()),
        "unsafe lock directory blocks");
  fchmod(lock.get(), 0700);
  lock.reset();
  unlinkat(runtime.get(), "emaki-lock-fixture", AT_REMOVEDIR);
  check(!symlinkat("/does-not-exist", runtime.get(), "emaki-lock-fixture"),
        "create symlink directory fixture");
  check(authenticationBlocked(runtime.get(), getuid()),
        "symlink lock directory blocks without following");
  unlinkat(runtime.get(), "emaki-lock-fixture", 0);
  for (unsigned n = 0; n < 1025; ++n) {
    auto name = std::to_string(n);
    Fd fixture(openat(runtime.get(), name.c_str(),
                      O_CREAT | O_WRONLY | O_CLOEXEC, 0600));
    check(bool(fixture), "create bounded scan fixture");
    if (n == 299)
      check(!authenticationBlocked(runtime.get(), getuid(), &unavailable) && !unavailable,
            "300 unrelated entries do not block marker discovery");
  }
  check(authenticationBlocked(runtime.get(), getuid(), &unavailable) && unavailable,
        "excessive runtime entries are unavailable, not an observed lock");
  for (unsigned n = 0; n < 1025; ++n)
    unlinkat(runtime.get(), std::to_string(n).c_str(), 0);
  fchmod(runtime.get(), 0755);
  check(authenticationBlocked(runtime.get(), getuid()),
        "unsafe runtime permissions block");
  runtime.reset();
  rmdir(path);
}
void heartbeats() {
  check(freshHeartbeat(100, 100), "new heartbeat accepted");
  check(freshHeartbeat(100, 103), "heartbeat deadline included");
  check(!freshHeartbeat(100, 104),
        "stopped frontend expires after three seconds");
  check(!freshHeartbeat(100, 99), "clock reversal fails closed");
  check(!freshHeartbeat(100, UINT64_MAX), "clock failure fails closed");
}
void requesters() {
  const auto knownRequester = [](const std::string &executable,
                                   const std::vector<std::string> &arguments) {
    const auto label = emaki::sudo_channel::knownRequester(executable, arguments);
    // Keep every recognized result within the broker's status-row proof,
    // including JSON quotes and the maximum two-byte escaping per byte.
    check(label.size() <= 11, "requester label fits status row bound");
    check(escapeJson(label).size() <= 24,
          "escaped requester label fits status row bound");
    return label;
  };
  check(knownRequester("/opt/bin/claude", {}) == "Claude Code",
        "named native requester is recognized");
  check(knownRequester("/home/test/.local/share/claude/versions/2.1.296", {}) ==
            "Claude Code", "versioned native requester uses its product name");
  check(knownRequester("/opt/bin/codex", {}) == "Codex",
        "named executable is recognized");
  check(knownRequester("/opt/bin/node", {"node", "/opt/node_modules/@anthropic-ai/claude-code/cli.js"}) ==
            "Claude Code", "interpreted requester uses its entry point");
  check(knownRequester("/opt/bin/node", {"node", "/opt/node_modules/@openai/codex/bin/codex.js"}) ==
            "Codex", "interpreted entry point is recognized");
  for (auto executable : {"/opt/bin/python", "/opt/versions/2.1.296",
                          "/opt/claude/versions/2.1.296/other", "/opt/bin/node"})
    check(knownRequester(executable, {"claude"}).empty(),
          "unknown executable or spoofed argv zero stays generic");
  check(knownRequester("/opt/bin/node", {"node", "-e", "/opt/node_modules/@openai/codex/bin/codex.js"}).empty(),
        "product mentioned in command arguments is not a requester identity");
}
void uiPeers() {
  check(validUiArguments({"qs", "-n", "-p", "/retained/generation"}),
        "normal retained-generation shell arguments accepted");
  check(validUiArguments({"qs", "-p", "/installed/shell"}),
        "installed shell arguments accepted");
  check(validUiArguments({"qs", "-n", "--path", "/retained/generation"}),
        "long path option accepted");
  for (const auto &args : std::vector<std::vector<std::string>>{
           {},
           {"qs"},
           {"qs", "-p", ""},
           {"qs", "-n", "-p", ""},
           {"qs", "-n", "-p", "/shell", "extra"},
           {"qs", "--config", "/shell"},
           {"qs", "-p", std::string(4097, 'x')}})
    check(!validUiArguments(args),
          "unknown or unbounded shell invocation rejects");
  check(validManagerArguments({"systemd", "--user"}),
        "user manager invocation accepted");
  check(!validManagerArguments({"systemd"}),
        "system manager invocation rejects");
  check(!validManagerArguments({"systemd", "--system"}),
        "non-user manager rejects");
  UiPeerDecision service;
  service.sameUid = service.parentUid = true;
  service.stableUi = service.stableParent = true;
  service.trustedUi = service.directParent = service.uiArguments = true;
  service.trustedManager = service.exactUnit = service.managerArguments = true;
  auto compositor = service;
  compositor.trustedManager = compositor.exactUnit = compositor.managerArguments = false;
  compositor.trustedCompositor = compositor.sessionController = true;
  check(!trustedUiPeer({}), "missing observations deny provider registration");
  check(trustedUiPeer(service), "pinned service shell under genuine user manager accepted");
  check(trustedUiPeer(compositor), "pinned shell under active session compositor accepted");
  bool UiPeerDecision::*common[] = {
      &UiPeerDecision::sameUid, &UiPeerDecision::parentUid,
      &UiPeerDecision::stableUi, &UiPeerDecision::stableParent,
      &UiPeerDecision::trustedUi, &UiPeerDecision::directParent,
      &UiPeerDecision::uiArguments};
  for (auto accepted : {service, compositor})
    for (auto gate : common) {
      auto denied = accepted;
      denied.*gate = false;
      check(!trustedUiPeer(denied), "both startup paths require all process identity gates");
    }
  for (auto gate : {&UiPeerDecision::trustedManager, &UiPeerDecision::exactUnit,
                    &UiPeerDecision::managerArguments}) {
    auto denied = service;
    denied.*gate = false;
    check(!trustedUiPeer(denied), "service path requires manager identity, unit and arguments");
  }
  for (auto gate : {&UiPeerDecision::trustedCompositor, &UiPeerDecision::sessionController}) {
    auto denied = compositor;
    denied.*gate = false;
    check(!trustedUiPeer(denied), "compositor path rejects fake executable and unrelated compositor");
  }
  auto ordinary = compositor;
  ordinary.trustedCompositor = ordinary.sessionController = false;
  check(!trustedUiPeer(ordinary), "user-started shell cannot register as provider");
  ordinary.trustedCompositor = ordinary.exactUnit = ordinary.managerArguments = true;
  check(!trustedUiPeer(ordinary), "mixed incomplete startup paths do not grant authority");
  ordinary.trustedCompositor = false;
  ordinary.trustedManager = ordinary.sessionController = true;
  ordinary.exactUnit = false;
  check(!trustedUiPeer(ordinary), "controller observation cannot replace service unit identity");
  Process provider;
  provider.pid = 4242;
  provider.start = 123456;
  const std::string trustedReply =
      "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":4242,\"start_time\":123456}}}\n";
  check(trustedShellReply(trustedReply, provider), "exact trusted connection identity");
  ++provider.start;
  check(!trustedShellReply(trustedReply, provider), "recycled provider PID rejects");
  --provider.start;
  ++provider.pid;
  check(!trustedShellReply(trustedReply, provider), "satellite child PID rejects");
  --provider.pid;
  for (const auto &reply : {
      std::string("{\"Ok\":{\"TrustedShellIdentity\":null}}\n"),
      std::string("{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":4242,\"start_time\":123456,\"pid\":4242}}}\n"),
      std::string("{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":04242,\"start_time\":123456}}}\n"),
      std::string("{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":4242,\"start_time\":18446744073709551616}}}\n"),
      trustedReply.substr(0, trustedReply.size() - 1), trustedReply + "{}\n",
      std::string(513, ' ') + trustedReply})
    check(!trustedShellReply(reply, provider), "malformed or revoked connection rejects");
  check(parseSessionController("CONTROLLER=:1.42\n") == ":1.42",
        "unique system bus controller name accepted");
  check(parseSessionController("UID=1000\nCONTROLLER=:12.345\nACTIVE=1\n") == ":12.345",
        "controller extracted from bounded session state");
  for (const auto &state : std::vector<std::string>{
           "", "CONTROLLER=:1.42", "CONTROLLER=\n", "CONTROLLER=org.example.Service\n",
           "CONTROLLER=1.42\n", "CONTROLLER=:.42\n", "CONTROLLER=:1.\n",
           "CONTROLLER=:1.2.3\n", "CONTROLLER=:a.2\n", "CONTROLLER=:1.2x\n",
           "CONTROLLER=\":1.42\"\n", "CONTROLLER= :1.42\n", "CONTROLLER=:1.42 \n",
           "CONTROLLER=:1.42\r\n", "CONTROLLER=:1.42\nCONTROLLER=:1.42\n",
           "CONTROLLER=:1.42\nCONTROLLER=:2.42\n",
           "CONTROLLER=:1.42\n" + std::string(16384, 'x'),
           std::string("CONTROLLER=:1.42\0\n", 18)})
    check(parseSessionController(state).empty(), "malformed or ambiguous controller state rejects");
}
void peers() {
  check(privilegedPamPeer(0, 0, 0, true, true),
        "root sudo with mapped module passes peer gate");
  check(!privilegedPamPeer(1000, 0, 0, true, true),
        "nonroot socket peer rejects");
  check(!privilegedPamPeer(0, 1000, 0, true, true),
        "dropped privilege peer rejects");
  check(!privilegedPamPeer(0, 0, 1, true, true),
        "terminal sudo bypasses service");
  check(!privilegedPamPeer(0, 0, 0, false, true), "other executable rejects");
  check(!privilegedPamPeer(0, 0, 0, true, false),
        "missing trusted PAM mapping rejects");
}
void candidates() {
  GrantCandidate noSubmission;
  check(!noSubmission.confirm(true, 1, true, true, true),
        "authentication without password submission cannot mint grant");
  GrantCandidate live;
  check(live.submit(420, 1), "first submission records candidate");
  check(!live.submit(660, 1), "second password submission rejects");
  check(live.confirm(true, 1, true, true, true),
        "successful PAM verdict activates candidate");
  check(!live.confirm(true, 1, true, true, true),
        "replayed verdict cannot activate twice");
  for (unsigned reason = 0; reason < 6; ++reason) {
    GrantCandidate revoked;
    check(revoked.submit(reason == 5 ? 0 : 420, 1), "candidate submitted");
    check(!revoked.confirm(reason != 0, reason == 1 ? 2 : 1, reason != 2,
                           reason != 3, reason != 4),
          "wrong password, revoke, removal, warning, exit or no-key approval "
          "denies grant");
  }
  GrantCandidate invalid;
  check(!invalid.submit(42, 0), "invalid lifetime creates no candidate");
  check(invalid.submit(Session, 0), "session lifetime candidate accepted");
  check(invalid.confirm(true, 0, true, true, true), "session grant confirms");
}
void wipe() {
  Secret secret;
  if (!secret.valid()) {
    std::fprintf(stderr, "FAIL: locked memory unavailable (%s)\n",
                 strerror(errno));
    ++failures;
    return;
  }
  const unsigned char fixture[] = {0x61, 0x62, 0x63};
  check(secret.assign(fixture, sizeof(fixture)), "secret assigned");
  check(validPassword(secret), "bounded password accepted");
  const auto child = fork();
  check(child >= 0, "fork secret-isolation fixture");
  if (!child) {
    const bool cleared = std::all_of(secret.data(), secret.data() + secret.capacity(),
                                     [](unsigned char value) { return value == 0; });
    _exit(cleared ? 0 : 1);
  }
  if (child > 0) {
    int status = 0;
    check(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
          "discovery children inherit no password bytes");
    check(!memcmp(secret.data(), fixture, sizeof(fixture)), "parent retains password until transfer");
  }
  secret.wipe();
  check(secret.size() == 0, "wipe resets length");
  check(std::all_of(secret.data(), secret.data() + secret.capacity(),
                    [](auto c) { return c == 0; }),
        "wipe clears full allocation");
  check(!validPassword(secret), "empty password rejects");
  const unsigned char newline[] = {0x61, 10};
  secret.assign(newline, sizeof(newline));
  check(!validPassword(secret), "line terminator rejects");
  const unsigned char nul[] = {0x61, 0};
  secret.assign(nul, sizeof(nul));
  check(!validPassword(secret), "NUL rejects");
  memset(secret.data(), 0x61, MaxPassword + 1);
  secret.setSize(MaxPassword + 1);
  check(!validPassword(secret), "oversized password rejects");
  secret.wipe();
}
void transport() {
  int pair[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair)) {
    if (errno == EPERM) {
      std::puts("SKIP socket integration: sandbox denies AF_UNIX (EPERM)");
      return;
    }
    check(false, "socketpair");
    return;
  }
  Fd a(pair[0]), b(pair[1]);
  pid_t pid = 0;
  uid_t uid = 0;
  errno = 0;
  bool peerOk = peerCredentials(a.get(), pid, uid);
  if (!peerOk && errno == EPERM)
    std::puts(
        "SKIP peer-credential integration: sandbox denies SO_PEERCRED (EPERM)");
  else
    check(peerOk && pid == getpid() && uid == getuid(),
          "SO_PEERCRED is actual socket peer");
  if (peerOk) {
    errno = 0;
    auto peer = inspectPeer(a.get(), pid, uid);
    if (!peer && errno == EPERM)
      std::puts(
          "SKIP peer-pidfd integration: sandbox denies SO_PEERPIDFD (EPERM)");
    else
      check(peer && sameProcess(*peer),
            "kernel peer pidfd pins socket creator");
  }
  auto rawSend = [&](const void *data, std::size_t size) {
    iovec io{const_cast<void *>(data), size};
    msghdr msg{};
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    return sendmsg(a.get(), &msg, MSG_NOSIGNAL);
  };
  check(sendText(a.get(), Type::Request, "{\"id\":\"41\"}", 41),
        "send metadata packet");
  Packet packet;
  check(receivePacket(b.get(), packet) && packet.header.type == Type::Request &&
            packet.header.request == 41,
        "receive exact frame");
  check(sendPacket(a.get(), Type::Response, 41),
        "send PAM confirmation acknowledgement");
  check(receivePacket(b.get(), packet) &&
            packet.header.type == Type::Response &&
            packet.header.request == 41 && packet.header.option == 0 &&
            packet.body.size() == 0,
        "PAM confirmation acknowledgement carries correlation without secret");
  Header bad;
  bad.type = Type::Status;
  bad.version = 999;
  check(rawSend(&bad, sizeof(bad)) == sizeof(bad), "send malformed version");
  check(!receivePacket(b.get(), packet), "reject protocol version mismatch");
  bad = Header{};
  bad.type = Type::Status;
  bad.size = 8;
  check(rawSend(&bad, sizeof(bad)) == sizeof(bad), "send malformed packet");
  check(!receivePacket(b.get(), packet), "reject declared body mismatch");
  bad = Header{};
  bad.reserved = 1;
  check(rawSend(&bad, sizeof(bad)) == sizeof(bad), "send malformed packet");
  check(!receivePacket(b.get(), packet), "reject reserved bits");
  unsigned char tooLarge[sizeof(Header) + MaxBody + 1]{};
  bad = Header{};
  bad.size = MaxBody;
  memcpy(tooLarge, &bad, sizeof(bad));
  check(rawSend(tooLarge, sizeof(tooLarge)) == sizeof(tooLarge),
        "send oversized packet");
  check(!receivePacket(b.get(), packet), "reject truncated oversized packet");
  check(!sendPacket(a.get(), Type::Submit, 0, 0, tooLarge, MaxBody + 1),
        "sender rejects oversized payload");
}
void processes() {
  auto self = inspectProcess(getpid());
  if (!self) {
    std::fprintf(
        stderr,
        "SKIP process integration: pidfd/proc inspection unavailable (%s)\n",
        strerror(errno));
    return;
  }
  check(sameProcess(*self), "live identity revalidates");
  auto second = inspectProcess(getpid());
  check(second && samePinnedProcess(*self, *second), "independent live snapshots identify same process");
  if (second) {
    auto rejectChange = [&](auto member) {
      const auto value = (*second).*member;
      ++((*second).*member);
      check(!samePinnedProcess(*self, *second), "changed controller identity rejects");
      (*second).*member = value;
    };
    rejectChange(&Process::pid);
    rejectChange(&Process::ppid);
    rejectChange(&Process::sid);
    rejectChange(&Process::start);
    rejectChange(&Process::dev);
    rejectChange(&Process::ino);
    rejectChange(&Process::uid);
    rejectChange(&Process::euid);
  }
  auto start = self->start;
  self->start++;
  check(!sameProcess(*self), "PID reuse start-time mismatch rejects");
  self->start = start;
  auto parent = self->ppid;
  self->ppid++;
  check(!sameProcess(*self), "reparented owner rejects");
  self->ppid = parent;
  check(self->sid == getsid(0), "kernel process session is captured for timestamp matching");
  auto sid = self->sid;
  self->sid++;
  check(!sameProcess(*self), "changed process session rejects");
  self->sid = sid;
  auto ino = self->ino;
  self->ino++;
  check(!sameProcess(*self), "executable replacement rejects");
  self->ino = ino;
  Identity a, b;
  a.uid = b.uid = getuid();
  a.owner = b.owner = self;
  check(sameOwner(a, b), "same pinned owner matches");
  b.uid++;
  check(!sameOwner(a, b), "matching PID with other uid rejects");
  check(!trustedExecutable(*self, "/does-not-exist"),
        "missing trusted executable rejects");
  Identity forged;
  forged.uid = getuid();
  forged.helper = self;
  forged.sudo = self;
  check(!validateIdentity(forged),
        "self cannot forge helper parent relationship");
  int sync[2];
  if (pipe2(sync, O_CLOEXEC)) {
    check(false, "process sync pipe");
    return;
  }
  pid_t child = fork();
  if (child == 0) {
    close(sync[1]);
    char c;
    auto ignored = read(sync[0], &c, 1);
    (void)ignored;
    _exit(0);
  }
  close(sync[0]);
  if (child < 0) {
    close(sync[1]);
    check(false, "fork process test");
    return;
  }
  auto process = inspectProcess(child);
  check(process && sameProcess(*process), "live child pinned");
  if (process) {
    check(!inspectProcess(child, Fd(dup(self->pidfd.get()))),
          "controller PID with another process pidfd rejects");
    auto supplied = inspectProcess(child, Fd(dup(process->pidfd.get())));
    check(supplied && samePinnedProcess(*process, *supplied),
          "matching externally supplied pidfd pins controller snapshot");
  }
  close(sync[1]);
  waitpid(child, nullptr, 0);
  check(process && !sameProcess(*process), "pidfd proves owner exit");
}
void runningExecutableReplacement() {
  char directory[] = "/tmp/emaki-sudo-running-XXXXXX";
  if (!mkdtemp(directory)) {
    check(false, "create running executable fixture");
    return;
  }
  const auto executable = std::string(directory) + "/controller";
  const auto replacement = std::string(directory) + "/replacement";
  std::error_code error;
  std::filesystem::copy_file("/proc/self/exe", executable, error);
  if (error || chmod(executable.c_str(), 0700)) {
    check(false, "copy running executable fixture");
    unlink(executable.c_str());
    rmdir(directory);
    return;
  }
  const auto child = fork();
  if (!child) {
    execl(executable.c_str(), executable.c_str(), "-p", "fixture",
          static_cast<char *>(nullptr));
    _exit(127);
  }
  int status = 0;
  if (child > 0 && waitpid(child, &status, WUNTRACED) == child && WIFSTOPPED(status)) {
    auto process = inspectProcess(child);
    check(process != nullptr, "inspect running executable fixture");
    if (process) {
      const bool rootOwned = geteuid() == 0;
      check(trustedRunningExecutable(*process, executable.c_str()) == rootOwned,
            "running executable requires root ownership");
      check(!trustedRunningExecutable(*process, replacement.c_str()),
            "running executable requires exact configured path");
      check(!chmod(executable.c_str(), 0770), "make running inode group writable");
      check(!trustedRunningExecutable(*process, executable.c_str()),
            "group-writable running inode rejects");
      check(!chmod(executable.c_str(), 0700), "restore running inode permissions");
      std::filesystem::copy_file("/proc/self/exe", replacement, error);
      check(!error && !rename(replacement.c_str(), executable.c_str()),
            "replace installed executable while controller runs");
      check(sameProcess(*process), "package replacement preserves pinned process");
      check(!trustedExecutable(*process, executable.c_str()),
            "strict installed executable check rejects replaced inode");
      check(trustedRunningExecutable(*process, executable.c_str()) == rootOwned,
            "running executable keeps ownership policy after package replacement");
      auto after = inspectProcess(child);
      check(after && after->executable == executable + " (deleted)" &&
                trustedRunningExecutable(*after, executable.c_str()) == rootOwned,
            "new discovery recognizes unlinked running executable");
      ++process->ino;
      check(!trustedRunningExecutable(*process, executable.c_str()),
            "running executable rejects changed pinned inode");
      --process->ino;
    }
    kill(child, SIGKILL);
    waitpid(child, nullptr, 0);
    if (process)
      check(!trustedRunningExecutable(*process, executable.c_str()),
            "exited controller cannot retain executable trust");
  } else {
    check(false, "running executable fixture stops after exec");
  }
  unlink(replacement.c_str());
  unlink(executable.c_str());
  rmdir(directory);
}
void satelliteImpostor() {
  char directory[] = "/tmp/emaki-sudo-satellite-XXXXXX";
  if (!mkdtemp(directory)) {
    check(false, "create satellite execution fixture");
    return;
  }
  const auto executable = readLinkBounded("/proc/self/exe");
  const auto qs = std::string(directory) + "/qs";
  const auto script = std::string(directory) + "/satellite";
  check(!symlink(executable.c_str(), qs.c_str()), "create harmless qs fixture");
  {
    Fd file(open(script.c_str(), O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0700));
    constexpr char content[] = "#!/bin/sh\nexec \"$1\" -p fixture\n";
    check(file && write(file.get(), content, sizeof(content) - 1) == static_cast<ssize_t>(sizeof(content) - 1),
          "write satellite-path script execing qs fixture");
  }
  auto spawn = [&](bool satellite) {
    auto pid = fork();
    if (!pid) {
      if (satellite)
        execl("/bin/sh", "sh", script.c_str(), qs.c_str(), static_cast<char *>(nullptr));
      else
        execl(qs.c_str(), qs.c_str(), "-p", "fixture", static_cast<char *>(nullptr));
      _exit(127);
    }
    int status = 0;
    check(pid > 0 && waitpid(pid, &status, WUNTRACED) == pid && WIFSTOPPED(status),
          "executed provider fixture stops after exec");
    return pid;
  };
  const auto genuinePid = spawn(false);
  const auto impostorPid = spawn(true);
  auto genuine = inspectProcess(genuinePid);
  auto impostor = inspectProcess(impostorPid);
  check(genuine && impostor && genuine->ppid == getpid() &&
        impostor->ppid == getpid() && genuine->dev == impostor->dev &&
        genuine->ino == impostor->ino && sameProcess(*genuine) && sameProcess(*impostor),
        "stopped shell and satellite exec have same executable and direct parent");
  if (genuine && impostor) {
    const auto reply = "{\"Ok\":{\"TrustedShellIdentity\":{\"pid\":" +
        std::to_string(genuine->pid) + ",\"start_time\":" +
        std::to_string(genuine->start) + "}}}\n";
    check(trustedShellReply(reply, *genuine), "stopped genuine retains trusted connection identity");
    check(!freshHeartbeat(100, 104), "stopped genuine provider slot expires");
    check(!trustedShellReply(reply, *impostor), "satellite script exec cannot take expired provider slot");
    check(!inspectProcess(impostorPid, Fd(dup(genuine->pidfd.get()))),
          "impostor PID cannot reuse genuine provider pidfd");
  }
  for (auto pid : {genuinePid, impostorPid}) {
    if (pid > 0) {
      kill(pid, SIGKILL);
      waitpid(pid, nullptr, 0);
    }
  }
  unlink(script.c_str());
  unlink(qs.c_str());
  rmdir(directory);
}
} // namespace
int main(int argc, char **argv) {
  if (argc == 3 && !strcmp(argv[1], "-p") && !strcmp(argv[2], "fixture")) {
    raise(SIGSTOP);
    return 0;
  }
  boundedReads();
  decisions();
  candidates();
  peers();
  uiPeers();
  heartbeats();
  requesters();
  lockMarkers();
  wipe();
  transport();
  processes();
  runningExecutableReplacement();
  satelliteImpostor();

  if (!failures)
    std::puts("sudo core checks passed");
  return failures ? 1 : 0;
}
