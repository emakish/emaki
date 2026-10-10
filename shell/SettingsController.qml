// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    id: controller
    required property var catalog
    required property SystemService service
    required property NiriService niri
    property var notificationStore: null
    readonly property alias displays: displays
    property bool enabled: true
    property var targetHost: null
    property var activeHost: null
    property string requestedPage: "panel"
    readonly property var view: activeHost?.input.settingsView ?? null
    readonly property string page: view?.page ?? requestedPage
    readonly property var availablePages: pageRegistry.availablePages
    readonly property bool opened: !!activeHost?.launcherOpen && !!activeHost?.input.settingsActive
    // Display confirmation and startup recovery belong to the session, not a page.
    DisplaysService {
        id: displays
    }
    function open(requested: string): string {
        return openOn(targetHost, requested);
    }
    function openOn(host, requested: string): string {
        const next = requested || "panel";
        if (!enabled || !availablePages.includes(next) || !host?.enabled || (!host.output && !host.headless))
            return JSON.stringify({
                schema_version: 1,
                status: "unavailable",
                page: next
            });
        if (activeHost && activeHost !== host)
            activeHost.closeLauncher();
        host.openLauncher(true);
        activeHost = host;
        requestedPage = next;
        const shown = host.input.showSettings(next);
        return JSON.stringify({
            schema_version: 1,
            status: shown ? "opened" : "unavailable",
            page: next
        });
    }
    function dismiss(): void {
        if (activeHost)
            activeHost.closeLauncher();
    }
    Component.onCompleted: SettingsBridge.controller = controller
    Component.onDestruction: {
        if (SettingsBridge.controller === controller)
            SettingsBridge.controller = null;
    }
    Connections {
        target: controller.view
        function onPageChanged(): void {
            controller.requestedPage = controller.view.page;
        }
    }
    SettingsPageRegistry {
        id: pageRegistry
    }
}
