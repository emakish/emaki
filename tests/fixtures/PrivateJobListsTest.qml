// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

// The production shell QML is copied beside this harness by the test. Lists in helper replies
// reach the product: the launcher's Recent (launcher-tools.py) and the sleep-lock notice
// (system-tools.py). Each goes through a PrivateJob `completed(var)` signal.
ShellRoot {
    RecentLog {
        id: recent
        active: true
        // The handler sets the state before the entries.
        onStateChanged: Qt.callLater(() => console.info("RECENT " + recent.state + " " + recent.entries.length))
    }
    SystemService {
        id: system
        helpersEnabled: true
        onSleepLockFailed: policy => console.info("SLEEP_LOCK " + policy)
        Component.onCompleted: takeSleepFlags(false)
    }
}
