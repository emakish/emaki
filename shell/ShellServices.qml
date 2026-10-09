// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Persistent stores and session integrations outlive individual outputs.
Scope {
    id: context
    required property NiriService niri
    property bool live: false
    property bool panelOpen: false
    readonly property alias settings: settings
    readonly property alias dockStore: dockStore
    readonly property alias services: services
    readonly property alias notifications: notes
    readonly property alias notificationService: notificationService
    readonly property alias clipboard: clipboard
    readonly property alias catalog: catalog
    readonly property alias identity: identity
    readonly property alias dockLabels: labels
    SettingsCatalog {
        id: settings
        active: false
    }
    DockStore {
        id: dockStore
    }
    // Managed dock values belong to the shared store even with no outputs.
    function applyManagedSettings(): void {
        const on = settings.value("dock.on");
        if (typeof on === "boolean")
            dockStore.on = on;
        const autoHide = settings.value("dock.auto_hide");
        if (typeof autoHide === "boolean")
            dockStore.autoHide = autoHide;
    }
    Component.onCompleted: applyManagedSettings()
    Connections {
        target: settings
        function onValuesChanged(): void {
            context.applyManagedSettings();
        }
    }
    NotificationStore {
        id: notes
    }
    NotificationService {
        id: notificationService
        store: notes
    }
    SystemService {
        id: services
        live: context.live
        panelOpen: context.panelOpen
        onLowBattery: percent => notes.systemBattery(percent)
        onSleepLockFailed: policy => notes.systemSleepLock(policy)
    }
    // Keep the optional recorder alive across output removal; history reads stay local.
    ClipboardHistory {
        id: clipboard
        active: false
    }
    AppCatalog {
        id: catalog
        onFailed: (id, name, reason) => notes.local("Emaki", "Couldn’t open " + (name || id), reason)
    }
    AppIdentity {
        id: identity
        catalog: catalog
    }
    WindowLabels {
        id: labels
        niri: context.niri
        active: dockStore.on
        onAppeared: (id, appId) => identity.windowAppeared(appId)
    }
}
