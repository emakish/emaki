// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    required property ShellOutputs outputs
    readonly property var sudoService: sudoLoader.item
    // For the offscreen load test (tests/fixtures/polkit-service-offscreen.qml).
    readonly property PolkitPromptController controller: prompt
    readonly property var surfaceObject: surfaceLoader.item
    // The native sudo module and its windows must stay out of the headless type graph.
    Loader {
        id: sudoLoader
        objectName: "sudoPromptServiceLoader"
        Component.onCompleted: setSource(Qt.resolvedUrl("SudoPromptService.qml"), {
            "outputs": Qt.binding(() => service.outputs),
            "locked": Qt.binding(() => lockState.locked)
        })
    }
    PolkitAdapter {
        id: nativeBackend
    }
    PolkitLockState {
        id: lockState
    }
    PolkitPromptQueue {
        id: queue
        polkitBackend: nativeBackend
        sudoBackend: service.sudoService?.backend ?? null
    }
    PolkitPromptController {
        id: prompt
        backend: queue
        locked: lockState.locked
        onStarted: service.outputs.closeAll()
        onNotice: (title, body) => service.outputs.shared.notifications.local("Emaki", title, body)
    }
    // The protected surface follows the focused output; its body owns the clock morph.
    Loader {
        id: surfaceLoader
        objectName: "authenticationSurfaceLoader"
        Component.onCompleted: setSource(Qt.resolvedUrl("PolkitPromptSurface.qml"), {
            "controller": prompt,
            "scene": Qt.binding(() => service.outputs.activeScene)
        })
    }
    Binding {
        target: service.outputs
        property: "authenticationPrompt"
        value: service.surfaceObject?.body ?? null
    }
    IpcHandler {
        target: "authentication"
        // Cancellation only. Passwords and request details have no IPC endpoint.
        function lock(): bool {
            lockState.prepareLock();
            prompt.cancel("lock");
            // An unavailable service cannot acknowledge cancellation.
            if (sudoLoader.status !== Loader.Ready || !service.sudoService)
                return false;
            service.sudoService.prepareLock();
            return !nativeBackend.flow && !service.sudoService.active;
        }
    }
}
