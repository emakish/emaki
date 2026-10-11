// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
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
    TestCase {
        name: "ProductionPolkitLoad"
        when: true
        function test_productionTypes(): void {
            const loader = findChild(service, "authenticationSurfaceLoader");
            verify(loader !== null);
            tryCompare(loader, "status", Loader.Ready);
            const surface = loader.item as PolkitPromptSurface;
            verify(surface !== null);
            verify((surface.controller.backend.polkitBackend as PolkitAdapter) !== null);
            const sudoLoader = findChild(service, "sudoPromptServiceLoader");
            verify(sudoLoader !== null);
            tryCompare(sudoLoader, "status", Loader.Ready);
            verify(sudoLoader.item !== null);
            compare(sudoLoader.item.outputs, outputs);
            compare(sudoLoader.item.locked, true);
            compare(sudoLoader.item.active, false);
            sudoLoader.item.prepareLock();
            compare(sudoLoader.item.active, false);
            console.info("POLKIT_SERVICE_OK");
            Qt.quit();
        }
    }
}
