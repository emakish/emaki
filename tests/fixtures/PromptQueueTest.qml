// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: root
    property int phase: 0
    property double changedAt: 0
    property double savedDeadline: 0
    property var password: null
    property var activeIdentity: null

    component Source: QtObject {
        property string requestKind: "polkit"
        property var flow: null
        property bool responseRequired: true
        property bool passwordRequest: true
        property string requestLine: requestKind + " request"
        property string accountName: "fixture"
        property string iconName: ""
        property var detailRows: []
        property bool agent: false
        property bool keyOffered: true
        property bool keyEligible: true
        property bool turnEligible: true
        property int submissions: 0
        property int cancellations: 0
        signal rejected
        signal interrupted
        function submit(value: string): void {
            ++submissions;
            responseRequired = false;
        }
        function submitKey(value: string, mode: string, seconds: int): void {
            submit(value);
        }
        function cancel(reason: string): void {
            ++cancellations;
            flow = null;
        }
    }
    Source {
        id: polkit
    }
    Source {
        id: sudoSource
        requestKind: "sudo"
    }
    PolkitPromptQueue {
        id: queue
        polkitBackend: polkit
        sudoBackend: sudoSource
    }
    PolkitPromptController {
        id: controller
        backend: queue
        locked: false
    }
    Window {
        id: window
        width: 1280
        height: 720
        visible: true
        PolkitPromptCard {
            id: card
            controller: controller
            reducedMotion: true
            sudoKeyInstalled: true
        }
        TestCase {
            id: input
            when: false
        }
    }
    function check(condition: bool, message: string): void {
        if (!condition)
            throw new Error("Prompt queue: " + message);
    }
    function begin(source: var, name: string): void {
        source.responseRequired = true;
        source.flow = {
            id: name
        };
    }
    function guarded(source: var): void {
        changedAt = Date.now();
        check(queue.activeBackend === source && controller.opened, "expected source visible");
        check(controller.inputBlocked && !controller.accepting, "new identity blocks input");
        check(password.text === "" && !card.detailsOpen && !card.keyOpen, "new identity clears password and expansions");
        const oldFlow = queue.flow;
        const cancels = source.cancellations;
        const submits = source.submissions;
        for (const key of [Qt.Key_A, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Tab])
            input.keyClick(key);
        check(password.text === "" && queue.flow === oldFlow, "stale keys cannot alter or dismiss new request");
        check(source.submissions === submits && source.cancellations === cancels, "stale keys cannot authorize or cancel");
        check(controller.inputBlocked, "guard persists through stale keys");
        earlyGuard.restart();
    }
    function ready(): void {
        check(Date.now() - changedAt >= 1000, "one second elapsed");
        check(controller.accepting && !controller.inputBlocked, "input resumes after guard");
        card.focusPassword();
        input.keyClick(Qt.Key_B);
        check(password.text === "b", "real typing accepted after guard");
    }
    function step(): void {
        if (phase === 0) {
            password = input.findChild(card, "polkit-password");
            check(password !== null, "actual password field found");
            begin(sudoSource, "sudo-first");
            guarded(sudoSource);
        } else if (phase === 1) {
            ready();
            card.detailsOpen = true;
            card.keyOpen = true;
            savedDeadline = controller.deadline;
            activeIdentity = queue.flow;
            begin(polkit, "polkit-waits");
            check(queue.flow === activeIdentity && queue.requests.length === 2, "sudo then polkit FIFO");
            check(password.text === "b" && card.detailsOpen && card.keyOpen, "arrival preserves current content");
            check(controller.deadline === savedDeadline && controller.accepting, "arrival preserves deadline and guard");
            polkit.flow = null;
            check(queue.flow === activeIdentity && queue.requests.length === 1 && password.text === "b", "waiting cancellation leaves active untouched");
            begin(polkit, "polkit-next");
            sudoSource.flow = null;
            guarded(polkit);
        } else if (phase === 2) {
            ready();
            savedDeadline = controller.deadline;
            activeIdentity = queue.flow;
            begin(sudoSource, "sudo-waits");
            check(queue.flow === activeIdentity && password.text === "b", "polkit then sudo FIFO");
            check(controller.deadline === savedDeadline && controller.accepting, "reverse arrival leaves timeout and guard untouched");
            polkit.flow = null;
            guarded(sudoSource);
        } else if (phase === 3) {
            ready();
            card.detailsOpen = true;
            card.keyOpen = true;
            begin(sudoSource, "sudo-replacement");
            guarded(sudoSource);
        } else if (phase === 4) {
            ready();
            savedDeadline = controller.deadline;
            controller.submit(password.text);
            check(sudoSource.submissions === 1 && password.text === "", "submission routes to active source and clears");
            sudoSource.rejected();
            sudoSource.responseRequired = true;
            check(controller.accepting && !controller.inputBlocked && controller.deadline === savedDeadline, "same identity retry keeps guard and deadline");
            input.keyClick(Qt.Key_C);
            check(password.text === "c", "retry accepts immediate typing");
            begin(polkit, "waiting-on-lock");
            controller.locked = true;
            check(!controller.opened && !controller.accepting && password.text === "", "lock closes input without promotion");
            check(polkit.flow === null && sudoSource.flow === null && queue.requests.length === 0, "lock cancels all queued source heads");
            controller.locked = false;
            check(!controller.opened, "unlock does not restore cancelled requests");
            begin(polkit, "polkit-replacement-one");
            guarded(polkit);
        } else if (phase === 5) {
            ready();
            card.detailsOpen = true;
            begin(polkit, "polkit-replacement-two");
            guarded(polkit);
        } else if (phase === 6) {
            ready();
            begin(sudoSource, "sudo-after-cancel");
            controller.cancel("cancel");
            guarded(sudoSource);
        } else {
            ready();
            console.info("PROMPT_QUEUE_OK");
            Qt.quit();
        }
        ++phase;
        next.restart();
    }
    Timer {
        id: earlyGuard
        interval: 700
        onTriggered: {
            try {
                root.check(controller.inputBlocked && !controller.accepting, "guard still closed before one second");
                const oldFlow = queue.flow;
                input.keyClick(Qt.Key_Z);
                input.keyClick(Qt.Key_Return);
                input.keyClick(Qt.Key_Escape);
                root.check(root.password.text === "" && queue.flow === oldFlow, "keys before one second remain swallowed");
            } catch (error) {
                console.error(String(error));
                Qt.exit(1);
            }
        }
    }
    Timer {
        id: next
        interval: root.phase === 0 ? 100 : 1100
        running: true
        onTriggered: {
            try {
                root.step();
            } catch (error) {
                console.error(String(error));
                Qt.exit(1);
            }
        }
    }
}
