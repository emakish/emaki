// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cerrno>
#include <charconv>
#include <string_view>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <limits>
#include <poll.h>
#include <sstream>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
namespace emaki::sudo_channel {
Fd::~Fd() { reset(); }
Fd::Fd(Fd &&o) noexcept : value_(o.release()) {}
Fd &Fd::operator=(Fd &&o) noexcept {
  if (this != &o)
    reset(o.release());
  return *this;
}
int Fd::release() {
  int result = value_;
  value_ = -1;
  return result;
}
void Fd::reset(int value) {
  if (value_ >= 0)
    close(value_);
  value_ = value;
}
Secret::Secret(std::size_t capacity) {
  const auto page = static_cast<std::size_t>(sysconf(_SC_PAGESIZE));
  capacity_ = (capacity + page - 1) / page * page;
  void *p = mmap(nullptr, capacity_, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (p == MAP_FAILED)
    return;
  if (mlock(p, capacity_) != 0 || madvise(p, capacity_, MADV_DONTDUMP) != 0 ||
      madvise(p, capacity_, MADV_WIPEONFORK) != 0) {
    explicit_bzero(p, capacity_);
    munlock(p, capacity_);
    munmap(p, capacity_);
    return;
  }
  data_ = static_cast<unsigned char *>(p);
}
Secret::~Secret() {
  if (data_) {
    wipe();
    munlock(data_, capacity_);
    munmap(data_, capacity_);
  }
}
bool Secret::assign(const void *p, std::size_t size) {
  if (!data_ || size > capacity_)
    return false;
  wipe();
  if (size)
    memcpy(data_, p, size);
  size_ = size;
  return true;
}
void Secret::setSize(std::size_t n) { size_ = n <= capacity_ ? n : 0; }
void Secret::wipe() {
  if (data_)
    explicit_bzero(data_, capacity_);
  size_ = 0;
}
bool harden() {
  rlimit lim{0, 0};
  return setrlimit(RLIMIT_CORE, &lim) == 0 && prctl(PR_SET_DUMPABLE, 0) == 0;
}
bool sendPacket(int fd, Type type, std::uint64_t request, std::uint32_t option,
                const void *data, std::size_t size) {
  if (size > MaxBody)
    return false;
  Header h;
  h.type = type;
  h.request = request;
  h.option = option;
  h.size = static_cast<std::uint32_t>(size);
  iovec io[] = {{&h, sizeof(h)}, {const_cast<void *>(data), size}};
  msghdr msg{};
  msg.msg_iov = io;
  msg.msg_iovlen = size ? 2 : 1;
  auto sent = sendmsg(fd, &msg, MSG_NOSIGNAL | MSG_DONTWAIT);
  return sent == static_cast<ssize_t>(sizeof(h) + size);
}
bool sendText(int fd, Type type, const std::string &s, std::uint64_t request) {
  return sendPacket(fd, type, request, 0, s.data(), s.size());
}
bool receivePacket(int fd, Packet &p) {
  if (!p.body.valid()) {
    errno = ENOMEM;
    return false;
  }
  p.body.wipe();
  iovec io[] = {{&p.header, sizeof(p.header)}, {p.body.data(), MaxBody}};
  msghdr msg{};
  msg.msg_iov = io;
  msg.msg_iovlen = 2;
  auto got = recvmsg(fd, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
  if (got < 0)
    return false;
  if (!got) {
    errno = ECONNRESET;
    return false;
  }
  if (got < static_cast<ssize_t>(sizeof(Header)) ||
      (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC))) {
    errno = EPROTO;
    return false;
  }
  auto &h = p.header;
  if (h.magic != Magic || h.version != Version || h.reserved ||
      h.size > MaxBody || got != static_cast<ssize_t>(sizeof(Header) + h.size)) {
    errno = EPROTO;
    return false;
  }
  p.body.setSize(h.size);
  return true;
}
bool peerCredentials(int fd, pid_t &pid, uid_t &uid) {
  ucred c{};
  socklen_t n = sizeof(c);
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &c, &n) || n != sizeof(c) ||
      c.pid <= 0)
    return false;
  pid = c.pid;
  uid = c.uid;
  return true;
}
Fd connectBroker() {
  Fd channel(open(SocketDirectory, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  struct stat st{};
  if (!channel || fstat(channel.get(), &st) || st.st_uid != 0 ||
      (st.st_mode & 0777) != 0755)
    return {};
  if (fstatat(channel.get(), "broker.sock", &st, AT_SYMLINK_NOFOLLOW) ||
      !S_ISSOCK(st.st_mode) || st.st_uid != 0 || (st.st_mode & 0777) != 0666)
    return {};
  Fd sock(socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0));
  if (!sock)
    return {};
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  std::string path = SocketPath;
  if (path.size() >= sizeof(address.sun_path))
    return {};
  memcpy(address.sun_path, path.c_str(), path.size() + 1);
  if (connect(sock.get(), reinterpret_cast<sockaddr *>(&address),
              sizeof(address)))
    return {};
  pid_t pid;
  uid_t uid;
  if (!peerCredentials(sock.get(), pid, uid) || uid != 0)
    return {};
  return sock;
}
std::string escapeJson(const std::string &s) {
  std::string out = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += static_cast<char>(c);
    } else if (c >= 32 && c < 127)
      out += static_cast<char>(c);
    else
      out += '?';
  }
  return out + '"';
}
std::uint64_t boottimeSeconds() {
  timespec ts{};
  if (clock_gettime(CLOCK_BOOTTIME, &ts))
    return UINT64_MAX;
  return static_cast<std::uint64_t>(ts.tv_sec);
}
bool authenticationBlocked(int runtime, uid_t uid, bool *unavailable) {
  if (unavailable)
    *unavailable = true;
  struct stat st{};
  if (runtime < 0 || fstat(runtime, &st) || st.st_uid != uid ||
      (st.st_mode & 0777) != 0700)
    return true;
  Fd copy(openat(runtime, ".", O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
  if (!copy)
    return true;
  DIR *entries = fdopendir(copy.release());
  if (!entries)
    return true;
  bool blocked = false;
  unsigned count = 0;
  while (true) {
    errno = 0;
    auto *entry = readdir(entries);
    if (!entry) {
      blocked = errno != 0;
      if (unavailable)
        *unavailable = blocked;
      break;
    }
    if (++count > 1024) {
      blocked = true;
      break;
    }
    std::string_view filename(entry->d_name);
    if (!filename.starts_with("emaki-lock-"))
      continue;
    Fd lock(
        openat(runtime, entry->d_name, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    if (!lock || fstat(lock.get(), &st) || st.st_uid != uid ||
        (st.st_mode & 0777) != 0700) {
      blocked = true;
      break;
    }
    if (!fstatat(lock.get(), "authentication-blocked", &st, AT_SYMLINK_NOFOLLOW)) {
      if (unavailable)
        *unavailable = false;
      blocked = true;
      break;
    }
    if (errno != ENOENT) {
      blocked = true;
      break;
    }
  }
  closedir(entries);
  return blocked;
}
bool freshHeartbeat(std::uint64_t last, std::uint64_t now) {
  return now != UINT64_MAX && now >= last && now - last <= 3;
}
bool validDuration(std::uint32_t n) {
  return validTimeDuration(n) || n == Session || turnCount(n);
}
bool validPassword(const Secret &s) {
  if (!s.size() || s.size() > MaxPassword)
    return false;
  return std::none_of(s.data(), s.data() + s.size(), [](unsigned char c) {
    return c == 0 || c == '\n' || c == '\r';
  });
}
std::string readBounded(const std::string &path, std::size_t limit) {
  Fd f(open(path.c_str(), O_RDONLY | O_CLOEXEC));
  if (!f)
    return {};
  std::string data;
  char buffer[4096];
  for (;;) {
    // Probe one byte after the limit as well: a complete bounded file must
    // reach EOF, including when procfs returns a short page-sized read.
    const auto remaining = limit - data.size();
    const auto requested = remaining < sizeof(buffer) ? remaining + 1 : sizeof(buffer);
    const auto n = read(f.get(), buffer, requested);
    if (n < 0) {
      if (errno == EINTR)
        continue;
      return {};
    }
    if (!n)
      return data;
    if (static_cast<std::size_t>(n) > remaining)
      return {};
    data.append(buffer, static_cast<std::size_t>(n));
  }
}
std::string readLinkBounded(const std::string &p) {
  char buf[4097];
  auto n = readlink(p.c_str(), buf, sizeof(buf));
  if (n < 0 || n >= static_cast<ssize_t>(sizeof(buf)))
    return {};
  return {buf, static_cast<std::size_t>(n)};
}
std::string knownRequester(const std::string &executable,
                           const std::vector<std::string> &arguments) {
  const auto base = executable.substr(executable.rfind('/') + 1);
  if (base == "claude" || base == "claude-code")
    return "Claude Code";
  if (base == "codex")
    return "Codex";
  // The native installer keeps version-number executables in this directory.
  const std::string versions = "/claude/versions/";
  const auto pos = executable.rfind(versions);
  if (pos != std::string::npos && pos + versions.size() < executable.size()) {
    const auto version = executable.substr(pos + versions.size());
    if (version.front() >= '0' && version.front() <= '9' &&
        version.find('.') != std::string::npos &&
        version.find_first_not_of("0123456789.-abcdefghijklmnopqrstuvwxyz") ==
            std::string::npos)
      return "Claude Code";
  }
  // Interpreted entry points count only as the actual script argument. A name
  // mentioned in an unrelated command's arguments is not an identity hint.
  if ((base == "node" || base == "nodejs" || base == "bun") &&
      arguments.size() > 1) {
    const auto &script = arguments[1];
    if (script.ends_with("/node_modules/@anthropic-ai/claude-code/cli.js"))
      return "Claude Code";
    if (script.ends_with("/node_modules/@openai/codex/bin/codex.js"))
      return "Codex";
  }
  return {};
}
static bool snapshot(Process &p) {
  auto prefix = "/proc/" + std::to_string(p.pid) + "/";
  auto stat = readBounded(prefix + "stat", 8192);
  auto end = stat.rfind(") ");
  if (end == std::string::npos)
    return false;
  std::istringstream fields(stat.substr(end + 2));
  std::string field;
  for (int index = 0; index <= 19; ++index) {
    if (!(fields >> field))
      return false;
    try {
      if (index == 1)
        p.ppid = std::stoi(field);
      if (index == 3)
        p.sid = std::stoi(field);
      if (index == 4)
        p.tty = std::stoll(field);
      if (index == 19)
        p.start = std::stoull(field);
    } catch (...) {
      return false;
    }
  }
  auto status = readBounded(prefix + "status", 16384);
  auto pos = status.find("\nUid:");
  if (pos == std::string::npos)
    return false;
  std::istringstream ids(status.substr(pos + 5));
  unsigned long long uid, euid;
  if (!(ids >> uid >> euid) || uid > UINT32_MAX || euid > UINT32_MAX)
    return false;
  p.uid = uid;
  p.euid = euid;
  Fd exe(open((prefix + "exe").c_str(), O_PATH | O_CLOEXEC));
  struct stat st{};
  if (!exe || fstat(exe.get(), &st) || !S_ISREG(st.st_mode))
    return false;
  p.dev = st.st_dev;
  p.ino = st.st_ino;
  p.executable = readLinkBounded(prefix + "exe");
  return !p.executable.empty() && p.start > 0;
}
std::shared_ptr<Process> inspectProcess(pid_t pid) {
  return inspectProcess(pid, Fd(static_cast<int>(syscall(SYS_pidfd_open, pid, 0))));
}
std::shared_ptr<Process> inspectProcess(pid_t pid, Fd descriptor) {
  if (pid <= 1 || !descriptor)
    return {};
  // A descriptor from socket/bus credentials must pin the supplied PID, not a
  // later process that happens to reuse its number.
  auto info = readBounded("/proc/self/fdinfo/" +
                              std::to_string(descriptor.get()), 4096);
  std::istringstream lines(info);
  std::string line;
  bool matches = false;
  while (std::getline(lines, line)) {
    if (line.starts_with("Pid:")) {
      std::istringstream value(line.substr(4));
      pid_t pinned = 0;
      std::string extra;
      matches = (value >> pinned) && !(value >> extra) && pinned == pid;
      break;
    }
  }
  if (!matches)
    return {};
  auto p = std::make_shared<Process>();
  p->pid = pid;
  p->pidfd = std::move(descriptor);
  if (!p->pidfd || !snapshot(*p) || !sameProcess(*p))
    return {};
  return p;
}
std::shared_ptr<Process> inspectPeer(int socket, pid_t pid, uid_t uid) {
#ifdef SO_PEERPIDFD
  int value = -1;
  socklen_t size = sizeof(value);
  if (getsockopt(socket, SOL_SOCKET, SO_PEERPIDFD, &value, &size) ||
      size != sizeof(value) || value < 0)
    return {};
  Fd descriptor(value);
  if (fcntl(descriptor.get(), F_SETFD, FD_CLOEXEC))
    return {};
  auto process = inspectProcess(pid, std::move(descriptor));
  if (!process || (uid != 0 && process->uid != uid) || process->euid != uid)
    return {};
  return process;
#else
  (void)socket;
  (void)pid;
  (void)uid;
  errno = ENOTSUP;
  return {};
#endif
}
bool sameProcess(const Process &p) {
  pollfd fd{p.pidfd.get(), POLLIN, 0};
  if (fd.fd < 0 || poll(&fd, 1, 0) != 0)
    return false;
  Process now;
  now.pid = p.pid;
  if (!snapshot(now))
    return false;
  return now.start == p.start && now.ppid == p.ppid && now.dev == p.dev &&
         now.ino == p.ino && now.uid == p.uid && now.euid == p.euid &&
         now.tty == p.tty && now.sid == p.sid;
}
bool samePinnedProcess(const Process &a, const Process &b) {
  return a.pid == b.pid && a.start == b.start && a.ppid == b.ppid &&
         a.dev == b.dev && a.ino == b.ino && a.uid == b.uid &&
         a.euid == b.euid && a.tty == b.tty && a.sid == b.sid &&
         sameProcess(a) && sameProcess(b);
}
bool trustedShellReply(const std::string &reply, const Process &provider) {
  if (reply.size() > 512 || reply.empty() || reply.back() != '\n')
    return false;
  std::string_view input(reply);
  auto whitespace = [&] {
    while (!input.empty() && (input.front() == ' ' || input.front() == '\t' ||
                             input.front() == '\r' || input.front() == '\n'))
      input.remove_prefix(1);
  };
  auto token = [&](std::string_view value) {
    whitespace();
    if (!input.starts_with(value))
      return false;
    input.remove_prefix(value.size());
    return true;
  };
  auto number = [&](std::uint64_t &value) {
    whitespace();
    if (input.empty() || input.front() < '1' || input.front() > '9')
      return false;
    auto result = std::from_chars(input.data(), input.data() + input.size(), value);
    if (result.ec != std::errc{})
      return false;
    input.remove_prefix(static_cast<std::size_t>(result.ptr - input.data()));
    return true;
  };
  std::uint64_t pid = 0, start = 0;
  if (!token("{") || !token("\"Ok\"") || !token(":") || !token("{") ||
      !token("\"TrustedShellIdentity\"") || !token(":") || !token("{") ||
      !token("\"pid\"") || !token(":") || !number(pid) || !token(",") ||
      !token("\"start_time\"") || !token(":") || !number(start) ||
      !token("}") || !token("}") || !token("}"))
    return false;
  whitespace();
  return input.empty() && provider.pid > 1 &&
         pid == static_cast<std::uint64_t>(provider.pid) && start == provider.start;
}
bool safeRootFile(const char *path) {
  Fd f(open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC));
  struct stat st{};
  return f && fstat(f.get(), &st) == 0 && S_ISREG(st.st_mode) &&
         st.st_uid == 0 && (st.st_mode & 0022) == 0;
}
bool trustedExecutable(const Process &p, const char *path) {
  Fd f(open(path, O_PATH | O_NOFOLLOW | O_CLOEXEC));
  struct stat st{};
  return f && fstat(f.get(), &st) == 0 && S_ISREG(st.st_mode) &&
         st.st_uid == 0 && (st.st_mode & 0022) == 0 &&
         p.dev == static_cast<std::uint64_t>(st.st_dev) &&
         p.ino == static_cast<std::uint64_t>(st.st_ino);
}
bool trustedRunningExecutable(const Process &p, const char *path) {
  if (!path || !*path || !sameProcess(p))
    return false;
  const auto executable = "/proc/" + std::to_string(p.pid) + "/exe";
  // Inspect the running inode, including an unlinked package version. Looking
  // up the installed path instead would compare it with the replacement inode.
  Fd f(open(executable.c_str(), O_PATH | O_CLOEXEC));
  struct stat st{};
  if (!f || fstat(f.get(), &st) || !S_ISREG(st.st_mode) || st.st_uid != 0 ||
      (st.st_mode & 0022) != 0 ||
      p.dev != static_cast<std::uint64_t>(st.st_dev) ||
      p.ino != static_cast<std::uint64_t>(st.st_ino))
    return false;
  const auto runningPath = readLinkBounded(executable);
  return (runningPath == path || runningPath == std::string(path) + " (deleted)") &&
         sameProcess(p);
}
bool validateIdentity(const Identity &i, bool requireHelper) {
  if (requireHelper && (!i.helper || !sameProcess(*i.helper) || !i.sudo ||
                        i.helper->ppid != i.sudo->pid))
    return false;
  if (!i.sudo || !sameProcess(*i.sudo) || i.sudo->euid != 0 ||
      i.sudo->tty != 0 || i.chain.empty() || i.chain.front() != i.sudo)
    return false;
  for (std::size_t n = 0; n < i.chain.size(); ++n) {
    if (!sameProcess(*i.chain[n]) ||
        (n && (i.chain[n]->uid != i.uid || i.chain[n]->euid != i.uid)))
      return false;
    if (n + 1 < i.chain.size() && i.chain[n]->ppid != i.chain[n + 1]->pid)
      return false;
  }
  return true;
}
bool privilegedPamPeer(uid_t peerUid, uid_t effectiveUid, long long tty,
                       bool trustedSudo, bool trustedModule) {
  return peerUid == 0 && effectiveUid == 0 && tty == 0 && trustedSudo &&
         trustedModule;
}
bool sameOwner(const Identity &a, const Identity &b) {
  return a.uid == b.uid && a.owner && b.owner && a.owner->pid == b.owner->pid &&
         a.owner->start == b.owner->start && a.owner->dev == b.owner->dev &&
         a.owner->ino == b.owner->ino && sameProcess(*a.owner) &&
         sameProcess(*b.owner);
}
bool trustedUiPeer(const UiPeerDecision &d) {
  return d.sameUid && d.parentUid && d.stableUi && d.stableParent &&
         d.trustedUi && d.directParent && d.uiArguments &&
         ((d.trustedManager && d.exactUnit && d.managerArguments) ||
          (d.trustedCompositor && d.sessionController));
}
std::string parseSessionController(const std::string &state) {
  if (state.empty() || state.size() > 16384 || state.back() != '\n' ||
      state.find('\0') != std::string::npos)
    return {};
  std::istringstream lines(state);
  std::string line, controller;
  while (std::getline(lines, line)) {
    if (!line.starts_with("CONTROLLER="))
      continue;
    if (!controller.empty())
      return {};
    auto value = line.substr(11);
    // Only a bus-assigned unique name is accepted, never a well-known name
    // that a process could acquire after the original controller disappears.
    auto dot = value.find('.', 1);
    if (value.size() > 255 || value.size() < 4 || value[0] != ':' ||
        dot == std::string::npos || dot == 1 || dot + 1 == value.size() ||
        value.substr(1, dot - 1).find_first_not_of("0123456789") !=
            std::string::npos ||
        value.substr(dot + 1).find_first_not_of("0123456789") !=
            std::string::npos)
      return {};
    controller = std::move(value);
  }
  return controller;
}
bool validUiArguments(const std::vector<std::string> &args) {
  std::size_t option = 1;
  if (args.size() == 4 && args[1] == "-n")
    option = 2;
  else if (args.size() != 3)
    return false;
  return !args[0].empty() &&
         (args[option] == "-p" || args[option] == "--path") &&
         !args[option + 1].empty() && args[option + 1].size() <= 4096;
}
bool validManagerArguments(const std::vector<std::string> &args) {
  return args.size() == 2 && !args[0].empty() && args[1] == "--user";
}
bool GrantCandidate::submit(std::uint32_t duration, std::uint64_t generation) {
  if (submitted_ || finished_ || (duration && !validDuration(duration)))
    return false;
  submitted_ = true;
  duration_ = duration;
  generation_ = generation;
  return true;
}
bool GrantCandidate::confirm(bool authenticated, std::uint64_t generation,
                             bool package, bool warning, bool ownerAlive) {
  if (finished_)
    return false;
  finished_ = true;
  return submitted_ && duration_ && authenticated &&
         generation == generation_ && package && warning && ownerAlive;
}
bool mayReuse(const KeyDecision &d) {
  return d.marker && d.warning && d.ownerAlive && d.ancestry && d.sameUid &&
         d.sameOwner && !d.retry && (!d.expires || d.now < d.expires);
}
} // namespace emaki::sudo_channel
