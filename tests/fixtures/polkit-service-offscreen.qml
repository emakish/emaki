// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// The production authorization service with no compositor: the native polkit listener, the
// adapter, the controller and the lock state must instantiate. The surfaces need a Wayland
// PanelWindow backend and the sudo service its native module; neither is checked here.
ShellRoot {
    id: root
    NiriService {
        id: niri
        binary: ""
    }
    ShellOutputs {
        id: outputs
        niri: niri
        headless: true
        enabled: false
        screens: []
    }
    PolkitPromptService {
        id: service
        outputs: outputs
    }
    Timer {
        interval: 500
        running: true
        onTriggered: {
            const controller = service.controller;
            const adapter = controller ? controller.backend.polkitBackend as PolkitAdapter : null;
            const failures = [[controller !== null, "controller"], [adapter !== null, "native adapter"], [!!adapter && adapter.flow === null && !adapter.responseRequired, "adapter has no request"], [!!adapter && adapter.registered === false, "listener did not register without a bus"], [!!controller && typeof controller.locked === "boolean", "controller follows a lock state"]].filter(row => !row[0]).map(row => row[1]);
            if (failures.length > 0) {
                console.error("POLKIT_OFFSCREEN_FAIL " + failures.join(", "));
                Qt.exit(1);
                return;
            }
            console.info("POLKIT_OFFSCREEN_OK");
            Qt.quit();
        }
    }
}
