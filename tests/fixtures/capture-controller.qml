// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: fixture
    property var notices: []
    property var links: []
    QtObject {
        id: notifications
        signal localActionRequested(string action)
        function transientLocal(app, title, body, actions, options) {
            const action = actions?.[0];
            fixture.notices = fixture.notices.concat([{app: app, title: title, body: body,
                transient: true, action: action?.id, label: action?.text}]);
            return -fixture.notices.length;
        }
        function offer(app, title, body, action, label) {
            fixture.notices = fixture.notices.concat([{app: app, title: title, body: body, action: action, label: label}]);
            return -fixture.notices.length;
        }
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
        id: controller
        locked: false
        onLinkRequested: url => fixture.links = fixture.links.concat([url])
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
    // PRODUCTION_CAPTURE_IPC
    IpcHandler {
        target: "tests"
        function configure(scenario: string): bool {
            if (controller.busy || controller.opened)
                return false;
            fixture.notices = [];
            fixture.links = [];
            controller.locked = false;
            controller.helperCommand = scenario === "missing" ? [Quickshell.env("CAPTURE_MISSING")] : [Quickshell.env("CAPTURE_PYTHON"), "-B", Quickshell.env("CAPTURE_HELPER"), scenario];
            return true;
        }
        function present(): bool {
            return controller.open();
        }
        function dismiss(): void {
            controller.close();
        }
        function lock(value: bool): void {
            controller.locked = value;
        }
        function mode(value: string): bool {
            return controller.selectMode(value);
        }
        function targetKind(value: string): bool {
            return controller.selectTarget(value);
        }
        function output(name: string): bool {
            return controller.selectOutput(name);
        }
        function windowId(id: double): bool {
            return controller.selectWindow(id);
        }
        function area(x: real, y: real, width: real, height: real): void {
            controller.setArea(x, y, width, height);
        }
        function begin(): string {
            const accepted = controller.capture();
            return JSON.stringify({
                accepted: accepted,
                opened: controller.opened,
                busy: controller.busy,
                duplicate: controller.capture()
            });
        }
        function finish(): bool {
            return controller.stop();
        }
        function loseHelper(): void {
            controller.helperCommand = [Quickshell.env("CAPTURE_MISSING")];
        }
        function action(value: string): void {
            notifications.localActionRequested(value);
        }
        function state(): string {
            const state = JSON.parse(controller.status());
            state.notices = fixture.notices;
            state.links = fixture.links;
            state.canCapture = controller.canCapture;
            return JSON.stringify(state);
        }
    }
}
