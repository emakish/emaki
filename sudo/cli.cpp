// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <cerrno>
#include <charconv>
#include <cstdio>
#include <poll.h>
#include <string_view>
using namespace emaki::sudo_channel;
static int unavailable() {
  puts("{\"promptAvailable\":false,\"keys\":[],\"error\":\"service-"
       "unavailable\"}");
  return 1;
}
int main(int argc, char **argv) {
  Type type;
  std::uint64_t id = 0;
  std::string token;
  if (argc < 2)
    return 2;
  std::string_view action(argv[1]);
  if (action == "status" &&
      (argc == 2 || (argc == 3 && std::string_view(argv[2]) == "--json")))
    type = Type::Status;
  else if (action == "revoke-all" &&
           (argc == 2 || (argc == 3 && std::string_view(argv[2]) == "--json")))
    type = Type::RevokeAll;
  else if (action == "revoke" &&
           (argc == 3 ||
            (argc == 4 && std::string_view(argv[3]) == "--json"))) {
    type = Type::Revoke;
    std::string_view value(argv[2]);
    auto result =
        std::from_chars(value.data(), value.data() + value.size(), id);
    if (result.ec != std::errc{} || result.ptr != value.data() + value.size() ||
        !id)
      return 2;
  } else if ((action == "turn-start" || action == "turn-end" ||
              action == "turn-complete") &&
             (argc == 3 ||
              (argc == 4 && std::string_view(argv[3]) == "--json"))) {
    type = action == "turn-start" ? Type::TurnStart
           : action == "turn-end" ? Type::TurnEnd : Type::TurnComplete;
    token = argv[2];
    if (token.empty() || token.size() > 129 ||
        token.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.") !=
            std::string::npos)
      return 2;
  } else
    return 2;
  // Quota eviction can close a newly accepted peer before its first packet.
  // Only revocation is safe to repeat if the response was lost after handling.
  const bool revocation = type == Type::Revoke || type == Type::RevokeAll;
  for (int attempt = 0; attempt < (revocation ? 2 : 1); ++attempt) {
    auto socket = connectBroker();
    if (!socket)
      return unavailable();
    if (!sendPacket(socket.get(), type, id, 0, token.data(), token.size())) {
      if (errno == EPIPE || errno == ECONNRESET)
        continue;
      return unavailable();
    }
    pollfd fd{socket.get(), POLLIN, 0};
    if (poll(&fd, 1, 5000) != 1)
      return unavailable();
    Packet packet;
    if (!receivePacket(socket.get(), packet)) {
      if (errno == ECONNRESET)
        continue;
      return unavailable();
    }
    if (packet.header.type != Type::Response)
      return unavailable();
    fwrite(packet.body.data(), 1, packet.body.size(), stdout);
    fputc('\n', stdout);
    return 0;
  }
  return unavailable();
}
