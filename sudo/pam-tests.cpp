// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Test-only dependencies, linked only into the fixture module.
#include "core.hpp"
#include <cstring>
#include <security/pam_modules.h>
#include <sys/socket.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
namespace {
int mode, tty, connections, verified, rejected;
Fd peer;
} // namespace
extern "C" void emaki_test_setup(int requested, int terminal) {
  peer.reset();
  mode = requested;
  tty = terminal;
  connections = verified = rejected = 0;
}
extern "C" void emaki_test_retry(int requested) { mode = requested; }
extern "C" int emaki_test_rejected() { return rejected; }
extern "C" int emaki_test_tty() { return tty; }
extern "C" int emaki_test_timeout() { return 30; }
extern "C" int emaki_test_connections() { return connections; }
extern "C" int emaki_test_verified() { return verified; }
extern "C" int emaki_test_next() {
  Packet p;
  if (!peer || !receivePacket(peer.get(), p))
    return 0;
  return static_cast<int>(p.header.type);
}
extern "C" void emaki_test_deny() {
  sendPacket(peer.get(), Type::Denied, 42);
  peer.reset();
}
extern "C" void emaki_test_ack() { sendPacket(peer.get(), Type::Response, 42); }
extern "C" int emaki_test_connect() {
  ++connections;
  if (mode == 0)
    return -1;
  int sockets[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets))
    return -1;
  if (peer) {
    Packet previous;
    if (receivePacket(peer.get(), previous) &&
        previous.header.type == Type::Rejected)
      ++rejected;
  }
  peer.reset(sockets[1]);
  if (mode == 1 || mode == 2 || mode == 9) {
    sendPacket(peer.get(), Type::Waiting, 42);
    const char *password = mode == 2 ? "wrong" : "correct";
    sendPacket(peer.get(), Type::Password, 42, 0, password, strlen(password));
  } else if (mode == 3) {
    sendPacket(peer.get(), Type::Waiting, 42);
    sendPacket(peer.get(), Type::Denied, 42);
  } else if (mode == 4) {
    sendPacket(peer.get(), Type::Granted, 42);
  } else if (mode == 6) {
    sendPacket(peer.get(), Type::Waiting, 42);
  } else if (mode == 7) {
    sendPacket(peer.get(), Type::Fallback);
  } else if (mode == 8) {
    sendPacket(peer.get(), Type::Waiting, 42);
    sendPacket(peer.get(), Type::Password, 43, 0, "correct", 7);
  }
  return sockets[0];
}
extern "C" int emaki_test_verify(pam_handle_t *pamh) {
  ++verified;
  const void *token = nullptr;
  if (pam_get_item(pamh, PAM_AUTHTOK, &token) != PAM_SUCCESS || !token)
    return PAM_AUTH_ERR;
  return strcmp(static_cast<const char *>(token), "correct") == 0
             ? PAM_SUCCESS
             : PAM_AUTH_ERR;
}
