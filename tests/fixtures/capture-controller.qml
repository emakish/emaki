// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: fixture
    property var notices: []
    QtObject {
        id: notifications
        function local(app, title, body) {
            fixture.notices = fixture.notices.concat([
                {
                    app: app,
                    title: title,
                    body: body
                }
            ]);
        }
    }
    CaptureController {
        id: capture
        locked: false
        preferredOutput: "TEST-1"
        screens: [
            {
                name: "TEST-1"
            },
            {
                name: "TEST-2"
            }
        ]
        notificationStore: notifications
    }
    IpcHandler {
        target: "tests"
        function configure(scenario: string): bool {
            if (capture.busy || capture.opened)
                return false;
            fixture.notices = [];
            capture.locked = false;
            capture.helperCommand = scenario === "missing" ? [Quickshell.env("CAPTURE_MISSING")] : [Quickshell.env("CAPTURE_PYTHON"), "-B", Quickshell.env("CAPTURE_HELPER"), scenario];
            return true;
        }
        function present(): bool {
            return capture.open();
        }
        function dismiss(): void {
            capture.close();
        }
        function lock(value: bool): void {
            capture.locked = value;
        }
        function mode(value: string): bool {
            return capture.selectMode(value);
        }
        function targetKind(value: string): bool {
            return capture.selectTarget(value);
        }
        function output(name: string): bool {
            return capture.selectOutput(name);
        }
        function windowId(id: double): bool {
            return capture.selectWindow(id);
        }
        function area(x: real, y: real, width: real, height: real): void {
            capture.setArea(x, y, width, height);
        }
        function begin(): string {
            const accepted = capture.capture();
            return JSON.stringify({
                accepted: accepted,
                opened: capture.opened,
                busy: capture.busy,
                duplicate: capture.capture()
            });
        }
        function finish(): bool {
            return capture.stop();
        }
        function loseHelper(): void {
            capture.helperCommand = [Quickshell.env("CAPTURE_MISSING")];
        }
        function state(): string {
            const state = JSON.parse(capture.status());
            state.notices = fixture.notices;
            state.canCapture = capture.canCapture;
            return JSON.stringify(state);
        }
    }
}
