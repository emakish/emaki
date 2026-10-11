// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Emaki.Sudo 1.0

Scope {
    id: service
    required property ShellOutputs outputs
    required property bool locked
    readonly property SudoPromptAdapter backend: sudoBackend
    readonly property bool active: !!sudoBackend.flow
    onLockedChanged: sudoClient.setLocked(locked)
    function prepareLock(): void {
        sudoBackend.cancel("lock");
        sudoClient.setLocked(true);
    }
    SudoClient {
        id: sudoClient
        enabled: true
    }
    Component.onCompleted: {
        SudoKeySupport.backend = sudoClient;
        sudoClient.setLocked(service.locked);
    }
    Component.onDestruction: {
        sudoClient.setLocked(true);
        SudoKeySupport.backend = null;
    }
    SudoPromptAdapter {
        id: sudoBackend
        client: sudoClient
    }
    PanelWindow {
        screen: service.outputs.activeScene?.output ?? null
        visible: !!service.outputs.activeScene && !service.locked && SudoKeySupport.keys.length > 0
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "emaki-sudo-key-status"
        WlrLayershell.layer: WlrLayer.Top
        anchors {
            top: true
            right: true
        }
        implicitWidth: 320 + Metrics.side
        implicitHeight: keyIndicator.y + keyIndicator.implicitHeight
        mask: Region {
            item: keyIndicator
        }
        SudoKeyIndicator {
            id: keyIndicator
            width: 320
            y: Metrics.top + Metrics.islandHeight + 8
        }
    }
    IpcHandler {
        target: "sudoKeys"
        // Revocation only. Authentication responses use the native channel.
        function revokeAll(): void {
            sudoClient.revokeAll();
        }
    }
}
