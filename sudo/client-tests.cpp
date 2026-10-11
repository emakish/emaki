// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include "ui.hpp"
#include <QCoreApplication>
#include <cassert>
#include <cerrno>
#include <fcntl.h>
#include <iostream>
#include <sys/socket.h>
#include <unistd.h>

#define main sudoCommandMain
#include "cli.cpp"
#undef main

using namespace emaki::sudo_channel;
namespace {
int connections = 0, packets = 0, lastDescriptor = -1;
Type expected = Type::RevokeAll;
std::uint64_t expectedId = 0;
bool available = true;
enum class Failure { None, BeforeSend, AfterSend, Malformed, WouldBlock };
Failure failure = Failure::None, activeFailure = Failure::None;
int failuresRemaining = 0;
Fd peer;
std::string expectedToken;
}
extern "C" bool realSend(int, Type, std::uint64_t, std::uint32_t,
                          const void *, std::size_t)
    asm("__real__ZN5emaki12sudo_channel10sendPacketEiNS0_4TypeEmjPKvm");
extern "C" bool realReceive(int, Packet &)
    asm("__real__ZN5emaki12sudo_channel13receivePacketEiRNS0_6PacketE");
// Wrap only the trusted transport boundary. Production socket authentication is
// unchanged; these tests prove client behavior without a host broker or shell.
extern "C" Fd wrappedConnect()
    asm("__wrap__ZN5emaki12sudo_channel13connectBrokerEv");
extern "C" Fd wrappedConnect() {
  ++connections;
  if (!available)
    return {};
  int sockets[2];
  assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK,
                    0, sockets) == 0);
  peer.reset(sockets[1]);
  activeFailure = failuresRemaining ? failure : Failure::None;
  if (failuresRemaining)
    --failuresRemaining;
  if (activeFailure == Failure::BeforeSend)
    peer.reset();
  lastDescriptor = sockets[0];
  return Fd(lastDescriptor);
}
extern "C" bool wrappedSend(int, Type, std::uint64_t, std::uint32_t,
                            const void *, std::size_t)
    asm("__wrap__ZN5emaki12sudo_channel10sendPacketEiNS0_4TypeEmjPKvm");
extern "C" bool wrappedSend(int fd, Type type, std::uint64_t id,
                            std::uint32_t option, const void *data, std::size_t size) {
  assert(fd == lastDescriptor && type == expected && id == expectedId);
  assert(option == 0 && size == expectedToken.size());
  assert(!size || std::string_view(static_cast<const char *>(data), size) == expectedToken);
  ++packets;
  if (activeFailure == Failure::WouldBlock) {
    errno = EAGAIN;
    return false;
  }
  if (!realSend(fd, type, id, option, data, size))
    return false;
  Packet request;
  assert(realReceive(peer.get(), request));
  assert(request.header.type == expected && request.header.request == expectedId);
  if (activeFailure == Failure::AfterSend) {
    // The request was handled, but the client never received its response.
    peer.reset();
    return true;
  }
  if (activeFailure == Failure::Malformed) {
    Header malformed;
    malformed.magic = 0;
    assert(send(peer.get(), &malformed, sizeof(malformed), MSG_NOSIGNAL) == sizeof(malformed));
    peer.reset();
    return true;
  }
  constexpr char response[] = "{\"keys\":[]}";
  assert(realSend(peer.get(), Type::Response, 0, 0, response, sizeof(response) - 1));
  return true;
}
extern "C" bool wrappedReceive(int, Packet &)
    asm("__wrap__ZN5emaki12sudo_channel13receivePacketEiRNS0_6PacketE");
extern "C" bool wrappedReceive(int fd, Packet &packet) {
  assert(fd == lastDescriptor);
  return realReceive(fd, packet);
}
void closed() {
  errno = 0;
  assert(fcntl(lastDescriptor, F_GETFD) == -1 && errno == EBADF);
}
int main(int argc, char **argv) {
  QCoreApplication application(argc, argv);
  SudoClient client;
  assert(!client.enabled() && !client.connected());
  expected = Type::Revoke;
  expectedId = 42;
  client.revoke(QStringLiteral("42"));
  assert(connections == 1 && packets == 1);
  closed();
  for (const auto *id : {"", "0", "bad", "18446744073709551616"})
    client.revoke(QString::fromLatin1(id));
  assert(connections == 1 && packets == 1);
  expected = Type::RevokeAll;
  expectedId = 0;
  client.revokeAll();
  assert(connections == 2 && packets == 2);
  closed();
  // A failed connection remains a closed, unregistered client, and the next
  // revocation retries independently of provider enablement or its timer.
  available = false;
  client.revokeAll();
  assert(connections == 3 && packets == 2 && !client.connected());
  available = true;
  client.revokeAll();
  assert(connections == 4 && packets == 3);
  closed();
  char executable[] = "emaki-sudo", action[] = "revoke-all", json[] = "--json";
  char *command[] = {executable, action, json, nullptr};
  assert(sudoCommandMain(3, command) == 0);
  assert(connections == 5 && packets == 4);
  closed();
  assert(!client.connected());
  auto run = [&](const char *verb, Type type, const char *argument,
                 Failure fault, int count, int result, int attempts) {
    expected = type;
    expectedId = type == Type::Revoke ? 42 : 0;
    expectedToken = type == Type::TurnStart || type == Type::TurnEnd ||
                            type == Type::TurnComplete ? argument : "";
    failure = fault;
    failuresRemaining = count;
    const int before = connections;
    char *args[] = {executable, const_cast<char *>(verb),
                    const_cast<char *>(argument), nullptr};
    assert(sudoCommandMain(argument ? 3 : 2, args) == result);
    assert(connections == before + attempts);
    closed();
  };
  for (const auto fault : {Failure::BeforeSend, Failure::AfterSend}) {
    run("revoke-all", Type::RevokeAll, nullptr, fault, 1, 0, 2);
    run("revoke", Type::Revoke, "42", fault, 1, 0, 2);
    run("revoke-all", Type::RevokeAll, nullptr, fault, 2, 1, 2);
    run("revoke", Type::Revoke, "42", fault, 2, 1, 2);
    run("status", Type::Status, nullptr, fault, 1, 1, 1);
    run("turn-start", Type::TurnStart, "turn-token", fault, 1, 1, 1);
    run("turn-end", Type::TurnEnd, "turn-token", fault, 1, 1, 1);
    run("turn-complete", Type::TurnComplete, "turn-token", fault, 1, 1, 1);
  }
  for (const auto fault : {Failure::Malformed, Failure::WouldBlock}) {
    run("revoke-all", Type::RevokeAll, nullptr, fault, 1, 1, 1);
    run("revoke", Type::Revoke, "42", fault, 1, 1, 1);
  }
  std::cout << "CLI revocation retries once after eviction or response loss only\n";
  std::cout << "Disconnected UI and standalone CLI revocation tests passed\n";
}
