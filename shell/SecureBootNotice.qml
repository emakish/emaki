// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "SecureBoot.js" as Logic

// Once per session start, one notice pointing to Settings. First: startup files an update could
// not sign (Emaki will not start with Secure Boot on), whatever the firmware switch says; Settings
// signs them again. Otherwise: Emaki runs with Secure Boot off and this computer's key is not
// confirmed; Settings queues a new confirmation. Machines without the key never see either.
// The notice is held (NotificationStore.hold): its preview stays, after Welcome if Welcome is
// open, until the person opens Settings or dismisses it, and the next login raises it again
// while the problem remains. The session asks until the answer is known: while root's copy is not
// written yet the answer knows no key, and a read can fail (the command missing or failing) or
// answer something malformed. Then it asks again: `attempts` times every retryInterval (root's
// copy is normally there within seconds), then every slowInterval, doubling up to
// slowestInterval, and stops at the first answer that knows.
Scope {
    id: notice
    required property NotificationStore store
    property bool ready: false
    property bool checked: false
    property string reply: ""
    property int asked: 0
    property bool pending: false
    property int attempts: 25
    property int retryInterval: 5000
    property int slowInterval: 60000
    property int slowestInterval: 900000
    readonly property string action: "settings:privacy"
    signal settingsRequested
    function check(): void {
        if (!ready || checked)
            return;
        checked = true;
        ask();
    }
    function ask(): void {
        asked += 1;
        reply = "";
        pending = true;
        probe.running = true;
    }
    function nextInterval(): int {
        return asked < attempts ? retryInterval : Math.min(slowestInterval, slowInterval * Math.pow(2, asked - attempts));
    }
    // One answer per ask: the exit code, or -1 when the command did not start (no exit then).
    function answered(code: int): void {
        if (!pending)
            return;
        pending = false;
        let status = null;
        try {
            status = code === 0 ? JSON.parse(reply) : null;
        } catch (_) {}
        if (!Logic.valid(status) || Logic.unpublished(status)) {
            retry.interval = nextInterval();
            retry.start();
            return;
        }
        const kind = Logic.noticeKind(status);
        if (kind === "unsigned")
            store.hold("Secure Boot", Logic.unsignedSummary(), Logic.unsignedBody(), action, Logic.noticeAction());
        else if (kind === "confirm")
            store.hold("Secure Boot", Logic.noticeSummary(), Logic.noticeBody(), action, Logic.noticeAction());
    }
    onReadyChanged: check()
    Component.onCompleted: check()
    Process {
        id: probe
        command: ["emaki-secureboot", "status", "--json"]
        stdout: StdioCollector {
            onStreamFinished: notice.reply = text
        }
        // Quickshell ends the output stream, then reports the exit, then running; a command that
        // fails to start reports only running.
        onExited: code => notice.answered(code)
        onRunningChanged: if (!running)
            notice.answered(-1)
    }
    Timer {
        id: retry
        onTriggered: notice.ask()
    }
    Connections {
        target: notice.store
        function onLocalActionRequested(action: string): void {
            if (action === notice.action)
                notice.settingsRequested();
        }
    }
}
