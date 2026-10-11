// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <cassert>
#include <cerrno>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <iostream>
#include <security/pam_appl.h>
#include <unistd.h>
// Preserve production PAM dispatch on hosts that prohibit audit sockets.
extern "C" int audit_open() {
  auto real = reinterpret_cast<int (*)()>(dlsym(RTLD_NEXT, "audit_open"));
  assert(real);
  int result = real();
  if (result < 0 && errno == EPERM)
    errno = EAFNOSUPPORT;
  return result;
}
int conversation(int, const pam_message **, pam_response **, void *) {
  return PAM_CONV_ERR;
}
int main(int argc, char **argv) {
  assert(argc == 5);
  int tty = open("/dev/tty", O_RDONLY | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
  bool terminal = tty >= 0 && isatty(tty);
  if (tty >= 0)
    close(tty);
  assert(terminal == (strcmp(argv[4], "tty") == 0));
  pam_conv conv{conversation, nullptr};
  pam_handle_t *handle = nullptr;
  assert(pam_start_confdir("emaki-sudo", argv[2], &conv, argv[1], &handle) ==
         PAM_SUCCESS);
  int result = pam_authenticate(handle, 0);
  int expected = strcmp(argv[3], "permit") == 0 ? PAM_SUCCESS : PAM_AUTH_ERR;
  if (result != expected)
    std::cerr << "Unexpected authentication: " << pam_strerror(handle, result)
              << '\n';
  assert(result == expected);
  if (result == PAM_SUCCESS)
    assert(pam_acct_mgmt(handle, 0) == PAM_SUCCESS);
  assert(pam_end(handle, result) == PAM_SUCCESS);
  std::cout << "PRODUCTION_PAM_PASSED\n";
}
