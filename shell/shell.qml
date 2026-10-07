pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "OutputSelection.js" as OutputSelection

ShellRoot {
    id: root
    property string borderMode: Quickshell.env("EMAKI_SHELL_BORDER")
    readonly property string requestedOutput: Quickshell.env("EMAKI_SHELL_OUTPUT")
    readonly property bool headless: Quickshell.env("EMAKI_SHELL_HEADLESS") === "1"
    readonly property int reservedSpace: Number(Quickshell.env("EMAKI_SHELL_EXCLUSIVE_ZONE") || String(Metrics.reservedSpace))
    readonly property int testWidth: Number(Quickshell.env("EMAKI_SHELL_TEST_WIDTH") || "0")
    readonly property int testHeight: Number(Quickshell.env("EMAKI_SHELL_TEST_HEIGHT") || "0")
    readonly property bool valid: ["soft", "full"].includes(borderMode) && reservedSpace >= 0 && testWidth >= 0 && testHeight >= 0 && (!headless || (testWidth > 0 && testHeight > 0))
    readonly property ShellScreen selectedOutput: OutputSelection.select(Quickshell.screens, requestedOutput)
    readonly property Surfaces surfaceWindows: surfacesLoader.item as Surfaces
    // The lowercase standalone entry has no named QML type for a typed Loader cast.
    readonly property var coverController: coverLoader.item

    Component.onCompleted: {
        Quickshell.watchFiles = false;
        if (!valid) {
            console.error("Emaki test shell: choose EMAKI_SHELL_BORDER=soft|full; dimensions/zone must be nonnegative; headless requires explicit test dimensions.");
            // Quickshell connects the engine's exit only after the root's onCompleted:
            // an exit from here is ignored, so defer it by one event-loop turn.
            Qt.callLater(Qt.exit, 1);
        } else if (!headless) {
            surfacesLoader.setSource(Qt.resolvedUrl("Surfaces.qml"), {
                controller: scene,
                startup: startup
            });
            if (startup.coverActive)
                coverLoader.setSource(Qt.resolvedUrl("session-cover.qml"));
        }
    }
    NiriService {
        id: coreService
        binary: Quickshell.env("EMAKI_BIN")
    }
    SessionStartup {
        id: startup
        modelsReady: scene.startupModelsReady && (root.surfaceWindows?.materialsReady ?? false)
        modelRevision: scene.startupRevision
        dockRequired: scene.dockStore.on
        barMapped: root.surfaceWindows?.barMapped ?? false
        dockMapped: root.surfaceWindows?.dockMapped ?? false
        overlayMapped: root.surfaceWindows?.overlayMapped ?? false
        onCoverActiveChanged: {
            if (!coverActive) {
                root.surfaceWindows?.sealStartupMaterial();
                root.coverController?.complete();
            }
        }
    }
    WelcomeController {
        enabled: root.valid && !root.headless
        ready: !startup.coverActive && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
        onOpening: scene.closeAll()
    }
    ShellRecoveryNotice {
        store: scene.notifications
        ready: root.valid && !root.headless && !startup.coverActive && (root.surfaceWindows?.barMapped ?? false) && (root.surfaceWindows?.overlayMapped ?? false)
    }
    IpcHandler {
        target: "workspaces"
        function focus(id: string): bool {
            return /^[0-9]+$/.test(id) && coreService.focusWorkspace(Number(id));
        }
    }
    ShellScene {
        id: scene
        niri: coreService
        skipIntro: startup.skipIntro
        enabled: root.valid
        output: root.selectedOutput
        borderMode: root.borderMode
        reservedSpace: root.reservedSpace
        headless: root.headless
        testWidth: root.testWidth
        testHeight: root.testHeight
    }
    Loader {
        id: surfacesLoader
        onStatusChanged: {
            if (status === Loader.Error) {
                console.error("Emaki test shell: failed to load Wayland surfaces.");
                Qt.exit(1); // nonzero: the session service restarts on failure
            }
        }
    }
    LazyLoader {
        active: !root.headless && !startup.coverActive
        SnapshotRecovery {}
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
            root.surfaceWindows?.sealStartupMaterial();
        }
        function onCompleted(): void {
            startup.finish();
        }
    }
    IpcHandler {
        target: "launcher"
        function open(): void {
            scene.openLauncher();
        }
        function close(): void {
            scene.closeAll();
        }
        function toggle(): void {
            scene.toggleLauncher();
        }
        function mode(name: string): bool {
            return scene.input.setMode(name);
        }
        function query(text: string): void {
            scene.input.setQuery(text);
        }
        function activate(): void {
            scene.input.activate();
        }
        function status(): string {
            return scene.status();
        }
    }
    IpcHandler {
        target: "drawer"
        function open(): void {
            scene.openDrawer();
        }
        function close(): void {
            scene.closeAll();
        }
        function toggle(): void {
            scene.toggleDrawer();
        }
        function dnd(enabled: bool): void {
            scene.notifications.dnd = enabled;
        }
        function media(action: string): bool {
            return scene.clockBody.mediaAction(action);
        }
        function clear(): void {
            scene.notifications.dismiss(scene.notifications.entries.map(n => n.id));
        }
        function status(): string {
            return scene.status();
        }
    }
    IpcHandler {
        target: "system"
        function open(page: string): bool {
            return scene.openSystem(page);
        }
        function close(): void {
            scene.closeAll();
        }
        function status(): string {
            return scene.status();
        }
    }
    DockIpc {
        scene: scene
    }
    IpcHandler {
        target: "review"
        function barAutoHide(value: bool): void {
            scene.barPolicy.autoHide = value;
        }
        function overviewWorkspaces(value: bool): void {
            scene.barPolicy.overviewWorkspaces = value;
        }
        function border(mode: string): bool {
            if (mode !== "soft" && mode !== "full")
                return false;
            root.borderMode = mode;
            return true;
        }
    }
}
