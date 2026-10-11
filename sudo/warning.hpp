// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <string>
#include <sys/types.h>

namespace emaki::sudo_channel {
// Records are private to the root broker and belong to one installation of the
// optional key package, including upgrades. Removing a user's record explicitly
// resets acceptance; package removal and reinstallation starts a new generation.
class WarningAcceptance {
public:
  explicit WarningAcceptance(std::string directory = "/var/lib/emaki-sudo",
                             std::string marker = EMAKI_SUDO_KEY_MARKER,
                             uid_t owner = 0);
  bool accepted(uid_t uid) const;
  bool acknowledge(uid_t uid) const;
private:
  std::string directory_, marker_;
  uid_t owner_;
  std::string epoch() const;
};
}
