// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include "mapping.hpp"
#include "arguments.hpp"
#include "cleanup.hpp"
#include "warning.hpp"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <optional>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <map>
#include <poll.h>
#include <pwd.h>
#include <set>
#include <sstream>
#include <string_view>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/fsuid.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <sys/un.h>
#include <systemd/sd-bus.h>
#include <systemd/sd-login.h>
#include <unistd.h>
#ifndef EMAKI_PAM_MODULE
#error EMAKI_PAM_MODULE must name the installed PAM module
#endif
using namespace emaki::sudo_channel;
namespace {
volatile sig_atomic_t stopping = 0;
void stop(int) { stopping = 1; }
std::string basename(const std::string &p) {
  return p.substr(p.rfind('/') == std::string::npos ? 0 : p.rfind('/') + 1);
}
bool marker() { return safeRootFile(EMAKI_SUDO_KEY_MARKER); }
// New prompts require an active login. Existing keys retain their live login
// through lock, suspend and temporary inactivity, but never logout.
// User-manager processes use
// the unambiguous graphical display session belonging to the same uid.
std::string session(pid_t pid, uid_t uid, bool requireActive = true) {
  char *value = nullptr;
  if (sd_pid_get_session(pid, &value) < 0) {
    uid_t owner = 0;
    if (sd_pid_get_owner_uid(pid, &owner) < 0 || owner != uid ||
        sd_uid_get_display(uid, &value) < 0)
      return {};
    char **sessions = nullptr;
    int count = sd_uid_get_sessions(uid, requireActive ? 1 : 0, &sessions);
    unsigned graphical = 0;
    bool displayActive = false;
    if (count >= 0) {
      for (int n = 0; n < count; ++n) {
        char *type = nullptr;
        if (sd_session_get_type(sessions[n], &type) >= 0) {
          if (!strcmp(type, "wayland") || !strcmp(type, "x11")) {
            ++graphical;
            if (!strcmp(sessions[n], value))
              displayActive = true;
          }
          free(type);
        }
        free(sessions[n]);
      }
      free(sessions);
    }
    if (graphical != 1 || !displayActive) {
      free(value);
      return {};
    }
  }
  std::string result(value);
  free(value);
  uid_t owner = 0;
  if (sd_session_get_uid(result.c_str(), &owner) < 0 || owner != uid ||
      (requireActive && sd_session_is_active(result.c_str()) <= 0))
    return {};
  char *state = nullptr;
  if (sd_session_get_state(result.c_str(), &state) < 0)
    return {};
  bool live = !strcmp(state, "active") || !strcmp(state, "online");
  free(state);
  return live ? result : std::string{};
}
// A uid-wide gate deliberately treats stale supervisor markers as blocked.
// A stale marker can deny a graphical prompt but cannot unlock another display.
std::optional<bool> supervisorBlocked(uid_t uid) {
  Fd root(open("/run/user", O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  struct stat st{};
  if (!root || fstat(root.get(), &st) || st.st_uid || (st.st_mode & 0022))
    return {};
  auto name = std::to_string(uid);
  Fd runtime(
      openat(root.get(), name.c_str(), O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  if (!runtime)
    return {};
  bool unavailable = false;
  const bool blocked = authenticationBlocked(runtime.get(), uid, &unavailable);
  return unavailable ? std::optional<bool>{} : std::optional<bool>{blocked};
}

bool moduleMapped(const Process &process) {
  const auto maps = readBounded("/proc/" + std::to_string(process.pid) + "/maps",
                                MaxProcessMaps);
  std::istringstream input(maps);
  std::string line;
  while (std::getline(input, line)) {
    std::istringstream fields(line);
    std::string range, perms, offset, device, inode, path, suffix;
    if (!(fields >> range >> perms >> offset >> device >> inode >> path) ||
        fields >> suffix || path != EMAKI_PAM_MODULE || perms != "r-xp")
      continue;
    if (secureMappedFile(process.pid, range, path))
      return true;
  }
  return false;
}
bool pluginMappings(pid_t pid, const std::string &maps) {
  constexpr std::string_view filename = "libemakisudoplugin.so";
  bool found = false;
  std::istringstream input(maps);
  std::string line;
  while (std::getline(input, line)) {
    if (line.find(filename) == std::string::npos)
      continue;
    std::istringstream fields(line);
    std::string range, perms, offset, device, inode, path, suffix;
    if (!(fields >> range >> perms >> offset >> device >> inode >> path) ||
        fields >> suffix || basename(path) != filename ||
        !(path.starts_with(std::string(EMAKI_SHELL_PATH) + "/") ||
          path.starts_with("/var/lib/emaki/shell-generations/")) ||
        !secureMappedFile(pid, range, path))
      return false;
    found = true;
  }
  return found;
}
bool pluginMapped(const Process &process) {
  const auto maps = readBounded("/proc/" + std::to_string(process.pid) + "/maps", MaxProcessMaps);
  return !maps.empty() && pluginMappings(process.pid, maps);
}
bool installedCommand(const Process &process, const std::string &name) {
  const auto path = std::string(EMAKI_COMMAND_BINDIR) + "/" + name;
  char *resolved = realpath(path.c_str(), nullptr);
  if (!resolved)
    return false;
  const bool trusted = trustedExecutable(process, resolved);
  free(resolved);
  return trusted;
}
bool wrapper(const Process &process) {
  static const std::set<std::string> values = {
      "sh", "bash", "dash", "zsh", "fish", "ksh", "env", "setsid", "timeout"};
  return std::any_of(values.begin(), values.end(), [&](const auto &name) {
    return installedCommand(process, name);
  });
}
std::string pinnedRequesterLabel(const Process &process) {
  for (const auto *name : {"claude", "claude-code", "codex"})
    if (installedCommand(process, name))
      return knownRequester(name, {});
  return {};
}
bool keyOwnerEligible(const Identity &identity) {
  return identity.owner && sameProcess(*identity.owner) &&
         !pinnedRequesterLabel(*identity.owner).empty();
}
bool boundary(const std::string &name) {
  static const std::set<std::string> values = {"systemd",
                                               "init",
                                               "login",
                                               "sshd",
                                               "agetty",
                                               "getty",
                                               "foot",
                                               "footclient",
                                               "kitty",
                                               "alacritty",
                                               "wezterm",
                                               "wezterm-gui",
                                               "gnome-terminal-server",
                                               "konsole",
                                               "tmux",
                                               "screen",
                                               "niri",
                                               "qs",
                                               "quickshell",
                                               "emaki-sudo-broker"};
  return values.contains(name);
}
std::unique_ptr<Identity> identify(const std::shared_ptr<Process> &sudo,
                                   uid_t uid) {
  if (!sudo || !privilegedPamPeer(0, sudo->euid, sudo->tty,
                                  trustedExecutable(*sudo, EMAKI_SUDO_PATH),
                                  moduleMapped(*sudo)))
    return {};
  auto identity = std::make_unique<Identity>();
  identity->uid = uid;
  identity->sudo = sudo;
  identity->chain.push_back(sudo);
  pid_t next = sudo->ppid;
  for (unsigned depth = 0; depth < 24 && next > 1; ++depth) {
    auto p = inspectProcess(next);
    if (!p || p->uid != uid || p->euid != uid)
      break;
    auto name = basename(p->executable);
    if (boundary(name))
      break;
    identity->chain.push_back(p);
    if (!identity->owner && !wrapper(*p))
      identity->owner = p;
    next = p->ppid;
  }
  // Never associate an arbitrary root sudo with a claimed PAM account. The
  // kernel-observed direct parent must be the account being authenticated.
  if (identity->chain.size() < 2 || !validateIdentity(*identity))
    return {};
  return identity;
}
std::vector<std::string> processArguments(pid_t pid) {
  auto cmd = readBounded("/proc/" + std::to_string(pid) + "/cmdline", 8192);
  std::vector<std::string> args;
  for (std::size_t pos = 0; pos < cmd.size();) {
    auto end = cmd.find('\0', pos);
    if (end == std::string::npos)
      return {};
    args.push_back(cmd.substr(pos, end - pos));
    pos = end + 1;
  }
  return args;
}
std::string requesterLabel(const Identity &identity) {
  // For a key-capable request, name the inode-pinned owner even when its
  // canonical executable has a versioned or otherwise unfamiliar basename.
  if (keyOwnerEligible(identity))
    return pinnedRequesterLabel(*identity.owner);
  for (const auto &process : identity.chain) {
    if (process == identity.sudo)
      continue;
    auto label = knownRequester(process->executable,
                                processArguments(process->pid));
    if (!label.empty())
      return label;
  }
  return {};
}
struct Controller {
  bool resolved = false;
  std::string name;
  std::shared_ptr<Process> process;
};
enum class IdentityResult { Trusted, Refused, LockUnknown };

#ifndef EMAKI_SUDO_LIFECYCLE_TEST
std::optional<std::string> controllerState(const std::string &login) {
  if (login.empty() || login.size() > 255 ||
      login.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                              "0123456789_-") != std::string::npos)
    return {};
  Fd directory(open("/run/systemd/sessions",
                    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  struct stat st{};
  if (!directory || fstat(directory.get(), &st) || st.st_uid ||
      (st.st_mode & 0022))
    return {};
  Fd file(openat(directory.get(), login.c_str(),
                 O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC));
  if (!file || fstat(file.get(), &st) || !S_ISREG(st.st_mode) || st.st_uid ||
      (st.st_mode & 0022) || st.st_size > 16384)
    return {};
  std::string state;
  char buffer[1024];
  while (state.size() <= 16384) {
    auto size = read(file.get(), buffer, sizeof(buffer));
    if (size < 0 && errno == EINTR)
      continue;
    if (size < 0)
      return {};
    if (!size) {
      const auto name = parseSessionController(state);
      if (!name.empty())
        return name;
      // An authenticated state record without a controller permits legacy
      // sessions. Missing, malformed or ambiguous records are not absence.
      if (!state.empty() && state.back() == '\n' &&
          state.find('\0') == std::string::npos) {
        std::istringstream lines(state);
        std::string line;
        bool absent = true;
        while (std::getline(lines, line))
          if (line.starts_with("CONTROLLER="))
            absent = false;
        if (absent)
          return std::string{};
      }
      return {};
    }
    state.append(buffer, static_cast<std::size_t>(size));
  }
  return {};
}
bool resolveSessionController(uid_t uid, const std::string &login,
                              Controller &result) {
  // Only the root-owned logind record and bus-supplied original credentials
  // identify the controller. Never infer it from the candidate's parent.
  const auto name = controllerState(login);
  if (!name)
    return false;
  if (name->empty()) {
    result = {true, {}, {}};
    return true;
  }
  sd_bus *rawBus = nullptr;
  if (sd_bus_open_system(&rawBus) < 0)
    return false;
  std::unique_ptr<sd_bus, decltype(&sd_bus_unref)> bus(rawBus, sd_bus_unref);
  if (sd_bus_set_method_call_timeout(bus.get(), 250000) < 0)
    return false;
  constexpr auto mask = SD_BUS_CREDS_PID | SD_BUS_CREDS_EUID | SD_BUS_CREDS_PIDFD;
  sd_bus_creds *rawCredentials = nullptr;
  if (sd_bus_get_name_creds(bus.get(), name->c_str(), mask, &rawCredentials) < 0)
    return false;
  std::unique_ptr<sd_bus_creds, decltype(&sd_bus_creds_unref)> credentials(
      rawCredentials, sd_bus_creds_unref);
  pid_t pid = 0;
  uid_t owner = static_cast<uid_t>(-1);
  int descriptor = -1;
  if ((sd_bus_creds_get_augmented_mask(credentials.get()) & mask) ||
      sd_bus_creds_get_pid(credentials.get(), &pid) < 0 || pid <= 1 ||
      sd_bus_creds_get_euid(credentials.get(), &owner) < 0 || owner != uid ||
      sd_bus_creds_get_pidfd_dup(credentials.get(), &descriptor) < 0)
    return false;
  auto process = inspectProcess(pid, Fd(descriptor));
  if (!process || process->uid != uid || process->euid != uid ||
      !sameProcess(*process) || controllerState(login) != name)
    return false;
  result = {true, *name, std::move(process)};
  return true;
}

// This broker is single-threaded. Never retain root's filesystem access while
// resolving a user's discovery paths, including a raced replacement at connect.
class SessionFilesystem {
  uid_t savedUid;
  gid_t savedGid;
  std::vector<gid_t> savedGroups;
  bool groupsChanged = false, valid = false;

public:
  explicit SessionFilesystem(uid_t uid)
      : savedUid(setfsuid(static_cast<uid_t>(-1))),
        savedGid(setfsgid(static_cast<gid_t>(-1))) {
    if (!uid)
      return;
    const auto *account = getpwuid(uid);
    if (!account)
      return;
    const auto gid = account->pw_gid;
    // The service's supplementary groups must not grant root-group access.
    if (!geteuid()) {
      const int count = getgroups(0, nullptr);
      if (count < 0)
        return;
      savedGroups.resize(count);
      if (getgroups(count, savedGroups.data()) != count || setgroups(0, nullptr))
        return;
      groupsChanged = true;
    } else if (uid != geteuid()) {
      return;
    }
    setfsgid(gid);
    setfsuid(uid);
    if (static_cast<gid_t>(setfsgid(static_cast<gid_t>(-1))) != gid ||
        static_cast<uid_t>(setfsuid(static_cast<uid_t>(-1))) != uid)
      return;
    // capabilities(7): fsuid 0 -> nonzero clears effective DAC capabilities.
    // Verify that contract even if securebits were unexpectedly changed.
    __user_cap_header_struct header{_LINUX_CAPABILITY_VERSION_3, 0};
    __user_cap_data_struct capabilities[2]{};
    valid = syscall(SYS_capget, &header, capabilities) == 0 &&
        !(capabilities[0].effective &
          ((1U << CAP_DAC_OVERRIDE) | (1U << CAP_DAC_READ_SEARCH)));
  }
  SessionFilesystem(const SessionFilesystem &) = delete;
  SessionFilesystem &operator=(const SessionFilesystem &) = delete;
  ~SessionFilesystem() {
    setfsuid(savedUid);
    setfsgid(savedGid);
    if (static_cast<uid_t>(setfsuid(static_cast<uid_t>(-1))) != savedUid ||
        static_cast<gid_t>(setfsgid(static_cast<gid_t>(-1))) != savedGid ||
        (groupsChanged && setgroups(savedGroups.size(), savedGroups.data())))
      _exit(1);
  }
  explicit operator bool() const { return valid; }
};

IdentityResult trustedShellIdentityAt(const Process &provider, const Process &controller,
                            uid_t uid, const std::string &runtime) {
  using Clock = std::chrono::steady_clock;
  const auto deadline = Clock::now() + std::chrono::milliseconds(100);
  // The path is discovery only. Every connected socket must present the
  // controller's original kernel pidfd before its response is considered.
  SessionFilesystem credentials(uid);
  if (!credentials)
    return IdentityResult::Refused;
  Fd runtimeFd(open(runtime.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  struct stat metadata{};
  if (!runtimeFd || fstat(runtimeFd.get(), &metadata) ||
      metadata.st_uid != uid || (metadata.st_mode & 07777) != 0700)
    return IdentityResult::Refused;
  auto closeDirectory = [](DIR *value) { closedir(value); };
  std::unique_ptr<DIR, decltype(closeDirectory)> directory(fdopendir(runtimeFd.get()),
                                                        closeDirectory);
  if (!directory)
    return IdentityResult::Refused;
  runtimeFd.release();
  bool timedOut = false;
  auto ready = [&](int fd, short events) {
    const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline - Clock::now()).count();
    if (remaining <= 0) {
      timedOut = true;
      return false;
    }
    pollfd item{fd, events, 0};
    const int result = poll(&item, 1, static_cast<int>(remaining));
    if (!result || Clock::now() >= deadline)
      timedOut = true;
    return !timedOut && result == 1 && (item.revents & events);
  };
  unsigned entries = 0;
  std::vector<std::string> candidates;
  errno = 0;
  while (auto *entry = readdir(directory.get())) {
    if (++entries > 1024)
      return IdentityResult::Refused;
    if (Clock::now() >= deadline)
      return IdentityResult::Refused;
    std::string name(entry->d_name);
    if (!name.starts_with("niri.") || !name.ends_with(".sock"))
      continue;
    candidates.push_back(std::move(name));
  }
  if (errno || Clock::now() >= deadline)
    return IdentityResult::Refused;
  // Refuse an oversized directory regardless of where the real socket occurs.
  for (const auto &name : candidates) {
    if (Clock::now() >= deadline)
      return IdentityResult::Refused;
    if (fstatat(dirfd(directory.get()), name.c_str(), &metadata,
                AT_SYMLINK_NOFOLLOW) || !S_ISSOCK(metadata.st_mode) ||
        metadata.st_uid != uid)
      continue;
    const auto path = runtime + "/" + name;
    sockaddr_un address{};
    if (path.size() >= sizeof(address.sun_path))
      continue;
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, path.c_str(), path.size() + 1);
    Fd socketFd(socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
    if (!socketFd)
      return IdentityResult::Refused;
    if (connect(socketFd.get(), reinterpret_cast<sockaddr *>(&address),
                sizeof(address)) < 0) {
      if (errno != EINPROGRESS || !ready(socketFd.get(), POLLOUT))
        continue;
      int error = 0;
      socklen_t size = sizeof(error);
      if (getsockopt(socketFd.get(), SOL_SOCKET, SO_ERROR, &error, &size) || error)
        continue;
    }
    pid_t pid = 0;
    uid_t owner = static_cast<uid_t>(-1);
    if (!peerCredentials(socketFd.get(), pid, owner) || owner != uid)
      continue;
    auto peer = inspectPeer(socketFd.get(), pid, owner);
    if (!peer || !samePinnedProcess(controller, *peer))
      continue;
    constexpr std::string_view request = "\"TrustedShellIdentity\"\n";
    std::size_t sent = 0;
    while (sent < request.size()) {
      if (Clock::now() >= deadline)
        return IdentityResult::Refused;
      auto count = send(socketFd.get(), request.data() + sent,
                        request.size() - sent, MSG_NOSIGNAL);
      if (count > 0)
        sent += static_cast<std::size_t>(count);
      else if (count < 0 && errno == EINTR)
        continue;
      else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) &&
               ready(socketFd.get(), POLLOUT))
        continue;
      else
        return IdentityResult::Refused;
    }
    std::string response;
    while (response.size() < 512) {
      if (Clock::now() >= deadline)
        return IdentityResult::Refused;
      char buffer[128];
      auto count = recv(socketFd.get(), buffer, sizeof(buffer), 0);
      if (Clock::now() >= deadline)
        return IdentityResult::Refused;
      if (count > 0) {
        response.append(buffer, static_cast<std::size_t>(count));
        if (response.find('\n') != std::string::npos)
          return trustedShellReply(response, provider) &&
                 samePinnedProcess(controller, *peer) && sameProcess(provider)
                     ? IdentityResult::Trusted : IdentityResult::Refused;
      } else if (count < 0 && errno == EINTR) {
        continue;
      } else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) &&
                 ready(socketFd.get(), POLLIN)) {
        continue;
      } else {
        return IdentityResult::Refused;
      }
    }
    return IdentityResult::Refused;
  }
  return IdentityResult::Refused;
}
IdentityResult trustedShellIdentity(const Process &provider, const Process &controller,
                          uid_t uid) {
  return trustedShellIdentityAt(provider, controller, uid,
                                "/run/user/" + std::to_string(uid));
}
#endif
// Process provenance alone grants no authority; discovery must also succeed.
bool validUiProcess(const Process &p, uid_t uid,
                const std::shared_ptr<Process> &parent,
                const std::string &login, Controller &controller,
                bool *discoveryRequired = nullptr) {
  if (discoveryRequired)
    *discoveryRequired = true;
  // Reject untrusted executables and ancestry before asking the system bus.
  if (!parent || login.empty() || p.uid != uid || p.euid != uid ||
      parent->uid != uid || parent->euid != uid || p.ppid != parent->pid ||
      !sameProcess(p) || !sameProcess(*parent) ||
      !trustedExecutable(p, EMAKI_QUICKSHELL_PATH) ||
      !validUiArguments(processArguments(p.pid)) || !pluginMapped(p) ||
      !trustedRunningExecutable(*parent, EMAKI_NIRI_PATH))
    return false;
  if ((!controller.resolved &&
       !resolveSessionController(uid, login, controller)) ||
      !controller.process ||
      controllerState(login) != std::optional<std::string>(controller.name) ||
      !trustedRunningExecutable(*controller.process, EMAKI_NIRI_PATH) ||
      !samePinnedProcess(*parent, *controller.process) ||
      session(parent->pid, uid) != login || session(p.pid, uid) != login)
    return false;
  char *type = nullptr;
  const bool wayland = sd_session_get_type(login.c_str(), &type) >= 0 && type &&
                       !strcmp(type, "wayland");
  free(type);
  return wayland && sameProcess(p) && sameProcess(*parent) &&
         controllerState(login) == std::optional<std::string>(controller.name) &&
         sameProcess(*controller.process);
}
struct User {
  int ui = -1;
  bool locked = true, warning = false;
  std::string session;
  std::uint64_t generation = 0;
};
struct IdentityCheck {
  using Clock = std::chrono::steady_clock;
  Fd result;
  pid_t pid = -1;
  Clock::time_point deadline = Clock::now() + std::chrono::milliseconds(500);
  std::unique_ptr<Packet> packet;
  uid_t uid = 0;
  int providerFd = -1;
  bool lockClear = false;
  std::shared_ptr<Process> providerProcess, providerParent;
  std::shared_ptr<const int> connection;
  std::string login, controllerName;
  ~IdentityCheck() {
    if (pid > 0) {
      pid_t status;
      do {
        status = waitpid(pid, nullptr, WNOHANG);
      } while (status < 0 && errno == EINTR);
      if (!status) {
        kill(pid, SIGKILL);
        waitpid(pid, nullptr, WNOHANG);
      }
    }
  }
};
struct IdentityReply {
  std::int64_t completed = 0;
  std::int64_t answer = 'R';
};
std::int64_t identityTime(IdentityCheck::Clock::time_point time) {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(time.time_since_epoch()).count();
}
struct TurnReporter;
struct Client {
  Fd fd;
  uid_t uid;
  std::uint64_t since;
  bool ui = false, pending = false;
  std::shared_ptr<Process> process, parent;
  Controller controller;
  std::unique_ptr<IdentityCheck> check;
  std::shared_ptr<const int> connection = std::make_shared<const int>(0);
  std::shared_ptr<TurnReporter> eligibleReporter{};
};
struct TurnReporter {
  std::shared_ptr<Process> owner;
  std::string login, token;
  bool completeOnly = false, active = false;
  std::uint64_t completed = 0, seen = 0;
  std::set<std::string> finished;
};
struct Pending {
  std::uint64_t id, created, generation;
  int fd;
  std::unique_ptr<Identity> identity;
  std::string session;
  GrantCandidate candidate;
  std::shared_ptr<TurnReporter> reporter;
  std::uint64_t completed = 0;
  bool confirming = false;
  std::shared_ptr<TurnReporter> eligibleReporter{};
};
struct Key {
  std::uint64_t id, expires;
  uid_t uid;
  std::string session;
  std::shared_ptr<Process> owner;
  std::string requester;
  std::shared_ptr<TurnReporter> reporter;
  std::uint32_t remainingTurns = 0;
};
class Broker {
#ifdef EMAKI_SUDO_LIFECYCLE_TEST
  friend struct BrokerFixture;
#endif
  Fd directory, listener;
  std::map<uid_t, User> users;
  std::map<int, Client> clients;
  std::map<std::uint64_t, Pending> pending;
  std::map<std::uint64_t, Key> keys;
  TimestampCleanup timestamps;
  WarningAcceptance warnings;
  std::map<pid_t, std::shared_ptr<TurnReporter>> reporters;
  std::uint64_t next = 1;
  bool hadMarker = false, rootBtrfs = false;
  int handledCursor = -1;
  // Keep timed-out children accounted for until the kernel releases them.
  // A stuck filesystem can exhaust only its own login's helper slots.
  std::map<pid_t, std::string> identityChildren;

public:
#ifdef EMAKI_SUDO_LIFECYCLE_TEST
  Broker() = default;
#else
  Broker() {
    directory.reset(
        open(SocketDirectory, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    struct stat st{};
    if (!directory || fstat(directory.get(), &st) || st.st_uid != 0 ||
        (st.st_mode & 0777) != 0755) {
      directory.reset();
      return;
    }
    if (!timestamps.initialize(SocketDirectory, "/run/sudo/ts")) {
      if (timestamps.busy())
        std::fprintf(stderr, "sudo timestamp cleanup: another process owns the journal; startup deferred\n");
      return;
    }
    if (!fstatat(directory.get(), "broker.sock", &st, AT_SYMLINK_NOFOLLOW)) {
      if (!S_ISSOCK(st.st_mode) || st.st_uid ||
          unlinkat(directory.get(), "broker.sock", 0))
        return;
    } else if (errno != ENOENT)
      return;
    listener.reset(
        socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0));
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, SocketPath, strlen(SocketPath) + 1);
    if (!listener ||
        bind(listener.get(), reinterpret_cast<sockaddr *>(&address),
             sizeof(address)) ||
        fchmodat(directory.get(), "broker.sock", 0666, 0) ||
        listen(listener.get(), 32))
      listener.reset();
    struct statfs fs{};
    rootBtrfs = !statfs("/", &fs) &&
                static_cast<unsigned long>(fs.f_type) == 0x9123683EUL;
  }

#endif
  ~Broker() { endKeys(); }
  auto endKey(std::map<std::uint64_t, Key>::iterator key) {
    timestamps.end(key->first);
    return keys.erase(key);
  }
  void endKeys() {
    for (auto key = keys.begin(); key != keys.end();)
      key = endKey(key);
    timestamps.endAll();
  }
  bool rememberParent(std::uint64_t key, const Identity &identity) {
    if (identity.chain.size() < 2 || !validateIdentity(identity))
      return false;
    const auto &parent = *identity.chain[1];
    // sudo keys non-terminal records on its immediate parent, its own process
    // session ID, and the parent's kernel start time; logind IDs are unrelated.
    return timestamps.remember(key, identity.uid,
        TimestampParent{parent.pid, identity.sudo->sid, parent.start});
  }
  bool providerAlive(uid_t uid) {
    auto u = users.find(uid);
    if (u == users.end() || u->second.ui < 0)
      return false;
    auto c = clients.find(u->second.ui);
    return c != clients.end() &&
           freshHeartbeat(c->second.since, boottimeSeconds());
  }
  bool uiAvailable(uid_t uid) {
    return providerAlive(uid) && !users.at(uid).locked;
  }
  bool liveUiProcess(uid_t uid) {
    if (!uiAvailable(uid))
      return false;
    auto &u = users.at(uid);
    auto provider = clients.find(u.ui);
    if (provider == clients.end() ||
        session(provider->second.process->pid, uid) != u.session)
      return false;
    return validUiProcess(*provider->second.process, uid, provider->second.parent,
                      u.session, provider->second.controller);
  }
  void collectChildren() {
    std::set<pid_t> active;
    for (const auto &[fd, c] : clients)
      if (c.check)
        active.insert(c.check->pid);
    for (auto it = identityChildren.begin(); it != identityChildren.end();) {
      const auto status = active.contains(it->first) ? 0 : waitpid(it->first, nullptr, WNOHANG);
      if (status > 0 || (status < 0 && errno == ECHILD))
        it = identityChildren.erase(it);
      else
        ++it;
    }
  }
  void checkIdentity(int fd, Client &provider, const std::string &login, Packet &packet) {
    bool discoveryRequired = true;
    if (!validUiProcess(*provider.process, provider.uid, provider.parent, login,
                        provider.controller, &discoveryRequired)) {
      processPacket(fd, packet, IdentityResult::LockUnknown);
      return;
    }
    collectChildren();
    const auto count = std::count_if(identityChildren.begin(), identityChildren.end(),
        [&](const auto &child) { return child.second == login; });
    if (count >= 4) {
      processPacket(fd, packet, IdentityResult::LockUnknown);
      return;
    }
    auto check = std::make_unique<IdentityCheck>();
    check->uid = provider.uid;
    check->login = login;
    check->providerFd = provider.fd.get();
    check->connection = provider.connection;
    check->providerProcess = provider.process;
    check->providerParent = provider.parent;
    check->controllerName = provider.controller.name;
    check->packet = std::make_unique<Packet>();
    check->packet->header = packet.header;
    int pipeFds[2];
    if (!check->packet->body.assign(packet.body.data(), packet.body.size()) ||
        pipe2(pipeFds, O_NONBLOCK | O_CLOEXEC)) {
      processPacket(fd, packet, IdentityResult::LockUnknown);
      return;
    }
    check->result.reset(pipeFds[0]);
    Fd output(pipeFds[1]);
    const auto parentPid = getpid();
    check->pid = fork();
    if (!check->pid) {
      // No client or listening socket survives into discovery. Retain only
      // the authenticated process handles and the fixed-size result channel.
      signal(SIGTERM, SIG_DFL);
      signal(SIGINT, SIG_DFL);
      if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parentPid)
        _exit(1);
      rlimit cpu{1, 1};
      if (setrlimit(RLIMIT_CPU, &cpu))
        _exit(1);
      alarm(1);
      std::set<int> keep{output.get(), provider.process->pidfd.get(),
                         provider.parent->pidfd.get()};
      if (provider.controller.process)
        keep.insert(provider.controller.process->pidfd.get());
      unsigned first = 0;
      for (int keepFd : keep) {
        if (keepFd < 0)
          continue;
        if (first < static_cast<unsigned>(keepFd) &&
            syscall(SYS_close_range, first, static_cast<unsigned>(keepFd) - 1, 0))
          _exit(1);
        first = static_cast<unsigned>(keepFd) + 1;
      }
      if (syscall(SYS_close_range, first, ~0U, 0))
        _exit(1);
      char answer = 'R';
#ifndef EMAKI_SUDO_LIFECYCLE_TEST
      SessionFilesystem credentials(provider.uid);
      if (!credentials)
        _exit(1);
#endif
      // Filesystem credential transitions clear the parent-death setting.
      if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != parentPid)
        _exit(1);
      const auto blocked = supervisorBlocked(provider.uid);
      if (!blocked || *blocked)
        answer = 'L';
      else {
        // Publish the completed lock scan before potentially slow socket work.
        // Silence or failure before this point must never confirm a password.
        const IdentityReply clear{identityTime(IdentityCheck::Clock::now()), 'C'};
        if (write(output.get(), &clear, sizeof(clear)) != sizeof(clear))
          _exit(1);
        if (!discoveryRequired ||
               trustedShellIdentity(*provider.process, *provider.controller.process,
                                    provider.uid) == IdentityResult::Trusted)
          answer = 'T';
      }
      const IdentityReply reply{identityTime(IdentityCheck::Clock::now()), answer};
      const auto sent = write(output.get(), &reply, sizeof(reply));
      _exit(sent == sizeof(reply) ? 0 : 1);
    }
    if (check->pid < 0) {
      processPacket(fd, packet, IdentityResult::LockUnknown);
      return;
    }
    identityChildren.emplace(check->pid, login);
    clients.at(fd).check = std::move(check);
    packet.body.wipe();
  }
  void completeChecks() {
    std::vector<int> ready;
    for (const auto &[fd, c] : clients) {
      if (!c.check)
        continue;
      pollfd item{c.check->result.get(), POLLIN, 0};
      if (poll(&item, 1, 0) > 0 || IdentityCheck::Clock::now() >= c.check->deadline)
        ready.push_back(fd);
    }
    for (int fd : ready) {
      auto found = clients.find(fd);
      if (found == clients.end() || !found->second.check)
        continue;
      auto &check = found->second.check;
      IdentityReply reply;
      const auto provider = clients.find(check->providerFd);
      const bool sameSource = provider != clients.end() &&
          provider->second.connection == check->connection &&
          provider->second.process == check->providerProcess &&
          provider->second.parent == check->providerParent &&
          provider->second.controller.name == check->controllerName;
      // A completed check keeps its own answer if unrelated parent work
      // delayed collection. Only the child's completion time spends its deadline.
      auto size = read(check->result.get(), &reply, sizeof(reply));
      if (sameSource && size == sizeof(reply) && reply.answer == 'C' &&
          reply.completed < identityTime(check->deadline)) {
        check->lockClear = true;
        size = read(check->result.get(), &reply, sizeof(reply));
      }
      if (sameSource && size < 0 && errno == EAGAIN &&
          IdentityCheck::Clock::now() < check->deadline)
        continue;
      const bool received = sameSource && size == sizeof(reply) &&
          reply.completed < identityTime(check->deadline);
      if (received && reply.answer == 'L' && users.contains(check->uid) &&
          users.at(check->uid).session == check->login) {
        auto &user = users.at(check->uid);
        user.locked = true;
        ++user.generation;
        cancelAll(check->uid);
      }
      if (!clients.contains(fd))
        continue;
      const auto result = received && reply.answer == 'T' && check->lockClear
          ? IdentityResult::Trusted
          : sameSource && check->lockClear ? IdentityResult::Refused
                                          : IdentityResult::LockUnknown;
      auto packet = std::move(check->packet);
      check.reset();
      processPacket(fd, *packet, result);
    }
    collectChildren();
  }
  // A name locates a possible reporting scope, never an authorization source.
  // Every traversed link and the socket peer are pinned and revalidated.
  std::shared_ptr<Process> reportOwner(const Client &c) {
    std::vector<std::shared_ptr<Process>> chain;
    auto p = c.process;
    for (unsigned depth = 0; depth < 24 && p; ++depth) {
      if (p->uid != c.uid || p->euid != c.uid || !sameProcess(*p) ||
          boundary(basename(p->executable)))
        return {};
      chain.push_back(p);
      if (!pinnedRequesterLabel(*p).empty()) {
        for (std::size_t n = 0; n < chain.size(); ++n)
          if (!sameProcess(*chain[n]) ||
              (n && chain[n - 1]->ppid != chain[n]->pid))
            return {};
        return p;
      }
      p = inspectProcess(p->ppid);
    }
    return {};
  }
  std::shared_ptr<TurnReporter> turnReporter(const Identity &i,
                                            const std::string &login) {
    if (!validateIdentity(i))
      return {};
    for (const auto &p : i.chain) {
      auto found = reporters.find(p->pid);
      if (found == reporters.end())
        continue;
      const auto &r = found->second;
      if (r->owner->uid == i.uid && r->login == login &&
          r->owner->start == p->start && r->owner->dev == p->dev &&
          r->owner->ino == p->ino && sameProcess(*r->owner) &&
          !r->completeOnly && r->completed > 0 && r->active &&
          i.owner && samePinnedProcess(*i.owner, *r->owner))
        return r;
    }
    return {};
  }
  void reportTurn(Client &c, Packet &packet) {
    int fd = c.fd.get();
    if (!marker()) {
      error(fd, "key-package-unavailable");
      drop(fd);
      return;
    }
    auto owner = reportOwner(c);
    auto login = owner ? session(owner->pid, c.uid, false) : std::string{};
    std::string token(reinterpret_cast<const char *>(packet.body.data()),
                      packet.body.size());
    auto colon = token.find(':');
    bool complete = packet.header.type == Type::TurnComplete;
    if (!owner || login.empty() || token.empty() || token.size() > 129 ||
        token.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.") !=
            std::string::npos ||
        (complete ? (colon == std::string::npos || colon == 0 ||
                     colon + 1 == token.size() || token.find(':', colon + 1) != std::string::npos)
                  : colon != std::string::npos)) {
      error(fd, "invalid-turn-report");
      drop(fd);
      return;
    }
    const auto sessionToken = complete ? token.substr(0, colon) : token;
    auto found = reporters.find(owner->pid);
    std::shared_ptr<TurnReporter> r = found == reporters.end() ? nullptr : found->second;
    if (r && (!sameProcess(*r->owner) || r->owner->start != owner->start ||
              r->owner->dev != owner->dev || r->owner->ino != owner->ino ||
              r->login != login || r->token != sessionToken ||
              r->completeOnly != complete)) {
      for (auto it = keys.begin(); it != keys.end();)
        if (it->second.reporter == r)
          it = endKey(it);
        else
          ++it;
      reporters.erase(owner->pid);
      r.reset();
    }
    bool ended = false;
    auto reporterCount = std::count_if(reporters.begin(), reporters.end(),
        [&](const auto &entry) { return entry.second->owner->uid == c.uid; });
    if (!r && packet.header.type != Type::TurnEnd && reporterCount < 256) {
      r = std::make_shared<TurnReporter>();
      r->owner = owner;
      r->login = login;
      r->token = sessionToken;
      r->completeOnly = complete;
      reporters.emplace(owner->pid, r);
    }
    if (r) {
      r->seen = boottimeSeconds();
      if (packet.header.type == Type::TurnStart)
        r->active = true;
      else if (complete) {
        // This bounded set evicts the smallest hash, not the oldest report.
        // Replaying an evicted hash can only expire a key earlier, never
        // replenish the count or move the cap.
        ended = r->finished.insert(token.substr(colon + 1)).second;
        if (r->finished.size() > 256)
          r->finished.erase(r->finished.begin());
      } else if (r->active) {
        ended = true;
        r->active = false;
      }
    }
    if (ended) {
      ++r->completed;
      for (auto it = keys.begin(); it != keys.end();) {
        if (it->second.reporter == r && --it->second.remainingTurns == 0)
          it = endKey(it);
        else
          ++it;
      }
    }
    broadcast(c.uid);
    sendText(fd, Type::Response, r ? "{\"ok\":true}" : "{\"ok\":false}");
    drop(fd);
  }
  std::string status(uid_t uid) {
    std::string out = "{\"keys\":[";
    bool first = true;
    auto now = boottimeSeconds();
    for (auto &[id, key] : keys) {
      if (key.uid != uid)
        continue;
      if (!first)
        out += ',';
      first = false;
      out += "{\"id\":" + escapeJson(std::to_string(id)) +
             ",\"ownerPid\":" + std::to_string(key.owner->pid) +
             ",\"requester\":" + escapeJson(key.requester) +
             ",\"remainingSeconds\":" +
             (key.expires
                  ? std::to_string(key.expires > now ? key.expires - now : 0)
                  : "-1") +
             ",\"session\":" + (key.expires ? "false" : "true") +
             ",\"kind\":" + escapeJson(key.reporter ? "turns" : key.expires ? "time" : "session") +
             ",\"remainingTurns\":" + std::to_string(key.remainingTurns) + "}";
    }
    auto u = users.find(uid);
    bool available = uiAvailable(uid);
    return out + "],\"keyAvailable\":" + (marker() ? "true" : "false") +
           ",\"warningAccepted\":" +
           (u != users.end() && u->second.warning ? "true" : "false") +
           ",\"timestampCleanupPending\":" + std::to_string(timestamps.pending(uid)) +
           ",\"timestampCleanupAvailable\":" + (timestamps.healthy() ? "true" : "false") +
           ",\"btrfs\":" + (rootBtrfs ? "true" : "false") +
           ",\"snapguardAvailable\":false,\"promptAvailable\":" +
           (available ? "true" : "false") + "}";
  }
  void broadcast(uid_t uid) {
    auto u = users.find(uid);
    if (u != users.end() && u->second.ui >= 0)
      sendText(u->second.ui, Type::Keys, status(uid));
  }
  void eraseKeys(uid_t uid) {
    for (auto it = keys.begin(); it != keys.end();)
      if (it->second.uid == uid)
        it = endKey(it);
      else
        ++it;
    if (users.contains(uid))
      ++users.at(uid).generation;
    broadcast(uid);
  }
  void finish(std::uint64_t id, Type reply = Type::Denied,
              unsigned outcome = 2) {
    auto p = pending.find(id);
    if (p == pending.end())
      return;
    auto u = users.find(p->second.identity->uid);
    if (u != users.end() && u->second.ui >= 0)
      sendPacket(u->second.ui, Type::Finished, id, outcome);
    if (reply != Type::Authenticated)
      sendPacket(p->second.fd, reply, id);
    clients.erase(p->second.fd);
    pending.erase(p);
  }
  void cancelAll(uid_t uid, Type reply = Type::Denied) {
    std::vector<std::uint64_t> ids;
    for (auto &[id, p] : pending)
      if (p.identity->uid == uid)
        ids.push_back(id);
    for (auto id : ids)
      finish(id, reply, reply == Type::Fallback ? 0 : 2);
  }
  void drop(int fd, bool discoveryFailure = false) {
    auto c = clients.find(fd);
    if (c == clients.end())
      return;
    if (c->second.ui) {
      auto uid = c->second.uid;
      auto &u = users.at(uid);
      u.ui = -1;
      u.locked = true;
      ++u.generation;
      cancelAll(uid, discoveryFailure ? Type::Fallback : Type::Denied);
    } else if (c->second.pending) {
      for (auto &[id, p] : pending)
        if (p.fd == fd) {
          finish(id);
          return;
        }
    }
    clients.erase(fd);
  }
  void error(int fd, const char *code) {
    sendText(fd, Type::Error, "{\"error\":" + escapeJson(code) + "}");
  }
  bool keyCapacity(uid_t uid) const {
    return std::count_if(keys.begin(), keys.end(), [&](const auto &key) {
      return key.second.uid == uid;
    }) < 64;
  }
  bool connectionAvailable(uid_t uid, pid_t pid) const {
    // An established provider never competes with idle peers. Its exact pinned
    // process has one additional reconnect slot even when its uid is flooded.
    pid_t provider = 0;
    auto user = users.find(uid);
    if (user != users.end()) {
      auto ui = clients.find(user->second.ui);
      if (ui != clients.end() && ui->second.ui && ui->second.process &&
          sameProcess(*ui->second.process))
        provider = ui->second.process->pid;
    }
    unsigned ordinary = 0, reconnects = 0;
    for (const auto &[fd, client] : clients) {
      (void)fd;
      if (client.uid != uid || client.ui)
        continue;
      if (provider && client.process && client.process->pid == provider)
        ++reconnects;
      else
        ++ordinary;
    }
    if (provider && pid == provider)
      return reconnects == 0;
    // Root PAM connections use their own quota, never another uid's slots.
    return ordinary < (uid == 0 ? 32U : 8U);
  }
  void acceptClient() {
    // Drain more than the listening backlog per tick, but never let continuous
    // producers starve established provider traffic or expiry processing.
    for (unsigned accepted = 0; accepted < 128; ++accepted) {
      Fd fd(accept4(listener.get(), nullptr, nullptr,
                    SOCK_NONBLOCK | SOCK_CLOEXEC));
      if (!fd) {
        if (errno == EINTR)
          continue;
        return;
      }
      pid_t pid;
      uid_t uid;
      if (!peerCredentials(fd.get(), pid, uid))
        continue;
      auto process = inspectPeer(fd.get(), pid, uid);
      if (!process)
        continue;
      Header first{};
      const auto queued = recv(fd.get(), &first, sizeof(first),
                               MSG_PEEK | MSG_DONTWAIT);
      const bool waiting = queued < 0 && (errno == EAGAIN || errno == EWOULDBLOCK);
      const bool revoke = uid && queued == static_cast<ssize_t>(sizeof(first)) &&
          (first.type == Type::Revoke || first.type == Type::RevokeAll);
      if (!revoke && !connectionAvailable(uid, pid)) {
        // connect() can wake the listener before the first send(). Replace an
        // idle first-packet waiter so it cannot reserve the last admission slot.
        // Queued requests, providers and authentication work are never evicted.
        if (!uid || !waiting)
          continue;
        auto idle = std::find_if(clients.begin(), clients.end(), [&](auto &entry) {
          auto &client = entry.second;
          if (client.uid != uid || client.ui || client.pending || client.check)
            return false;
          auto user = users.find(uid);
          if (user != users.end()) {
            auto provider = clients.find(user->second.ui);
            if (provider != clients.end() && client.process &&
                provider->second.process &&
                client.process->pid == provider->second.process->pid)
              return false;
          }
          Header header{};
          return recv(entry.first, &header, sizeof(header),
                      MSG_PEEK | MSG_DONTWAIT) < 0 &&
                 (errno == EAGAIN || errno == EWOULDBLOCK);
        });
        if (idle == clients.end())
          continue;
        drop(idle->first);
      }
      int raw = fd.get();
      clients.emplace(raw, Client{std::move(fd), uid, boottimeSeconds(),
                                  false, false, process, {}, {}, {}});
      if (revoke)
        handle(raw);
    }
  }
  void fallback(int fd) {
    sendPacket(fd, Type::Fallback);
    drop(fd);
  }
  void request(Client &c, Packet &packet, std::optional<IdentityResult> checked) {
    int fd = c.fd.get();
    auto reject = [&] { fallback(fd); };
    if (c.uid || !packet.body.size() || packet.body.size() > 255 ||
        std::any_of(packet.body.data(), packet.body.data() + packet.body.size(),
                    [](unsigned char v) { return v < 33 || v > 126; })) {
      reject();
      return;
    }
    std::string name(reinterpret_cast<const char *>(packet.body.data()),
                     packet.body.size());
    passwd entry{}, *found = nullptr;
    char buffer[16384];
    if (getpwnam_r(name.c_str(), &entry, buffer, sizeof(buffer), &found) ||
        !found || !entry.pw_uid) {
      reject();
      return;
    }
    auto identity = identify(c.process, entry.pw_uid);
    if (!identity) {
      reject();
      return;
    }
    auto uid = identity->uid;
    auto login = session(identity->chain[1]->pid, uid, false);
    auto user = users.find(uid);
    if (login.empty() || user == users.end() ||
        user->second.session != login) {
      reject();
      return;
    }
    auto &u = user->second;
    auto now = boottimeSeconds();
    for (auto &[id, key] : keys) {
      bool descendant = std::any_of(
          identity->chain.begin(), identity->chain.end(), [&](const auto &p) {
            return p->pid == key.owner->pid && p->start == key.owner->start &&
                   p->dev == key.owner->dev && p->ino == key.owner->ino;
          });
      KeyDecision decision{marker(),
                           u.warning,
                           sameProcess(*key.owner),
                           validateIdentity(*identity),
                           key.uid == uid,
                           descendant && key.session == login,
                           false,
                           key.expires,
                           now};
      if (mayReuse(decision)) {
        if (!rememberParent(id, *identity)) {
          std::fprintf(stderr, "sudo timestamp cleanup: parent registration refused; ending key %llu\n",
                       static_cast<unsigned long long>(id));
          endKey(keys.find(id));
          break;
        }
        sendPacket(fd, Type::Granted);
        drop(fd);
        return;
      }
    }
    if (!checked)
      c.eligibleReporter = turnReporter(*identity, login);
    if (!checked && liveUiProcess(uid)) {
      checkIdentity(fd, clients.at(u.ui), login, packet);
      return;
    }
    if (checked != IdentityResult::Trusted || !liveUiProcess(uid) ||
        session(identity->chain[1]->pid, uid) != login) {
      reject();
      return;
    }
    if (std::any_of(pending.begin(), pending.end(), [&](const auto &p) {
          return p.second.identity->uid == uid;
        })) {
      reject();
      return;
    }
    auto id = next++;
    std::string chain = "[";
    for (const auto &p : identity->chain) {
      if (chain.size() > 1)
        chain += ',';
      chain += escapeJson(p->executable);
    }
    chain += ']';
    auto prefix = "/proc/" + std::to_string(identity->sudo->pid) + "/";
    const auto requester = requesterLabel(*identity);
    const auto eligibleReporter = c.eligibleReporter == turnReporter(*identity, login)
        ? c.eligibleReporter : nullptr;
    const auto command = parseSudoArguments(processArguments(identity->sudo->pid));
    std::string metadata =
        "{\"id\":" + escapeJson(std::to_string(id)) +
        ",\"agent\":" + (requester.empty() ? "false" : "true") +
        ",\"requester\":" + escapeJson(requester) +
        ",\"processChain\":" + chain +
        ",\"command\":" + escapeJson(command.command) +
        ",\"workingDirectory\":" + escapeJson(readLinkBounded(prefix + "cwd")) +
        ",\"user\":" + escapeJson(name) +
        ",\"targetUser\":\"\",\"keyAvailable\":" +
        (marker() ? "true" : "false") +
        ",\"keyEligible\":" + (keyOwnerEligible(*identity) ? "true" : "false") +
        ",\"turnEligible\":" + (eligibleReporter ? "true" : "false") +
        ",\"warningAccepted\":" + (u.warning ? "true" : "false") + "}";
    if (!validateIdentity(*identity) || !sendPacket(fd, Type::Waiting, id) ||
        !sendText(u.ui, Type::Request, metadata, id)) {
      fallback(fd);
      return;
    }
    c.pending = true;
    pending.emplace(
        id, Pending{id, now, u.generation, fd, std::move(identity), login, {}, {}, 0});
    pending.at(id).eligibleReporter = eligibleReporter;
    return;
  }
  void submit(Client &c, User &u, Packet &packet) {
    auto p = pending.find(packet.header.request);
    if (p == pending.end() || p->second.identity->uid != c.uid ||
        p->second.candidate.submitted() || !uiAvailable(c.uid) ||
        (packet.header.option && !validDuration(packet.header.option)) ||
        !validPassword(packet.body)) {
      error(c.fd.get(), "invalid-submission");
      return;
    }
    auto &i = *p->second.identity;
    if (!validateIdentity(i) || !moduleMapped(*i.sudo)) {
      finish(p->first);
      return;
    }
    if (turnCount(packet.header.option)) {
      auto r = turnReporter(i, p->second.session);
      if (!r || r != p->second.eligibleReporter) {
        error(c.fd.get(), "turn-reporting-unavailable");
        return;
      }
      p->second.reporter = r;
      p->second.completed = r->completed;
    }
    if (packet.header.option &&
        (!marker() || !u.warning || !keyOwnerEligible(i))) {
      error(c.fd.get(), "key-unavailable");
      return;
    }
    if (!p->second.candidate.submit(packet.header.option,
                                    p->second.generation)) {
      finish(p->first);
      return;
    }
    bool sent = sendPacket(p->second.fd, Type::Password, p->first, 0,
                           packet.body.data(), packet.body.size());
    packet.body.wipe();
    if (!sent)
      finish(p->first);
  }
  void verdict(int fd, Packet &packet, std::optional<IdentityResult> checked) {
    const auto &h = packet.header;
    auto p = pending.find(h.request);
    if (p == pending.end() || p->second.fd != fd ||
        !p->second.candidate.submitted() || h.size || h.option ||
        (h.type != Type::Authenticated && h.type != Type::Rejected)) {
      drop(fd);
      return;
    }
    if (!p->second.confirming && boottimeSeconds() - p->second.created >= 120) {
      finish(p->first);
      return;
    }
    if (h.type == Type::Authenticated)
      p->second.confirming = true;
    auto &i = *p->second.identity;
    auto &u = users.at(i.uid);
    const bool provider = liveUiProcess(i.uid);
    if (h.type == Type::Authenticated && !checked &&
        provider) {
      checkIdentity(fd, clients.at(u.ui), u.session, packet);
      return;
    }
    // Once the lock scan succeeds, socket discovery cost cannot deny a password.
    // Unknown lock state fails closed; reusable grants also need trusted IPC.
    bool success = h.type == Type::Authenticated && validateIdentity(i) &&
                   moduleMapped(*i.sudo) && !u.locked && checked &&
                   *checked != IdentityResult::LockUnknown &&
                   u.session == p->second.session &&
                   session(i.chain[1]->pid, i.uid) == p->second.session;
    const auto &reporter = p->second.reporter;
    bool turnReady = !reporter ||
        (turnReporter(i, p->second.session) == reporter &&
         reporter->completed == p->second.completed);
    if (p->second.candidate.confirm(success && provider &&
                                    checked == IdentityResult::Trusted,
                                    u.generation, marker(), u.warning,
                                    keyOwnerEligible(i)) &&
        turnReady && keyCapacity(i.uid) && boottimeSeconds() != UINT64_MAX) {
      for (auto it = keys.begin(); it != keys.end();)
        if (it->second.uid == i.uid && it->second.owner->pid == i.owner->pid &&
            it->second.owner->start == i.owner->start)
          it = endKey(it);
        else
          ++it;
      auto id = next++;
      if (rememberParent(id, i))
        keys.emplace(id,
                   Key{id,
                       p->second.candidate.duration() == Session
                           ? 0
                           : boottimeSeconds() + (reporter ? TurnHardCap : p->second.candidate.duration()),
                       i.uid, p->second.session, i.owner, requesterLabel(i),
                       reporter, turnCount(p->second.candidate.duration())});
    }
    auto uid = i.uid;
    // Keep sudo inside its PAM account callback until the service has checked
    // the still-pinned authentication process and committed any selected grant.
    if (h.type == Type::Authenticated)
      sendPacket(fd, success ? Type::Response : Type::Denied, h.request);
    finish(h.request, Type::Authenticated, success ? 1 : 2);
    broadcast(uid);
    return;
  }
  static bool denyOnly(Type type) {
    return type == Type::Lock || type == Type::Cancel ||
           type == Type::Revoke || type == Type::RevokeAll;
  }
  bool queuedDenial(int fd) {
    auto c = clients.find(fd);
    if (c == clients.end() || !c->second.ui)
      return false;
    Header header{};
    return recv(fd, &header, sizeof(header), MSG_PEEK | MSG_DONTWAIT) ==
               static_cast<ssize_t>(sizeof(header)) && denyOnly(header.type);
  }
  void handle(int fd) {
    auto found = clients.find(fd);
    if (found == clients.end())
      return;
    if (found->second.check && !queuedDenial(fd))
      return;
    found->second.check.reset();
    Packet packet;
    if (!receivePacket(fd, packet)) {
      drop(fd);
      return;
    }
    processPacket(fd, packet);
  }
  void processPacket(int fd, Packet &packet,
                     std::optional<IdentityResult> checked = {}) {
    auto found = clients.find(fd);
    if (found == clients.end())
      return;
    auto &c = found->second;
    if (!sameProcess(*c.process)) {
      drop(fd);
      return;
    }
    auto &h = packet.header;
    if (c.pending) {
      verdict(fd, packet, checked);
      return;
    }
    bool turnReport = h.type == Type::TurnStart || h.type == Type::TurnEnd ||
                      h.type == Type::TurnComplete;
    if (h.type != Type::Submit && h.type != Type::Authenticate && !turnReport &&
        (h.size || h.option)) {
      drop(fd);
      return;
    }
    if (h.type != Type::Submit && h.type != Type::Cancel &&
        h.type != Type::Revoke && h.request) {
      drop(fd);
      return;
    }
    if (h.type == Type::Authenticate && !c.ui && !h.option) {
      request(c, packet, checked);
      return;
    }
    if (c.uid == 0) {
      drop(fd);
      return;
    }
    if (turnReport && !c.ui && !h.option && !h.request) {
      reportTurn(c, packet);
      return;
    }
    if (c.ui) {
      auto &u = users.at(c.uid);
      // A registered peer can only reduce authority through these messages.
      // Process pinning and packet validation above still apply; do not delay
      // a lock/cancellation behind another session's discovery work.
      if (!denyOnly(h.type)) {
        if (!freshHeartbeat(c.since, boottimeSeconds()) ||
            session(c.process->pid, c.uid) != u.session) {
          drop(fd, !freshHeartbeat(c.since, boottimeSeconds()));
          return;
        }
        if (!validUiProcess(*c.process, c.uid, c.parent, u.session, c.controller)) {
          drop(fd);
          return;
        }
        if (!checked) {
          checkIdentity(fd, c, u.session, packet);
          return;
        }
        if (*checked != IdentityResult::Trusted) {
          drop(fd, true);
          return;
        }
      }
      if (h.type == Type::Status)
        c.since = boottimeSeconds();
      switch (h.type) {
      case Type::Submit:
        submit(c, u, packet);
        break;
      case Type::Cancel: {
        auto p = pending.find(h.request);
        if (p != pending.end() && p->second.identity->uid == c.uid)
          finish(h.request);
        break;
      }
      case Type::Lock:
        u.locked = true;
        ++u.generation;
        cancelAll(c.uid);
        break;
      case Type::Unlock:
        u.locked = false;
        broadcast(c.uid);
        break;
      case Type::AcknowledgeWarning:
        if (uiAvailable(c.uid) && marker())
          u.warning = warnings.acknowledge(c.uid);
        broadcast(c.uid);
        break;
      case Type::RevokeAll:
        eraseKeys(c.uid);
        break;
      case Type::Revoke: {
        auto k = keys.find(h.request);
        if (k != keys.end() && k->second.uid == c.uid)
          endKey(k);
        ++u.generation;
        broadcast(c.uid);
        break;
      }
      case Type::Status:
        sendText(fd, Type::Response, status(c.uid));
        break;
      default:
        drop(fd);
        break;
      }
      return;
    }
    switch (h.type) {
    case Type::HelloUi: {
      auto login = session(c.process->pid, c.uid);
      c.parent = inspectProcess(c.process->ppid);
      if (!checked) {
        checkIdentity(fd, c, login, packet);
        return;
      }
      if (login.empty() || *checked != IdentityResult::Trusted ||
          !validUiProcess(*c.process, c.uid, c.parent, login, c.controller) ||
          (users.contains(c.uid) && users.at(c.uid).ui >= 0)) {
        error(fd, "frontend-unverified");
        drop(fd);
        break;
      }
      auto &u = users[c.uid];
      c.ui = true;
      u.ui = fd;
      u.locked = true;
      if (u.session != login) {
        eraseKeys(c.uid);
      }
      u.warning = marker() && warnings.accepted(c.uid);
      u.session = login;
      ++u.generation;
      sendText(fd, Type::Response, "{\"ok\":true}");
      broadcast(c.uid);
      break;
    }
    case Type::Status:
      sendText(fd, Type::Response, status(c.uid));
      drop(fd);
      break;
    case Type::Revoke: {
      auto k = keys.find(h.request);
      if (k != keys.end() && k->second.uid == c.uid)
        endKey(k);
      if (users.contains(c.uid))
        ++users.at(c.uid).generation;
      broadcast(c.uid);
      sendText(fd, Type::Response, "{\"ok\":true}");
      drop(fd);
      break;
    }
    case Type::RevokeAll:
      eraseKeys(c.uid);
      sendText(fd, Type::Response, "{\"ok\":true}");
      drop(fd);
      break;
    default:
      error(fd, "unauthorized-method");
      drop(fd);
      break;
    }
  }
  void reap() {
    auto now = boottimeSeconds();
    bool installed = marker();
    if (installed != hadMarker) {
      for (auto &[uid, u] : users) {
        u.warning = false;
        ++u.generation;
        broadcast(uid);
      }
      hadMarker = installed;
    }
    for (auto &[uid, u] : users) {
      const bool accepted = installed && warnings.accepted(uid);
      if (u.warning != accepted) {
        u.warning = accepted;
        ++u.generation;
        broadcast(uid);
      }
    }
    std::set<uid_t> changed;
    for (auto it = keys.begin(); it != keys.end();) {
      auto &k = it->second;
      if (!installed || !sameProcess(*k.owner) ||
          (k.expires && now >= k.expires) ||
          session(k.owner->pid, k.uid, false) != k.session) {
        changed.insert(k.uid);
        it = endKey(it);
      } else
        ++it;
    }
    for (auto it = reporters.begin(); it != reporters.end();) {
      const auto &r = it->second;
      if (!installed || !sameProcess(*r->owner) ||
          session(r->owner->pid, r->owner->uid, false) != r->login ||
          now - r->seen >= TurnHardCap) {
        for (auto key = keys.begin(); key != keys.end();)
          if (key->second.reporter == r) {
            changed.insert(key->second.uid);
            key = endKey(key);
          } else
            ++key;
        it = reporters.erase(it);
      } else
        ++it;
    }
    std::vector<std::pair<std::uint64_t, Type>> expired;
    for (auto &[id, p] : pending) {
      if ((!p.confirming && now - p.created >= 120) || !validateIdentity(*p.identity) ||
          users.at(p.identity->uid).locked)
        expired.emplace_back(id, Type::Denied);
      else if (!providerAlive(p.identity->uid))
        expired.emplace_back(id, Type::Fallback);
    }
    for (auto [id, reply] : expired)
      finish(id, reply, reply == Type::Fallback ? 0 : 2);
    std::vector<std::pair<int, bool>> idle;
    std::vector<int> order;
    for (const auto &[fd, c] : clients)
      order.push_back(fd);
    for (auto fd : order) {
      auto &c = clients.at(fd);
      bool invalid = !c.ui && !c.pending && now - c.since >= 1;
      if (c.ui) {
        invalid = !freshHeartbeat(c.since, now) || !sameProcess(*c.process) ||
            !c.parent || !sameProcess(*c.parent) ||
            (c.controller.process && !sameProcess(*c.controller.process)) ||
            controllerState(users.at(c.uid).session) !=
                std::optional<std::string>(c.controller.name) ||
            session(c.process->pid, c.uid) != users.at(c.uid).session;
      }
      if (invalid)
        idle.emplace_back(fd, c.ui && !freshHeartbeat(c.since, now));
    }
    for (auto [fd, unavailable] : idle)
      drop(fd, unavailable);
    for (auto uid : changed)
      broadcast(uid);
  }
  void dispatchReady(std::vector<pollfd> &fds) {
    // The listener is first, followed by the ordered client descriptors.
    auto first = fds.begin() + 1;
    auto pivot = std::upper_bound(first, fds.end(), handledCursor,
        [](int fd, const pollfd &item) { return fd < item.fd; });
    std::rotate(first, pivot, fds.end());
    if (fds.front().revents)
      acceptClient();
    // Drain a ready denial before root authentication requests can observe the
    // old unlocked state. Only one packet per descriptor is processed per tick.
    for (auto it = first; it != fds.end(); ++it)
      if (queuedDenial(it->fd)) {
        handle(it->fd);
        it->revents = 0;
      }
    for (auto it = first; it != fds.end(); ++it) {
      auto c = clients.find(it->fd);
      if ((it->revents & POLLIN) && c != clients.end() && c->second.pending) {
        handle(it->fd);
        it->revents = 0;
      }
    }
    for (auto it = first; it != fds.end(); ++it) {
      auto c = clients.find(it->fd);
      if ((it->revents & POLLIN) && c != clients.end() && !c->second.uid) {
        handle(it->fd);
        it->revents = 0;
      }
    }
    for (auto it = first; it != fds.end(); ++it) {
      if (!it->revents)
        continue;
      if (!(it->revents & POLLIN)) {
        drop(it->fd);
        continue;
      }
      auto c = clients.find(it->fd);
      if (c == clients.end())
        continue;
      if (c->second.uid && !c->second.pending) {
        handledCursor = it->fd;
      }
      handle(it->fd);
    }
  }
  struct PollInputs {
    std::vector<pollfd> descriptors;
    std::size_t clientCount;
    int timeout = 250;
  };
  PollInputs pollInputs() {
    PollInputs inputs{{{listener.get(), POLLIN, 0}}, clients.size() + 1};
    for (const auto &[fd, c] : clients)
      inputs.descriptors.push_back({fd, static_cast<short>(c.check ? 0 : POLLIN), 0});
    for (const auto &[fd, c] : clients) {
      if (!c.check)
        continue;
      inputs.descriptors.push_back({c.check->result.get(), POLLIN, 0});
      const auto remaining = std::chrono::ceil<std::chrono::milliseconds>(
          c.check->deadline - IdentityCheck::Clock::now()).count();
      inputs.timeout = std::min(inputs.timeout, static_cast<int>(std::max(0L, remaining)));
    }
    return inputs;
  }
  int run() {
    if (!listener)
      return 1;
    std::uint64_t updated = UINT64_MAX;
    while (!stopping) {
      auto now = boottimeSeconds();
      if (now == UINT64_MAX)
        return 1;
      if (updated != now) {
        for (auto &[uid, u] : users)
          broadcast(uid);
        updated = now;
      }
      auto inputs = pollInputs();
      auto &fds = inputs.descriptors;
      if (poll(fds.data(), fds.size(), inputs.timeout) < 0) {
        if (errno == EINTR)
          continue;
        return 1;
      }
      fds.resize(inputs.clientCount);
      dispatchReady(fds);
      completeChecks();
      reap();
      timestamps.poll();
    }
    endKeys();
    return timestamps.drain(std::chrono::seconds(8)) ? 0 : 1;
  }
};
} // namespace
int main(int argc, char **argv) {
  if (argc != 2 || (std::string_view(argv[1]) != "--serve" &&
                    std::string_view(argv[1]) != "--cleanup" &&
                    std::string_view(argv[1]) != "--cleanup-if-idle") || getuid() ||
      geteuid() || !harden())
    return 1;
  umask(0077);
  if (std::string_view(argv[1]) != "--serve") {
    TimestampCleanup timestamps;
    if (!timestamps.initialize(SocketDirectory, "/run/sudo/ts")) {
      // An active broker or another drain owns all retry work. The timer must
      // neither end live keys nor race that owner. Removal still fails closed.
      if (timestamps.busy()) {
        if (std::string_view(argv[1]) == "--cleanup-if-idle")
          return 0;
        std::fprintf(stderr, "sudo timestamp cleanup: journal busy; retry required\n");
      }
      return 1;
    }
    return timestamps.drain(std::chrono::seconds(30)) ? 0 : 1;
  }
  signal(SIGTERM, stop);
  signal(SIGINT, stop);
  Broker broker;
  return broker.run();
}
