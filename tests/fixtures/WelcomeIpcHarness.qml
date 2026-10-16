// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "../../shell" as Shell

ShellRoot {
    id: harness
    // Stands in for the shell's shortcut list (shell.qml: the scene's openKeyboard("shortcuts")).
    property bool listWorks: true
    property int listOpened: 0
    Shell.WelcomeController {
        id: controller
        loadWallpaper: false
        ready: Quickshell.env("WELCOME_READY") !== "0"
        openShortcutList: () => {
            ++harness.listOpened;
            return harness.listWorks;
        }
    }
    IpcHandler {
        target: "welcomeTest"
        function status(): string {
            return JSON.stringify({
                opened: controller.opened,
                seen: controller.store.seen,
                restored: controller.store.restored,
                saveFailed: controller.store.saveFailed,
                error: controller.shortcutError,
                listOpened: harness.listOpened
            });
        }
        function dismiss(): void {
            controller.dismiss();
        }
        function ready(): void {
            controller.ready = true;
        }
        function shortcuts(): void {
            controller.allShortcuts();
        }
        function failShortcuts(): void {
            harness.listWorks = false;
        }
    }
}
