// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "WhatsNewContent.js" as ReleaseContent

Scope {
    id: controller
    readonly property bool liveSession: Quickshell.env("EMAKI_LIVE_SESSION") === "1"
    property bool enabled: true
    property bool ready: false
    property bool loadWallpaper: true
    property bool pending: false
    property bool presentedThisRun: false
    property string shortcutError: ""
    property alias releasePath: whatsNewStoreObject.releasePath
    property bool showingWhatsNew: false
    readonly property bool hasWhatsNew: whatsNewStoreObject.available
    readonly property alias whatsNewStore: whatsNewStoreObject
    // Opens the shell's searchable shortcut list, the one Super + / opens (shell.qml sets it to
    // the active scene's openKeyboard("shortcuts")); returns whether it opened.
    property var openShortcutList: null
    readonly property WelcomeWindow window: windowLoader.item as WelcomeWindow
    readonly property alias store: storeObject
    readonly property bool opened: windowLoader.active
    signal opening
    signal languageRequested
    function maybePresent(): void {
        if (!enabled || !ready || liveSession || !storeObject.restored || !whatsNewStoreObject.restored)
            return;
        whatsNewStoreObject.initialize(storeObject.seen);
        if (pending || (!storeObject.seen && !presentedThisRun))
            present();
        else if (!opened && whatsNewStoreObject.pending)
            presentWhatsNew();
    }
    function present(): string {
        if (!enabled || liveSession)
            return "unavailable";
        if (!ready) {
            pending = true;
            return "queued";
        }
        pending = false;
        if (showingWhatsNew)
            windowLoader.active = false;
        showingWhatsNew = false;
        shortcutError = "";
        opening();
        windowLoader.active = true;
        window?.present();
        return window ? "presented" : "unavailable";
    }
    function presentWhatsNew(): string {
        if (!enabled || liveSession || !ready || !hasWhatsNew)
            return "unavailable";
        whatsNewStoreObject.initialize(storeObject.seen);
        if (!showingWhatsNew)
            windowLoader.active = false;
        showingWhatsNew = true;
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
            shortcutError = qsTranslate("WelcomeController", "Could not open the shortcut list. Try Super + /.");
    }
    onReadyChanged: Qt.callLater(maybePresent)
    onEnabledChanged: Qt.callLater(maybePresent)
    Component.onCompleted: Qt.callLater(maybePresent)
    WelcomeStore {
        id: storeObject
        onRestoredChanged: Qt.callLater(controller.maybePresent)
    }
    WhatsNewStore {
        id: whatsNewStoreObject
        onRestoredChanged: Qt.callLater(controller.maybePresent)
    }
    Loader {
        id: windowLoader
        active: false
        sourceComponent: WelcomeWindow {
            whatsNew: controller.showingWhatsNew
            whatsNewAvailable: controller.hasWhatsNew
            loadWallpaper: controller.loadWallpaper
            error: controller.shortcutError || (controller.showingWhatsNew ? (whatsNewStoreObject.saveFailed ? ReleaseContent.saveError() : "") : (storeObject.saveFailed ? qsTranslate("WelcomeController", "Could not save welcome progress. This screen may appear again next login.") : ""))
            onBackingWindowVisibleChanged: {
                if (backingWindowVisible) {
                    if (controller.showingWhatsNew) {
                        whatsNewStoreObject.markSeen();
                    } else {
                        whatsNewStoreObject.initialize(storeObject.seen);
                        controller.presentedThisRun = true;
                        storeObject.markSeen();
                    }
                }
            }
            onDismissRequested: controller.dismiss()
            onClosed: controller.dismiss()
            onShortcutsRequested: controller.allShortcuts()
            onWhatsNewRequested: controller.presentWhatsNew()
            onLanguageRequested: {
                controller.dismiss();
                controller.languageRequested();
            }
        }
    }
    IpcHandler {
        target: "welcome"
        function present(): string {
            return controller.present();
        }
        function whatsNew(): string {
            return controller.presentWhatsNew();
        }
    }
}
