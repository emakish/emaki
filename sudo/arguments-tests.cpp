// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "arguments.hpp"
#include <cassert>
#include <iostream>
using namespace emaki::sudo_channel;
int main() {
  for (const auto &args : std::vector<std::vector<std::string>>{
           {"sudo", "-S", "id"},
           {"sudo", "-kSn", "id"},
           {"sudo", "--stdin", "id"},
           {"sudo", "--std", "id"},
           {"sudo", "LANG=C", "-S", "id"},
           {"sudo", "-u", "root", "-S", "id"},
           {"sudo", "--user=root", "-S", "id"},
           {"sudo", "--preserve-env", "-S", "id"}}) {
    auto parsed = parseSudoArguments(args);
    assert(parsed.valid && parsed.standardInput && !parsed.askpass);
    assert(parsed.command == "id");
  }
  for (const auto &args : std::vector<std::vector<std::string>>{
           {"sudo", "-A", "id"},
           {"sudo", "-kAn", "id"},
           {"sudo", "--askpass", "id"},
           {"sudo", "--ask", "id"},
           {"sudo", "NAME=value", "-A", "/bin/id"}}) {
    auto parsed = parseSudoArguments(args);
    assert(parsed.valid && parsed.askpass && !parsed.standardInput);
    assert(parsed.command == "id");
  }
  for (const auto &args : std::vector<std::vector<std::string>>{
           {"sudo", "id", "-S", "-A"},
           {"sudo", "-p", "-S", "id"},
           {"sudo", "-p-A", "id"},
           {"sudo", "--prompt=-S", "id"},
           {"sudo", "--user", "-A", "id"},
           {"sudo", "--preserve-env=-S", "id"},
           {"sudo", "-h", "host", "id", "-A"},
           {"sudo", "-hAS", "id"},
           {"sudo", "-ES", "id", "-A"}}) {
    auto parsed = parseSudoArguments(args);
    assert(parsed.valid && !parsed.askpass);
    assert(parsed.standardInput == (args[1] == "-ES"));
    assert(parsed.command == "id");
  }
  // Every operand-bearing option must consume values that resemble switches.
  for (const char *option : {"-a",          "-C",           "-c",
                             "-D",          "-g",           "-p",
                             "-R",          "-r",           "-T",
                             "-t",          "-U",           "-u",
                             "--auth-type", "--close-from", "--login-class",
                             "--chdir",     "--group",      "--prompt",
                             "--chroot",    "--role",       "--command-timeout",
                             "--type",      "--other-user", "--user",
                             "--host"}) {
    for (const char *operand : {"-S", "-A"}) {
      auto parsed = parseSudoArguments({"sudo", option, operand, "id"});
      assert(parsed.valid && !parsed.standardInput && !parsed.askpass);
      assert(parsed.command == "id");
    }
  }
  for (const auto &args :
       std::vector<std::vector<std::string>>{{},
                                             {"sudo", "--unknown", "id"},
                                             {"sudo", "--s", "id"},
                                             {"sudo", "-u"},
                                             {"sudo", "--user"},
                                             {"sudo", "--stdin=yes", "id"},
                                             {"sudo", "-z", "id"}})
    assert(!parseSudoArguments(args).valid);
  auto afterEnd = parseSudoArguments({"sudo", "--", "-S", "-A"});
  assert(afterEnd.valid && !afterEnd.standardInput && !afterEnd.askpass);
  assert(afterEnd.command == "-S");
  assert(parseSudoArguments({"sudo", "--", "X=Y", "-A"}).command == "X=Y");
  assert(parseSudoArguments({"sudo", "/tmp/a=b", "-S"}).command == "a=b");
  for (const char *mode : {"-s", "-i", "-e", "-l", "-v", "--shell", "--login"})
    assert(parseSudoArguments({"sudo", mode, "id"}).command.empty());
  assert(parseSudoArguments({"sudoedit", "/tmp/file"}).command.empty());
  std::cout << "Sudo argument tests passed\n";
}
