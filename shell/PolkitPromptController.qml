// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "PolkitText.js" as Copy

Scope {
    id: controller
    required property var backend
    property bool locked: true
    property int timeoutMs: 120000
    property double deadline: 0
    property bool opened: false
    property bool wrong: false
    property bool cancelling: false
    property bool inputBlocked: false
    property var currentFlow: null
    readonly property bool accepting: opened && !locked && !inputBlocked && backend.responseRequired && backend.passwordRequest
    signal clearInput
    signal focusInput
    signal shake
    signal notice(string title, string body)
    signal started

    function sync(): void {
        if (!backend || currentFlow === backend.flow)
            return;
        clearInput();
        currentFlow = backend.flow;
        wrong = false;
        opened = !!currentFlow && !locked;
        inputBlocked = opened;
        inputGuard.stop();
        if (opened)
            inputGuard.start();
        deadline = opened ? Date.now() + timeoutMs : 0;
        if (locked && currentFlow) {
            notice(backend.requestKind === "sudo" ? Copy.sudoInterruptedTitle() : Copy.blockedTitle(), backend.requestKind === "sudo" ? Copy.sudoInterruptedBody() : Copy.cancelledBody());
            backend?.cancel("lock");
        } else if (opened) {
            started();
            focusInput();
        }
    }
    function submit(value: string): void {
        if (!accepting)
            return;
        clearInput();
        backend.submit(value);
    }
    function submitKey(value: string, mode: string, seconds: int): void {
        if (!accepting || !backend.keyEligible)
            return;
        clearInput();
        backend.submitKey(value, mode, seconds);
    }
    function cancel(reason: string): void {
        const wasOpen = opened;
        const sudoInterrupted = backend.requestKind === "sudo" && reason !== "cancel" && reason !== "escape";
        opened = false;
        inputBlocked = false;
        inputGuard.stop();
        deadline = 0;
        wrong = false;
        clearInput();
        if (!cancelling) {
            cancelling = true;
            backend?.cancel(reason);
            cancelling = false;
        }
        if (wasOpen) {
            const title = reason === "timeout" ? qsTranslate("PolkitPromptController", "Not done — nobody answered") : reason === "lock" ? Copy.lockedTitle() : qsTranslate("PolkitPromptController", "Not done — you cancelled");
            notice(sudoInterrupted ? Copy.sudoInterruptedTitle() : title, sudoInterrupted ? Copy.sudoInterruptedBody() : (reason === "timeout" ? qsTranslate("PolkitPromptController", "The request closed after 2 minutes. Nothing was done. The program was told you said no.") : Copy.cancelledBody()));
        }
    }
    function tick(now: double): void {
        if (opened && deadline > 0 && now >= deadline)
            cancel("timeout");
    }
    onLockedChanged: {
        if (locked)
            cancel("lock");
        sync();
    }
    Component.onCompleted: sync()
    Component.onDestruction: {
        clearInput();
        backend?.cancel("shutdown");
    }
    Connections {
        target: controller.backend
        function onFlowChanged(): void {
            controller.sync();
        }
        function onResponseRequiredChanged(): void {
            if (controller.accepting)
                controller.focusInput();
        }
        function onRejected(): void {
            if (!controller.opened || controller.locked)
                return;
            controller.clearInput();
            controller.wrong = true;
            controller.shake();
            controller.focusInput();
        }
        function onInterrupted(): void {
            controller.notice(Copy.sudoInterruptedTitle(), Copy.sudoInterruptedBody());
        }
    }
    Timer {
        id: inputGuard
        interval: 1000
        onTriggered: {
            controller.clearInput();
            controller.inputBlocked = false;
            if (controller.accepting)
                controller.focusInput();
        }
    }
    Timer {
        interval: 200
        repeat: true
        running: controller.opened
        onTriggered: controller.tick(Date.now())
    }
}
