// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "arguments.hpp"
#include "core.hpp"
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <memory>
#include <poll.h>
#include <security/pam_modules.h>
#include <unistd.h>

namespace {
using namespace emaki::sudo_channel;
constexpr const char *PendingName = "emaki-sudo-pending";
struct Pending {
  Fd socket;
  std::uint64_t request;
  ~Pending() {
    if (socket)
      sendPacket(socket.get(), Type::Rejected, request);
  }
};
void cleanup(pam_handle_t *, void *data, int) {
  delete static_cast<Pending *>(data);
}
#ifdef EMAKI_PAM_TEST
extern "C" int emaki_test_tty();
extern "C" int emaki_test_connect();
extern "C" int emaki_test_timeout();
extern "C" int emaki_test_verify(pam_handle_t *);
#endif
bool terminal(pam_handle_t *pamh) {
#ifdef EMAKI_PAM_TEST
  (void)pamh;
  return emaki_test_tty();
#else
  Fd tty(open("/dev/tty", O_RDONLY | O_NOCTTY | O_NONBLOCK | O_CLOEXEC));
  if (tty && isatty(tty.get()))
    return true;
  const void *item = nullptr;
  if (pam_get_item(pamh, PAM_TTY, &item) == PAM_SUCCESS && item) {
    const char *path = static_cast<const char *>(item);
    if (strncmp(path, "/dev/", 5) == 0) {
      Fd reported(open(path, O_RDONLY | O_NOCTTY | O_NONBLOCK | O_CLOEXEC));
      if (reported && isatty(reported.get()))
        return true;
    }
  }
  return false;
#endif
}
bool waitPacket(int fd, Packet &packet, int milliseconds) {
#ifdef EMAKI_PAM_TEST
  milliseconds = emaki_test_timeout();
#endif
  auto deadline = std::chrono::steady_clock::now() +
                  std::chrono::milliseconds(milliseconds);
  for (;;) {
    auto left = std::chrono::duration_cast<std::chrono::milliseconds>(
                    deadline - std::chrono::steady_clock::now())
                    .count();
    if (left <= 0)
      return false;
    pollfd event{fd, POLLIN, 0};
    int result = poll(&event, 1, static_cast<int>(left));
    if (result < 0 && errno == EINTR)
      continue;
    return result > 0 && (event.revents & POLLIN) && receivePacket(fd, packet);
  }
}
bool ordinaryConversation() {
  // sudo may sanitize its live environment before PAM. Its original environment
  // still records an implicit askpass request. This caller-controlled hint only
  // selects the standard stack; it cannot authorize authentication.
  auto environment = readBounded("/proc/self/environ", 1024 * 1024);
  if (environment.empty() || environment.back() != '\0')
    return true;
  constexpr std::string_view askpass = "SUDO_ASKPASS=";
  for (std::size_t begin = 0; begin < environment.size();) {
    auto end = environment.find('\0', begin);
    std::string_view entry(environment.data() + begin, end - begin);
    if (entry.starts_with(askpass) && entry.size() > askpass.size())
      return true;
    begin = end + 1;
  }
  // Read this process's kernel argument view, not caller-controlled environment
  // metadata. Preserve NUL boundaries and reject truncated or oversized input.
  auto commandLine = readBounded("/proc/self/cmdline", 1024 * 1024);
  if (commandLine.empty() || commandLine.back() != '\0')
    return true;
  std::vector<std::string> arguments;
  for (std::size_t begin = 0; begin < commandLine.size();) {
    auto end = commandLine.find('\0', begin);
    arguments.emplace_back(commandLine.substr(begin, end - begin));
    begin = end + 1;
  }
  const auto parsed = parseSudoArguments(arguments);
  return !parsed.valid || parsed.standardInput || parsed.askpass;
}
int authenticate(pam_handle_t *pamh) {
  // Repeated pam_authenticate calls must never retain a previous candidate.
  if (pam_set_data(pamh, PendingName, nullptr, cleanup) != PAM_SUCCESS)
    return PAM_IGNORE;
  if (terminal(pamh) || ordinaryConversation())
    return PAM_IGNORE;
  const void *item = nullptr;
  if (pam_get_item(pamh, PAM_USER, &item) != PAM_SUCCESS || !item)
    return PAM_IGNORE;
  const char *user = static_cast<const char *>(item);
  std::size_t length = strnlen(user, 256);
  if (!length || length > 255 || strcmp(user, "root") == 0)
    return PAM_IGNORE;
#ifdef EMAKI_PAM_TEST
  Fd socket(emaki_test_connect());
#else
  Fd socket = connectBroker();
#endif
  if (!socket ||
      !sendPacket(socket.get(), Type::Authenticate, 0, 0, user, length))
    return PAM_IGNORE;
  Packet packet;
  if (!waitPacket(socket.get(), packet, 1000))
    return PAM_IGNORE;
  std::uint64_t request = packet.header.request;
  if (packet.header.type == Type::Waiting) {
    if (!request || packet.body.size() || packet.header.option)
      return PAM_IGNORE;
    if (!waitPacket(socket.get(), packet, 125000))
      return PAM_IGNORE;
    if (packet.header.request != request)
      return PAM_IGNORE;
  }
  if (packet.header.option)
    return PAM_IGNORE;
  if (packet.header.type == Type::Denied && !packet.body.size())
    return PAM_ABORT;
  if (packet.header.type == Type::Granted && !packet.body.size())
    return PAM_SUCCESS;
  if (packet.header.type != Type::Password || !packet.header.request ||
      !validPassword(packet.body))
    return PAM_IGNORE;
  // PAM owns its token copy. Neither pending state nor the service retains it.
  Secret token(MaxPassword + 1);
  if (!token.valid() || !token.assign(packet.body.data(), packet.body.size()))
    return PAM_IGNORE;
  token.data()[token.size()] = 0;
  auto pending = std::make_unique<Pending>();
  pending->socket = std::move(socket);
  pending->request = packet.header.request;
  if (pam_set_item(pamh, PAM_AUTHTOK, token.data()) != PAM_SUCCESS)
    return PAM_IGNORE;
  packet.body.wipe();
  token.wipe();
  if (pam_set_data(pamh, PendingName, pending.get(), cleanup) != PAM_SUCCESS) {
    pam_set_item(pamh, PAM_AUTHTOK, nullptr);
    return PAM_IGNORE;
  }
  pending.release();
  return PAM_IGNORE;
}
} // namespace
extern "C" int pam_sm_authenticate(pam_handle_t *pamh, int, int argc,
                                   const char **argv) {
#ifdef EMAKI_PAM_TEST
  if (argc == 1 && strcmp(argv[0], "verify") == 0)
    return emaki_test_verify(pamh);
#else
  (void)argc;
  (void)argv;
#endif
  try {
    return authenticate(pamh);
  } catch (...) {
    return PAM_IGNORE;
  }
}
extern "C" int pam_sm_setcred(pam_handle_t *, int, int, const char **) {
  return PAM_IGNORE;
}
extern "C" int pam_sm_acct_mgmt(pam_handle_t *pamh, int, int, const char **) {
  const void *data = nullptr;
  if (pam_get_data(pamh, PendingName, &data) == PAM_SUCCESS && data) {
    auto *pending = const_cast<Pending *>(static_cast<const Pending *>(data));
    // sudo reaches account management only after pam_authenticate succeeds.
    // Account policy still runs for every invocation, including existing
    // grants.
    // Keep sudo in this callback until the service has checked its pinned
    // identity and recorded the outcome; a short command could otherwise exit
    // before the service consumes the confirmation.
    Packet response;
    sendPacket(pending->socket.get(), Type::Authenticated, pending->request);
    // A cancellation may already be queued even when sending finds a closed
    // peer. Consume it before deciding whether infrastructure is unavailable.
    bool received = waitPacket(pending->socket.get(), response, 1000);
    bool matched = received && response.header.request == pending->request &&
                   !response.header.option && !response.body.size();
    bool denied = matched && response.header.type == Type::Denied;
    if (matched && (response.header.type == Type::Response || denied))
      pending->socket.reset();
    // Missing acknowledgement affects only the optional grant; an explicit
    // cancellation still prevents this command after password verification.
    pam_set_data(pamh, PendingName, nullptr, cleanup);
    if (denied)
      return PAM_PERM_DENIED;
  }
  return PAM_IGNORE;
}
