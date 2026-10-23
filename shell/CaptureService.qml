// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    required property ShellOutputs outputs
    CaptureController {
        id: controller
        locked: service.outputs.authenticationLockState?.locked ?? true
        enabled: !(service.outputs.startup?.coverActive ?? false)
        preferredOutput: service.outputs.focusedScreen?.name ?? ""
        notificationStore: service.outputs.shared.notifications
        onOpening: service.outputs.closeAll()
        onLinkRequested: url => Qt.openUrlExternally(url)
    }
    // The privacy pill leaves the shell's own recording to its Stop control.
    Binding {
        target: service.outputs.shared
        property: "captureNode"
        value: controller.streamNode
    }
    Loader {
        Component.onCompleted: setSource(Qt.resolvedUrl("CaptureOverlay.qml"), {
            controller: controller
        })
    }
    Connections {
        target: service.outputs
        function onClosing(): void {
            controller.close();
        }
    }
    IpcHandler {
        target: "capture"
        function open(): bool {
            return controller.open();
        }
        function close(): void {
            controller.close();
        }
        function mode(value: string): bool {
            return controller.selectMode(value);
        }
        function selection(value: string): bool {
            return controller.selectTarget(value);
        }
        function output(name: string): bool {
            return controller.selectOutput(name);
        }
        function window(id: string): bool {
            return /^[0-9]+$/.test(id) && controller.selectWindow(Number(id));
        }
        function area(x: int, y: int, width: int, height: int): void {
            controller.setArea(x, y, width, height);
        }
        function start(): bool {
            return controller.capture();
        }
        function stop(): bool {
            return controller.stop();
        }
        function record(): bool {
            return controller.record();
        }
        function text(): bool {
            return controller.recognize("text");
        }
        function qr(): bool {
            return controller.recognize("qr");
        }
        function status(): string {
            return controller.status();
        }
    }
}
