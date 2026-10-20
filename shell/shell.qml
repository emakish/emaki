// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    property string borderMode: Quickshell.env("EMAKI_SHELL_BORDER")
    readonly property string requestedOutput: Quickshell.env("EMAKI_SHELL_OUTPUT")
    readonly property bool headless: Quickshell.env("EMAKI_SHELL_HEADLESS") === "1"
    readonly property int reservedSpace: Number(Quickshell.env("EMAKI_SHELL_EXCLUSIVE_ZONE") || String(Metrics.reservedSpace))
    readonly property int testWidth: Number(Quickshell.env("EMAKI_SHELL_TEST_WIDTH") || "0")
    readonly property int testHeight: Number(Quickshell.env("EMAKI_SHELL_TEST_HEIGHT") || "0")
    readonly property bool valid: ["soft", "full"].includes(borderMode) && reservedSpace >= 0 && testWidth >= 0 && testHeight >= 0 && (!headless || (testWidth > 0 && testHeight > 0))
    readonly property ShellScene scene: outputs.activeScene
    readonly property Surfaces surfaceWindows: outputs.instances.find(i => i.scene === scene)?.surfaces ?? null
    // The lowercase standalone entry has no named QML type for a typed Loader cast.
    readonly property var coverController: coverLoader.item

    Component.onCompleted: {
        Quickshell.watchFiles = false;
        UpdateService.enabled = valid && !headless && Quickshell.env("EMAKI_LIVE_SESSION") !== "1";
        if (!valid) {
            console.error("Emaki test shell: choose EMAKI_SHELL_BORDER=soft|full; dimensions/zone must be nonnegative; headless requires explicit test dimensions.");
            // Quickshell connects the engine's exit only after the root's onCompleted:
            // an exit from here is ignored, so defer it by one event-loop turn.
            Qt.callLater(Qt.exit, 1);
        } else if (!headless) {
            if (startup.coverActive)
                coverLoader.setSource(Qt.resolvedUrl("session-cover.qml"));
        }
    }
    // One writer for every output; also covers automatic schedule and timezone changes.
    property bool applicationPalettePending: true
    Connections {
        target: Appearance
        function onDarkChanged() { root.applicationPalettePending = true; }
        function onAccentChanged() { root.applicationPalettePending = true; }
    }
    Timer {
        interval: 100
        running: root.valid && !root.headless && root.applicationPalettePending && !applicationPalette.running
        onTriggered: {
            root.applicationPalettePending = false;
            applicationPalette.running = true;
        }
    }
    Process {
        id: applicationPalette
        command: [Quickshell.env("EMAKI_BIN") || "emaki", "settings", "sync-appearance"]
    }
    NiriService {
        id: coreService
        binary: Quickshell.env("EMAKI_BIN")
    }
    SessionStartup {
        id: startup
        modelsReady: outputs.modelsReady
        modelRevision: outputs.modelRevision
        dockRequired: outputs.shared.dockStore.on
        barMapped: outputs.barMapped
        dockMapped: outputs.dockMapped
        overlayMapped: outputs.overlayMapped
        onCoverActiveChanged: {
            if (!coverActive) {
                outputs.sealStartupMaterial();
                root.coverController?.complete();
            }
        }
    }
    WelcomeController {
        id: welcome
        enabled: root.valid && !root.headless
        ready: !startup.coverActive && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
        onOpening: outputs.closeAll()
        openShortcutList: () => root.scene?.openKeyboard("shortcuts") ?? false
    }
    ShellRecoveryNotice {
        store: outputs.shared.notifications
        ready: root.valid && !root.headless && !startup.coverActive && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
    }
    ReportController {
        id: report
        store: outputs.shared.notifications
        enabled: root.valid && !root.headless
        onOpening: outputs.closeAll()
        Component.onCompleted: ReportBridge.controller = report
    }
    SessionUpdateNotice {
        store: outputs.shared.notifications
        ready: root.valid && !root.headless && !startup.coverActive && !(root.scene?.pairingPeekOpen ?? false) && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
    }
    SecureBootNotice {
        store: outputs.shared.notifications
        ready: root.valid && !root.headless && !startup.coverActive && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
        onSettingsRequested: outputs.shared.settingsController.open("privacy")
    }
    IpcHandler {
        target: "workspaces"
        function focus(id: string): bool {
            return /^[0-9]+$/.test(id) && coreService.focusWorkspace(Number(id));
        }
    }
    ShellOutputs {
        id: outputs
        niri: coreService
        startup: startup
        enabled: root.valid
        firstRunPresent: welcome.opened
        requestedOutput: root.requestedOutput
        borderMode: root.borderMode
        reservedSpace: root.reservedSpace
        headless: root.headless
        testWidth: root.testWidth
        testHeight: root.testHeight
    }
    LazyLoader {
        active: root.valid && !root.headless
        PolkitPromptService {
            outputs: outputs
        }
    }
    LazyLoader {
        active: root.valid && !root.headless
        CaptureService {
            outputs: outputs
        }
    }
    LazyLoader {
        active: !root.headless && !startup.coverActive
        SnapshotRecovery {
            id: snapshotRecovery
            Binding {
                target: outputs.shared.services
                property: "recovery"
                value: snapshotRecovery
            }
        }
    }
    Loader {
        id: coverLoader
        onStatusChanged: if (status === Loader.Error)
            startup.finish()
        // The compositor owns startup coverage until this same-client cover is ready.
    }
    Connections {
        target: coverLoader.item
        function onRevealing(): void {
            outputs.sealStartupMaterial();
        }
        function onCompleted(): void {
            startup.finish();
        }
    }
    IpcHandler {
        target: "launcher"
        function open(): void {
            root.scene?.openLauncher(true);
        }
        function close(): void {
            outputs.closeAll();
        }
        function toggle(): void {
            root.scene?.toggleLauncher(true);
        }
        function mode(name: string): bool {
            return root.scene?.input.setMode(name) ?? false;
        }
        function query(text: string): void {
            root.scene?.input.setQuery(text);
        }
        function activate(): void {
            root.scene?.input.activate();
        }
        function status(): string {
            return root.scene?.status() ?? "{}";
        }
    }
    // Alt+Tab and its companions (niri/default.kdl). The first call opens the switcher, the
    // next ones move its selection; releasing the chord switches (WindowSwitcher).
    IpcHandler {
        target: "switcher"
        function next(): bool {
            return root.scene?.switchWindows(1, false, 0) ?? false;
        }
        function previous(): bool {
            return root.scene?.switchWindows(-1, false, 0) ?? false;
        }
        function app(): bool {
            return root.scene?.switchWindows(1, true, 0) ?? false;
        }
        function appPrevious(): bool {
            return root.scene?.switchWindows(-1, true, 0) ?? false;
        }
        // What the bindings call (emaki-shell): the same, with when the key was pressed (ms since
        // the epoch), so a press that arrives after its switcher closed is dropped.
        function step(direction: string, sent: string): bool {
            const moves = {
                next: [1, false],
                previous: [-1, false],
                app: [1, true],
                appPrevious: [-1, true]
            };
            // Object.hasOwn is not a function in Qt 6.12.0's QML engine (IV9, 2026-10-16).
            const move = Object.prototype.hasOwnProperty.call(moves, direction) ? moves[direction] : null;
            const at = Number(sent);
            return !!move && Number.isFinite(at) && (root.scene?.switchWindows(move[0], move[1], at) ?? false);
        }
        function cancel(): void {
            root.scene?.switcher.cancel();
        }
        function status(): string {
            return root.scene?.switcherStatus() ?? "{}";
        }
    }
    IpcHandler {
        target: "keyboard"
        function open(surface: string): bool {
            return root.scene?.openKeyboard(surface) ?? false;
        }
    }
    IpcHandler {
        target: "drawer"
        function open(): void {
            root.scene?.openKeyboard("clock");
        }
        function close(): void {
            outputs.closeAll();
        }
        function toggle(): void {
            if (root.scene?.drawerOpen)
                outputs.closeAll();
            else
                root.scene?.openKeyboard("clock");
        }
        function newest(): void {
            root.scene?.openKeyboard("notifications");
        }
        function dnd(enabled: bool): void {
            outputs.shared.notifications.setDnd(enabled);
        }
        function media(action: string): bool {
            return root.scene?.clockBody.mediaAction(action) ?? false;
        }
        function clear(): void {
            outputs.shared.notifications.dismiss(outputs.shared.notifications.entries.map(n => n.id));
        }
        function status(): string {
            return root.scene?.status() ?? "{}";
        }
    }
    IpcHandler {
        target: "system"
        function ping(): string {
            return "pong";
        }
        function open(page: string): bool {
            return root.scene?.openKeyboard(page) ?? false;
        }
        function close(): void {
            outputs.closeAll();
        }
        function status(): string {
            return root.scene?.status() ?? "{}";
        }
    }
    DockIpc {
        shared: outputs.shared
        scene: root.scene
    }
    IpcHandler {
        target: "review"
        function barAutoHide(value: bool): void {
            for (const instance of outputs.instances)
                instance.scene.barPolicy.autoHide = value;
        }
        function overviewWorkspaces(value: bool): void {
            for (const instance of outputs.instances)
                instance.scene.barPolicy.overviewWorkspaces = value;
        }
        function border(mode: string): bool {
            if (mode !== "soft" && mode !== "full")
                return false;
            root.borderMode = mode;
            return true;
        }
    }
}
