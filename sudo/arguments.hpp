// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <string>
#include <string_view>
#include <vector>
namespace emaki::sudo_channel {
struct SudoArguments {
  bool valid = false, standardInput = false, askpass = false;
  std::string command;
};
// Inspect sudo's original argument boundaries, never a reconstructed shell
// line. Unknown syntax leaves the ordinary PAM conversation in control. This
// parser identifies option boundaries; sudo remains responsible for option
// policy.
inline SudoArguments parseSudoArguments(const std::vector<std::string> &args) {
  SudoArguments result;
  if (args.empty() || args.front().empty())
    return result;
  auto basename = [](const std::string &value) {
    return value.substr(value.find_last_of('/') + 1);
  };
  auto assignment = [](const std::string &value) {
    return !value.empty() && value.front() != '/' && value.front() != '=' &&
           value.find('=') != std::string::npos;
  };
  bool indirect = basename(args.front()) == "sudoedit";
  auto flag = [&](char option) {
    if (option == 'S')
      result.standardInput = true;
    if (option == 'A')
      result.askpass = true;
    if (std::string_view("ehiKlsvV").find(option) != std::string_view::npos)
      indirect = true;
  };
  struct Option {
    std::string_view name;
    char letter;
    int operand;
  };
  constexpr Option options[] = {{"background", 'b', 0},
                                {"preserve-env", 'E', 2},
                                {"edit", 'e', 0},
                                {"set-home", 'H', 0},
                                {"login", 'i', 0},
                                {"remove-timestamp", 'K', 0},
                                {"list", 'l', 0},
                                {"preserve-groups", 'P', 0},
                                {"shell", 's', 0},
                                {"other-user", 'U', 1},
                                {"validate", 'v', 0},
                                {"askpass", 'A', 0},
                                {"auth-type", 'a', 1},
                                {"bell", 'B', 0},
                                {"close-from", 'C', 1},
                                {"login-class", 'c', 1},
                                {"chdir", 'D', 1},
                                {"group", 'g', 1},
                                {"help", 'h', 0},
                                {"host", 'o', 1},
                                {"reset-timestamp", 'k', 0},
                                {"no-update", 'N', 0},
                                {"non-interactive", 'n', 0},
                                {"prompt", 'p', 1},
                                {"chroot", 'R', 1},
                                {"role", 'r', 1},
                                {"stdin", 'S', 0},
                                {"command-timeout", 'T', 1},
                                {"type", 't', 1},
                                {"user", 'u', 1},
                                {"version", 'V', 0}};
  std::size_t index = 1;
  for (; index < args.size(); ++index) {
    const auto &arg = args[index];
    if (arg == "--") {
      ++index;
      break;
    }
    if (arg.size() > 2 && arg.starts_with("--")) {
      auto equal = arg.find('=');
      std::string_view name(arg.data() + 2,
                            (equal == std::string::npos ? arg.size() : equal) -
                                2);
      const Option *match = nullptr;
      for (const auto &option : options) {
        if (option.name == name) {
          match = &option;
          break;
        }
        if (!name.empty() && option.name.starts_with(name)) {
          if (match)
            return {};
          match = &option;
        }
      }
      if (!match || (equal != std::string::npos && !match->operand))
        return {};
      if (match->operand == 1 && equal == std::string::npos &&
          ++index == args.size())
        return {};
      flag(match->letter);
    } else if (arg.size() > 1 && arg.front() == '-') {
      for (std::size_t offset = 1; offset < arg.size(); ++offset) {
        char option = arg[offset];
        if (std::string_view("aCcDgpRrTtUu").find(option) !=
            std::string_view::npos) {
          if (offset + 1 == arg.size() && ++index == args.size())
            return {};
          break;
        }
        if (option == 'h') {
          // -hHOST consumes the rest, while bare -h consumes a following
          // non-option host only. A grouped -h without an operand means help.
          if (offset + 1 == arg.size()) {
            if (arg == "-h" && index + 1 < args.size() &&
                !args[index + 1].starts_with('-') &&
                !assignment(args[index + 1]))
              ++index;
            else
              indirect = true;
          }
          break;
        }
        if (std::string_view("ABbEeHiKklNnPSsvV").find(option) ==
            std::string_view::npos)
          return {};
        flag(option);
      }
    } else if (!assignment(arg)) {
      break;
    }
  }
  result.valid = true;
  if (!indirect && index < args.size())
    result.command = basename(args[index]);
  return result;
}
} // namespace emaki::sudo_channel
