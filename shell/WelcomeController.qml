// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: controller
    readonly property bool liveSession: Quickshell.env("EMAKI_LIVE_SESSION") === "1"
    property bool enabled: true
    property bool ready: false
    property bool loadWallpaper: true
    property bool pending: false
    property bool presentedThisRun: false
    property string shortcutError: ""
    // Opens the shell's searchable shortcut list, the one Super + / opens (shell.qml sets it to
    // the active scene's openKeyboard("shortcuts")); returns whether it opened.
    property var openShortcutList: null
    readonly property WelcomeWindow window: windowLoader.item as WelcomeWindow
    readonly property alias store: store
    readonly property bool opened: windowLoader.active
    signal opening
    function maybePresent(): void {
        if (enabled && ready && store.restored && (pending || (!store.seen && !presentedThisRun)))
            present();
    }
    function present(): string {
        if (!enabled || liveSession)
            return "unavailable";
        if (!ready) {
            pending = true;
            return "queued";
        }
        pending = false;
        shortcutError = "";
        opening();
        windowLoader.active = true;
        window?.present();
        return window ? "presented" : "unavailable";
    }
    function dismiss(): void {
        windowLoader.active = false;
    }
    function allShortcuts(): void {
        shortcutError = "";
        let shown = false;
        try {
            shown = typeof openShortcutList === "function" && openShortcutList() === true;
        } catch (_) {
            shown = false;
        }
        if (!shown)
            shortcutError = "Could not open the shortcut list. Try Super + /.";
    }
    onReadyChanged: Qt.callLater(maybePresent)
    onEnabledChanged: Qt.callLater(maybePresent)
    Component.onCompleted: Qt.callLater(maybePresent)
    WelcomeStore {
        id: store
        onRestoredChanged: Qt.callLater(controller.maybePresent)
    }
    Loader {
        id: windowLoader
        active: false
        sourceComponent: WelcomeWindow {
            loadWallpaper: controller.loadWallpaper
            error: controller.shortcutError || (store.saveFailed ? "Could not save welcome progress. This screen may appear again next login." : "")
            onBackingWindowVisibleChanged: {
                if (backingWindowVisible) {
                    controller.presentedThisRun = true;
                    store.markSeen();
                }
            }
            onDismissRequested: controller.dismiss()
            onClosed: controller.dismiss()
            onShortcutsRequested: controller.allShortcuts()
        }
    }
    IpcHandler {
        target: "welcome"
        function present(): string {
            return controller.present();
        }
    }
}
