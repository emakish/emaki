// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "protocol.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <sys/types.h>
#include <vector>
namespace emaki::sudo_channel {
class Fd {
  int value_ = -1;

public:
  Fd() = default;
  explicit Fd(int value) : value_(value) {}
  ~Fd();
  Fd(const Fd &) = delete;
  Fd &operator=(const Fd &) = delete;
  Fd(Fd &&) noexcept;
  Fd &operator=(Fd &&) noexcept;
  int get() const { return value_; }
  explicit operator bool() const { return value_ >= 0; }
  int release();
  void reset(int value = -1);
};
class Secret {
  unsigned char *data_ = nullptr;
  std::size_t capacity_ = 0, size_ = 0;

public:
  explicit Secret(std::size_t capacity = MaxBody);
  ~Secret();
  Secret(const Secret &) = delete;
  Secret &operator=(const Secret &) = delete;
  bool valid() const { return data_ != nullptr; }
  unsigned char *data() { return data_; }
  const unsigned char *data() const { return data_; }
  std::size_t size() const { return size_; }
  std::size_t capacity() const { return capacity_; }
  bool assign(const void *, std::size_t);
  void setSize(std::size_t);
  void wipe();
};
struct Packet {
  Header header{};
  Secret body;
};
bool harden();
bool sendPacket(int fd, Type type, std::uint64_t request = 0,
                std::uint32_t option = 0, const void *data = nullptr,
                std::size_t size = 0);
bool sendText(int fd, Type type, const std::string &,
              std::uint64_t request = 0);
// EOF reports ECONNRESET; malformed packets report EPROTO; syscall errors remain.
bool receivePacket(int fd, Packet &);
bool peerCredentials(int fd, pid_t &pid, uid_t &uid);
Fd connectBroker();
std::string escapeJson(const std::string &);
std::uint64_t boottimeSeconds();
bool validDuration(std::uint32_t);
bool authenticationBlocked(int runtimeDirectory, uid_t uid, bool *unavailable = nullptr);
bool freshHeartbeat(std::uint64_t last, std::uint64_t now);
bool validPassword(const Secret &);
struct Process {
  pid_t pid = 0, ppid = 0, sid = 0;
  uid_t uid = static_cast<uid_t>(-1), euid = static_cast<uid_t>(-1);
  long long tty = 0;
  std::uint64_t start = 0, dev = 0, ino = 0;
  std::string executable;
  Fd pidfd;
};
std::shared_ptr<Process> inspectProcess(pid_t);
std::shared_ptr<Process> inspectProcess(pid_t, Fd pidfd);
std::shared_ptr<Process> inspectPeer(int socket, pid_t pid, uid_t uid);
bool sameProcess(const Process &);
bool samePinnedProcess(const Process &, const Process &);
// Strict bounded IPC reply; the caller must authenticate the socket and pidfds.
bool trustedShellReply(const std::string &, const Process &);
bool trustedExecutable(const Process &, const char *);
// A running controller retains its root-owned inode across package replacement.
bool trustedRunningExecutable(const Process &, const char *);
bool safeRootFile(const char *);
std::string readBounded(const std::string &, std::size_t);
std::string readLinkBounded(const std::string &);
// Display hints only: executable/script names do not authorize a requester.
std::string knownRequester(const std::string &executable,
                           const std::vector<std::string> &arguments);
// Chain includes sudo first and owner last. Every link must remain unchanged.
struct Identity {
  uid_t uid = 0;
  std::shared_ptr<Process> helper, sudo;
  std::vector<std::shared_ptr<Process>> chain;
  std::shared_ptr<Process> owner;
};
bool validateIdentity(const Identity &, bool requireHelper = false);
bool sameOwner(const Identity &, const Identity &);
bool privilegedPamPeer(uid_t peerUid, uid_t effectiveUid, long long tty,
                       bool trustedSudo, bool trustedModule);
// Prompt-provider authorization is a process/session boundary. Arguments describe
// the retained configuration path; they do not attest the loaded QML contents.
struct UiPeerDecision {
  bool sameUid = false, parentUid = false, stableUi = false,
       stableParent = false;
  bool trustedUi = false, trustedManager = false, directParent = false;
  bool exactUnit = false, uiArguments = false, managerArguments = false;
  bool trustedCompositor = false, sessionController = false;
};
bool trustedUiPeer(const UiPeerDecision &);
std::string parseSessionController(const std::string &);
bool validUiArguments(const std::vector<std::string> &);
bool validManagerArguments(const std::vector<std::string> &);
// A password submission is provisional until the same PAM conversation reports
// successful authentication. Revocation generations prevent late resurrection.
class GrantCandidate {
  bool submitted_ = false, finished_ = false;
  std::uint32_t duration_ = 0;
  std::uint64_t generation_ = 0;

public:
  // Zero submits a password without requesting a key.
  bool submit(std::uint32_t duration, std::uint64_t generation);
  bool submitted() const { return submitted_; }
  std::uint32_t duration() const { return duration_; }
  bool confirm(bool authenticated, std::uint64_t generation, bool package,
               bool warning, bool ownerAlive);
};
// Pure authorization predicate used by daemon and unit tests.
struct KeyDecision {
  bool marker = false, warning = false, ownerAlive = false, ancestry = false;
  bool sameUid = false, sameOwner = false, retry = false;
  std::uint64_t expires = 0, now = 0;
};
bool mayReuse(const KeyDecision &);
} // namespace emaki::sudo_channel
