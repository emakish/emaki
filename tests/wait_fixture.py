# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded condition polling for asynchronous test fixtures."""
import time


def wait_for(condition, timeout=30, interval=.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError('condition wait expired')
