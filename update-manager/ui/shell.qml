// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//@ pragma UseQApplication
//@ pragma AppId emaki-update-manager
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: root
    property string currentPage: Quickshell.env("EMAKI_MANAGER_PAGE") === "updates" ? "updates" : "browse"
    property string currentCategory: ""
    property string currentCategoryName: ""
    property string currentQuery: ""
    property string notifiedRestart: ""
    property bool presenting: false
    property bool checkPending: false
    Component.onCompleted: Quickshell.watchFiles = false
    UpdateController { id: controller; externalBusy: programs.mutating }
    ProgramsController {
        id: programs
        externalBusy: controller.busy || controller.restorePending
        onTransactionFinished: ok => {
            root.checkPending = true;
            Qt.callLater(root.flushCheck);
        }
    }
    function flushCheck(): void {
        if (!checkPending || controller.busy || programs.mutating) return;
        checkPending = false;
        controller.check();
    }
    function notifyIfHidden(): void {
        const window = windowLoader.item as FloatingWindow;
        if (presenting || window?.visible || !controller.reminder) return;
        const key = JSON.stringify(controller.restartPackages);
        if (notifiedRestart === key) return;
        notifiedRestart = key;
        controller.notifyRestart();
    }
    Connections {
        target: controller
        function onBusyChanged(): void { if (!controller.busy) Qt.callLater(root.flushCheck); }
        function onReminderChanged(): void {
            if (!controller.reminder) root.notifiedRestart = "";
            else root.notifyIfHidden();
        }
        function onRestartPackagesChanged(): void { root.notifyIfHidden(); }
    }
    function present(): bool {
        presenting = true;
        if (!windowLoader.active) windowLoader.active = true;
        const window = windowLoader.item as FloatingWindow;
        if (!window) { presenting = false; return false; }
        if (controller.phase === "idle") controller.check();
        window.visible = false;
        window.visible = true;
        window.minimized = false;
        presenting = false;
        return window.visible;
    }
    function presentUpdates(): bool {
        if (!present()) return false;
        root.currentPage = "updates";
        const window = windowLoader.item;
        window.manager.navigate("updates");
        return true;
    }
    Loader {
        id: windowLoader
        onActiveChanged: if (!active) root.notifyIfHidden()
        sourceComponent: FloatingWindow {
            id: window
            property alias manager: updateView
            title: "Programs & Updates"
            implicitWidth: 1180
            implicitHeight: 780
            minimumSize: Qt.size(960, 600)
            color: "transparent"
            onClosed: { windowLoader.active = false; Qt.callLater(root.notifyIfHidden); }
            onVisibleChanged: {
                if (visible) updateView.present();
                else Qt.callLater(root.notifyIfHidden);
            }
            Shell.WallpaperSource {
                id: wallpaper
                outputWidth: window.width
                outputHeight: window.height
                outputScale: window.devicePixelRatio
                outputName: window.screen?.name || ""
                variant: "sharp"
                refreshManaged: true
                helperPath: Shell.Platform.dataDir + "/shell/helpers/wallpaper.py"
            }
            ManagerView {
                id: updateView
                anchors.fill: parent
                controller: controller
                programs: programs
                page: root.currentPage
                onPageChanged: root.currentPage = page
                category: root.currentCategory
                onCategoryChanged: root.currentCategory = category
                categoryName: root.currentCategoryName
                onCategoryNameChanged: root.currentCategoryName = categoryName
                query: root.currentQuery
                onQueryChanged: root.currentQuery = query
                wallpaperTexture: wallpaper.texture
                dpr: window.devicePixelRatio
            }
        }
    }
    IpcHandler {
        target: "updates"
        function present(): string { return root.present() ? "presented" : "not-presented"; }
        function presentUpdates(): string { return root.presentUpdates() ? "presented" : "not-presented"; }
    }
}
