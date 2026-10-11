// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include "protocol.hpp"
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <security/pam_appl.h>
#include <sys/wait.h>
#include <unistd.h>
using namespace emaki::sudo_channel;
// Emulate sudo's root IDs inside this test process without changing OS
// identity. This catches accidental process-UID checks in the loaded fixture
// module.
extern "C" uid_t getuid() noexcept { return 0; }
extern "C" uid_t geteuid() noexcept { return 0; }
// Some test sandboxes prohibit audit netlink sockets. Keep real PAM dispatch,
// but emulate a kernel without audit in that case; production has no such shim.
extern "C" int audit_open() {
  auto real = reinterpret_cast<int (*)()>(dlsym(RTLD_NEXT, "audit_open"));
  assert(real);
  int result = real();
  if (result < 0 && errno == EPERM) {
    static bool reported = false;
    if (!reported) {
      std::cout << "SKIP kernel audit transport: sandbox denies audit socket\n";
      reported = true;
    }
    errno = EAFNOSUPPORT;
  }
  return result;
}
int conversation(int, const pam_message **, pam_response **, void *) {
  return PAM_CONV_ERR;
}
int main(int argc, char **argv) {
  const char *argumentModule = getenv("EMAKI_TEST_ARGUMENT_MODULE");
  const char *argumentStack = getenv("EMAKI_TEST_ARGUMENT_STACK");
  const char *argumentInstalled = getenv("EMAKI_TEST_ARGUMENT_INSTALLED");
  assert(argumentModule || argc == 4);
  std::string module =
      std::filesystem::absolute(argumentModule ? argumentModule : argv[1]);
  std::ifstream templateFile(argumentStack ? argumentStack : argv[2]);
  std::string stack((std::istreambuf_iterator<char>(templateFile)), {});
  const std::string installed = argumentInstalled ? argumentInstalled : argv[3];
  assert(!stack.empty() && !installed.empty());
  auto render = [&](const std::string &path) {
    auto result = stack;
    std::size_t count = 0, offset = 0;
    while ((offset = result.find(installed, offset)) != std::string::npos) {
      result.replace(offset, installed.size(), path);
      offset += path.size();
      ++count;
    }
    assert(count == 2);
    return result;
  };
  void *handle = dlopen(module.c_str(), RTLD_NOW | RTLD_GLOBAL);
  if (!handle) {
    std::cerr << dlerror() << '\n';
    return 1;
  }
  auto setup =
      reinterpret_cast<void (*)(int, int)>(dlsym(handle, "emaki_test_setup"));
  auto connections =
      reinterpret_cast<int (*)()>(dlsym(handle, "emaki_test_connections"));
  auto verified =
      reinterpret_cast<int (*)()>(dlsym(handle, "emaki_test_verified"));
  auto next = reinterpret_cast<int (*)()>(dlsym(handle, "emaki_test_next"));
  auto retry =
      reinterpret_cast<void (*)(int)>(dlsym(handle, "emaki_test_retry"));
  auto rejected =
      reinterpret_cast<int (*)()>(dlsym(handle, "emaki_test_rejected"));
  auto ack = reinterpret_cast<void (*)()>(dlsym(handle, "emaki_test_ack"));
  auto deny = reinterpret_cast<void (*)()>(dlsym(handle, "emaki_test_deny"));
  assert(setup && connections && verified && next && retry && rejected && ack &&
         deny);
  char directory[] = "/tmp/emaki-pam-test-XXXXXX";
  assert(mkdtemp(directory));
  auto write = [&](const char *name, const std::string &body) {
    std::ofstream file(std::string(directory) + "/" + name);
    file << body;
    assert(file.good());
  };
  // Every transaction uses the rendered shipped service. Only its module
  // location and the included distribution stack vary in this private confdir.
  write("verify", render(module));
  write("fallback", render(module));
  write("missing", render(std::string(directory) + "/missing.so"));
  auto stock = [&](bool verify) {
    write("sudo", std::string("auth required ") +
                      (verify ? module + " verify" : "pam_permit.so") +
                      "\naccount required pam_permit.so\n");
  };
  pam_conv conv{conversation, nullptr};
  auto start = [&](const char *service, const char *user = "test-user") {
    stock(std::string(service) == "verify");
    pam_handle_t *pamh = nullptr;
    assert(pam_start_confdir(service, user, &conv, directory, &pamh) ==
           PAM_SUCCESS);
    return pamh;
  };
  if (argumentModule) {
    // Simulate sudo clearing its live environment before entering PAM. The module
    // must inspect the original environment recorded at exec time.
    assert(unsetenv("SUDO_ASKPASS") == 0);
    // A broker cancellation would fail if transport were contacted.
    setup(3, 0);
    auto pamh = start("fallback");
    bool contact = getenv("EMAKI_TEST_ARGUMENT_CONTACT") != nullptr;
    int expected = contact ? PAM_ABORT : PAM_SUCCESS;
    assert(pam_authenticate(pamh, 0) == expected);
    assert(connections() == (contact ? 1 : 0));
    pam_end(pamh, expected);
    if (!contact) {
      pamh = start("verify");
      assert(pam_authenticate(pamh, 0) == PAM_AUTH_ERR);
      assert(connections() == 0 && verified() == 1);
      pam_end(pamh, PAM_AUTH_ERR);
    }
    std::filesystem::remove_all(directory);
    return 0;
  }
  // Exercise the actual /proc/self/cmdline read, before any broker I/O.
  const std::vector<std::vector<std::string>> argumentCases = {
      {"-S", "id"},
      {"-A", "id"},
      {"-kS", "id"},
      {"-kA", "id"},
      {"--stdin", "id"},
      {"--askpass", "id"},
      {"NAME=value", "-S", "id"},
      {"id", "-S"},
      {"id", "-A"},
      {"-p", "-S", "id"},
      {"--prompt=-A", "id"},
      {"--", "-S"}};
  auto runChild = [&](const std::vector<std::string> &arguments, bool contact,
                      const char *askpass) {
    pid_t child = fork();
    assert(child >= 0);
    if (!child) {
      assert(setenv("EMAKI_TEST_ARGUMENT_MODULE", module.c_str(), 1) == 0);
      assert(setenv("EMAKI_TEST_ARGUMENT_STACK", argv[2], 1) == 0);
      assert(setenv("EMAKI_TEST_ARGUMENT_INSTALLED", argv[3], 1) == 0);
      assert(unsetenv("SUDO_ASKPASS") == 0);
      assert(unsetenv("EMAKI_TEST_ARGUMENT_CONTACT") == 0);
      if (askpass)
        assert(setenv("SUDO_ASKPASS", askpass, 1) == 0);
      // Neither an environment value nor a longer name is the requested key.
      assert(setenv("EMAKI_TEST_ENV_VALUE", "SUDO_ASKPASS=/unused", 1) == 0);
      assert(setenv("SUDO_ASKPASS_SUFFIX", "/unused", 1) == 0);
      if (contact)
        assert(setenv("EMAKI_TEST_ARGUMENT_CONTACT", "1", 1) == 0);
      std::vector<char *> childArgs{argv[0]};
      for (const auto &argument : arguments)
        childArgs.push_back(const_cast<char *>(argument.c_str()));
      childArgs.push_back(nullptr);
      execv(argv[0], childArgs.data());
      _exit(127);
    }
    int status = 0;
    assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
  };
  for (std::size_t index = 0; index < argumentCases.size(); ++index)
    runChild(argumentCases[index], index >= 7, nullptr);
  // No explicit -A: a configured helper leaves both stock success and stock
  // failure in charge, whereas an absent or empty helper still reaches the card.
  runChild({"id"}, false, "/unused askpass helper");
  runChild({"id"}, true, "");
  runChild({"id"}, true, nullptr);
  // No module, no service, slow service, explicit fallback: stock stack runs.
  for (int mode : {0, 5, 6, 7, 8}) {
    setup(mode, 0);
    auto pamh = start("fallback");
    auto then = std::chrono::steady_clock::now();
    int result = pam_authenticate(pamh, 0);
    if (result != PAM_SUCCESS)
      std::cerr << "fallback mode " << mode << ": "
                << pam_strerror(pamh, result) << " code " << result << "\n";
    assert(result == PAM_SUCCESS);
    assert(std::chrono::steady_clock::now() - then < std::chrono::seconds(1));
    assert(connections() == 1);
    pam_end(pamh, PAM_SUCCESS);
  }
  setup(0, 0);
  auto pamh = start("missing");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  pam_end(pamh, PAM_SUCCESS);
  // A terminal never contacts the service, even if it would grant.
  setup(4, 1);
  pamh = start("fallback");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(connections() == 0);
  pam_end(pamh, PAM_SUCCESS);
  // PAM_USER names the authentication account, independently of sudo's IDs.
  setup(4, 0);
  pamh = start("fallback", "root");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(connections() == 0);
  pam_end(pamh, PAM_SUCCESS);
  // Emulated root process IDs must not suppress a nonroot PAM_USER request.
  assert(getuid() == 0 && geteuid() == 0);
  setup(4, 0);
  pamh = start("verify", "test-user");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(connections() == 1 && verified() == 0);
  pam_end(pamh, PAM_SUCCESS);
  // Cancellation cannot reach even a permissive stock stack.
  setup(3, 0);
  pamh = start("fallback");
  assert(pam_authenticate(pamh, 0) == PAM_ABORT);
  pam_end(pamh, PAM_ABORT);
  // Fresh tokens must be checked by the ordinary authentication module.
  for (int mode : {1, 2, 4}) {
    setup(mode, 0);
    pamh = start("verify");
    int result = pam_authenticate(pamh, 0);
    assert(result == (mode == 2 ? PAM_AUTH_ERR : PAM_SUCCESS));
    assert(verified() == (mode == 4 ? 0 : 1));
    assert(next() == static_cast<int>(Type::Authenticate));
    assert(next() == 0); // No grant commit before successful authentication.
    if (result == PAM_SUCCESS) {
      if (mode == 1)
        ack();
      assert(pam_acct_mgmt(pamh, 0) == PAM_SUCCESS);
      assert(next() == (mode == 1 ? static_cast<int>(Type::Authenticated) : 0));
      assert(next() ==
             0); // A valid acknowledgement avoids candidate rejection.
    }
    pam_end(pamh, result);
    if (mode == 2)
      assert(next() == static_cast<int>(Type::Rejected));
  }
  // Cancellation after token delivery must still stop the command, even if
  // the service closed before the account callback could send confirmation.
  setup(1, 0);
  pamh = start("verify");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(next() == static_cast<int>(Type::Authenticate));
  deny();
  assert(pam_acct_mgmt(pamh, 0) == PAM_PERM_DENIED);
  pam_end(pamh, PAM_PERM_DENIED);
  // A missing acknowledgement is bounded and does not fail account policy.
  setup(9, 0);
  pamh = start("verify");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(next() == static_cast<int>(Type::Authenticate));
  auto beforeAccount = std::chrono::steady_clock::now();
  assert(pam_acct_mgmt(pamh, 0) == PAM_SUCCESS);
  auto accountWait = std::chrono::steady_clock::now() - beforeAccount;
  assert(accountWait >= std::chrono::milliseconds(20));
  assert(accountWait < std::chrono::seconds(1));
  assert(next() == static_cast<int>(Type::Authenticated));
  assert(next() == static_cast<int>(Type::Rejected));
  pam_end(pamh, PAM_SUCCESS);
  // Successful token without account management never commits a grant.
  setup(1, 0);
  pamh = start("verify");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(next() == static_cast<int>(Type::Authenticate));
  pam_end(pamh, PAM_SUCCESS);
  assert(next() == static_cast<int>(Type::Rejected));
  // A retry abandons the old pending candidate before accepting a new token.
  setup(1, 0);
  pamh = start("verify");
  assert(pam_authenticate(pamh, 0) == PAM_SUCCESS);
  assert(next() == static_cast<int>(Type::Authenticate));
  retry(2);
  assert(pam_authenticate(pamh, 0) == PAM_AUTH_ERR);
  assert(rejected() == 1);
  assert(next() == static_cast<int>(Type::Authenticate));
  assert(next() == 0);
  pam_end(pamh, PAM_AUTH_ERR);
  assert(next() == static_cast<int>(Type::Rejected));
  std::filesystem::remove_all(directory);
  dlclose(handle);
  std::cout << "PAM stack tests passed\n";
}
