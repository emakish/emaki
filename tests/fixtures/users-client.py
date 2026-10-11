#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the Users client only against explicitly supplied private test services."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/emaki-settings-users'
loader = importlib.machinery.SourceFileLoader('settings_users_fixture', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
users = importlib.util.module_from_spec(spec)
loader.exec_module(users)


class FixtureSystem(users.System):
    """The synthetic service has no real home directories."""

    def home_exists(self, home):
        return False

    def retain_home(self, user):
        return None


def main():
    # A managed password terminal must re-enter this wrapper, never the installed client.
    users.__file__ = __file__
    accounts = users.Accounts(os.environ['EMAKI_USERS_BUS_ADDRESS'])
    system = FixtureSystem(os.environ['EMAKI_USERS_PASSWD'], os.environ['EMAKI_USERS_GROUP'],
                           os.environ['EMAKI_USERS_LOGIN_DEFS'])
    return users.main(sys.argv[1:], accounts=accounts, system=system,
                      uid=int(os.environ['EMAKI_USERS_TEST_UID']))


if __name__ == '__main__':
    sys.exit(main())
