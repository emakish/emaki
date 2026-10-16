// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstddef>
#include <cstdint>

namespace emaki::sudo_channel {
// Local, native-endian SOCK_SEQPACKET protocol. Exactly one frame per packet.
// Both ends must check SO_PEERCRED before exchanging any frame. Never put a
// password into a JSON document, argv, environment, a file, or diagnostic
// output.
inline constexpr std::uint32_t Magic = 0x454D5355;
inline constexpr std::uint16_t Version = 3;
inline constexpr std::size_t MaxBody = 16384;
inline constexpr std::size_t MaxPassword = 1024;
inline constexpr std::uint32_t Session = UINT32_MAX;
inline constexpr std::uint32_t TurnsFlag = 0x80000000;
// Typed amounts are bounded before any grant is created. Time uses whole
// minutes on the wire as seconds; turn grants retain their independent timeout.
inline constexpr std::uint32_t MinMinutes = 5;
inline constexpr std::uint32_t MaxMinutes = 24 * 60;
inline constexpr std::uint32_t MinTurns = 1;
inline constexpr std::uint32_t MaxTurns = 1000;
inline constexpr std::uint32_t TurnHardCap = 4 * 60 * 60;
constexpr bool validTimeDuration(std::uint32_t seconds) {
  return seconds >= MinMinutes * 60 && seconds <= MaxMinutes * 60 &&
         seconds % 60 == 0;
}
constexpr std::uint32_t turnCount(std::uint32_t option) {
  const auto count = option & ~TurnsFlag;
  return (option & TurnsFlag) && count >= MinTurns && count <= MaxTurns
             ? count
             : 0;
}
enum class Type : std::uint16_t {
  HelloUi = 1,
  Authenticate = 2,
  Submit = 3,
  Cancel = 4,
  Lock = 5,
  Status = 6,
  Revoke = 7,
  RevokeAll = 8,
  AcknowledgeWarning = 9,
  Unlock = 10,
  Authenticated = 11,
  Rejected = 12,
  TurnStart = 13,
  TurnEnd = 14,
  TurnComplete = 15,
  Request = 101,
  Finished = 102,
  Keys = 103,
  Response = 104,
  Password = 105,
  Error = 106,
  Granted = 107,
  Fallback = 108,
  Denied = 109,
  Waiting = 110,
};
struct Header {
  std::uint32_t magic = Magic;
  std::uint16_t version = Version;
  Type type{};
  std::uint64_t request = 0;
  std::uint32_t option = 0;
  std::uint32_t size = 0;
  std::uint64_t reserved = 0;
};
static_assert(sizeof(Header) == 32);
// Authenticate: PAM username body (1..255 bytes), zero request and option.
// Authenticated/Rejected: request from Password, empty body, zero option.
// Authenticated receives empty Response after approval or Denied on
// cancellation, with the matching request id. PAM waits boundedly for that
// acknowledgement before leaving its account phase. Password: bounded raw
// bytes, transient, delivered only to pinned root sudo.
// Granted/Fallback/Denied: empty. Fallback permits the ordinary PAM stack;
// Denied records cancellation, lock or prompt timeout as authentication
// failure. UI methods retain the v1 field layout. Submit carries a raw password
// and option 0 (no key), whole minutes in MinMinutes..MaxMinutes encoded as
// seconds, Session, or TurnsFlag | count in MinTurns..MaxTurns. No grant exists before
// Authenticated. Finished: request id, option 0=unavailable, 1=success,
// 2=failure. Request/Keys/Response contain public JSON only. Grants contain no
// secrets.
// TurnStart/TurnEnd: a bounded opaque session token. TurnComplete: session:turn
// tokens. Zero request/option, no claimed PID. The service derives and pins the
// reporting process tree from kernel peer credentials. Reports never grant.
inline constexpr const char *SocketDirectory = "/run/emaki-sudo";
inline constexpr const char *SocketPath = "/run/emaki-sudo/broker.sock";
} // namespace emaki::sudo_channel
