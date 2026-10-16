// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "manager" as Manager
import "PolkitDetails.js" as Details

// Production surfaces with isolated, deterministic services and application data.
Item {
    id: root
    width: 1536
    height: 960
    property string shot: "launcher"
    property string theme: "light"
    property string accent: "orange"
    property string wallpaper: ""
    property bool configured: false
    // Optional real host data for presentation pictures (pins, open windows, prompt texts), as JSON.
    // Without it every shot keeps its built-in fixture data.
    readonly property var pictureData: {
        const text = Quickshell.env("EMAKI_PICTURE_DATA");
        return text ? JSON.parse(String(text)) : null;
    }
    readonly property bool ready: configured && !!surface.item && surface.item.ready
    readonly property var renderStats: ({
            theme: theme,
            accent: accent,
            shot: shot,
            dark: "dark" in ShellPalette ? ShellPalette.dark : false,
            accentColor: String(ShellPalette.accent),
            graphics_api: GraphicsInfo.api,
            textRasterizer: Quickshell.env("QML_DISABLE_DISTANCEFIELD") === "1" ? "native" : "distance-field",
            notificationVisible: ["notification", "reminder"].includes(root.shot) && !!surface.item && surface.item.fixtureScene.peekIds.length === 1
        })
    function applyPalette() {
        const rows = [
            {
                key: "appearance.theme",
                value: theme
            },
            {
                key: "appearance.accent",
                value: accent
            },
            {
                key: "appearance.light_time",
                value: "07:00"
            },
            {
                key: "appearance.dark_time",
                value: "19:00"
            }
        ].filter(row => row.value !== "");
        if (JSON.stringify(SettingsBridge.values) !== JSON.stringify(rows))
            SettingsBridge.values = rows;
    }
    Component.onCompleted: applyPalette()
    Timer {
        interval: 50
        running: true
        repeat: true
        onTriggered: root.applyPalette()
    }
    Image {
        anchors.fill: parent
        source: root.wallpaper
        fillMode: Image.PreserveAspectCrop
    }
    Loader {
        id: surface
        anchors.fill: parent
        sourceComponent: root.shot.startsWith("programs-") ? programsSurface : ["appearance", "settings"].includes(root.shot) ? settingsSurface : ["polkit", "lock", "snapshot", "shortcuts", "capture", "osd", "privacy", "welcome", "report"].includes(root.shot) ? extraSurface : desktopSurface
        onLoaded: root.configured = true
    }
    Component {
        id: desktopSurface
        ShellRender {
            id: desktop
            shot: ["notification", "reminder"].includes(root.shot) ? "bar" : root.shot === "settings-launcher" ? "launcher" : root.shot
            wallpaper: root.wallpaper
            textureScale: 1
            Timer {
                interval: 350
                running: true
                onTriggered: {
                    const scene = desktop.fixtureScene;
                    scene.dockStore.pinned = root.pictureData?.pins ?? ["fixture-browser", "emaki-update-manager", "fixture-files"];
                    if (root.pictureData?.windows)
                        scene.dockLabels.values = root.pictureData.windows;
                    // A playing app as the PipeWire stream names it, on the fixture's volume control.
                    const stream = root.pictureData?.stream;
                    const backend = scene.services.backend;
                    if (stream && backend)
                        backend.streams = Array.from(backend.streams).map(row => Object.assign({}, row, stream));
                    if (root.shot === "settings-launcher") {
                        scene.services.account = JSON.parse(Quickshell.env("EMAKI_FIXTURE_ACCOUNT"));
                        scene.input.showSettings("panel");
                    }
                    if (root.shot === "notification") {
                        scene.notifications.entries = [
                            {
                                id: 901,
                                app: "Calendar",
                                summary: "Design review",
                                body: "Your meeting starts in ten minutes.",
                                time: Date.now(),
                                object: null,
                                deadline: 0,
                                actions: []
                            }
                        ];
                        // Keep the real notification surface visible throughout capture.
                        scene.peekIds = [901];
                        scene.clockPresent = true;
                        scene.clockExpansion = 1;
                    }
                    if (root.shot === "reminder") {
                        // The update reminder as update-manager/reminder.py sends it: the app's
                        // icon by name, as a real notice carries it.
                        scene.notifications.entries = [
                            {
                                id: 902,
                                app: "Programs & Updates",
                                icon: "emaki-programs-updates",
                                summary: "Restart to finish the update",
                                body: "The new kernel and system services start after a restart. Save your work first.",
                                time: Date.now(),
                                object: null,
                                deadline: 0,
                                actions: []
                            }
                        ];
                        scene.peekIds = [902];
                        scene.clockPresent = true;
                        scene.clockExpansion = 1;
                    }
                }
            }
        }
    }
    Component {
        id: settingsSurface
        SettingsVisualTest {
            requestedPage: root.shot === "appearance" ? "appearance" : "panel"
        }
    }
    Component {
        id: programsSurface
        Item {
            id: manager
            readonly property bool ready: opened && view.children.some(item => item.objectName === "settings-glass" && item.ready)
            property bool opened: false
            Manager.PictureUpdates {
                id: updates
            }
            Manager.PicturePrograms {
                id: programs
            }
            Manager.ManagerView {
                id: view
                anchors {
                    fill: parent
                    margins: 48
                }
                controller: updates
                programs: programs
                wallpaperTexture: root.wallpaper
            }
            Timer {
                interval: 150
                running: true
                onTriggered: {
                    if (root.shot === "programs-program")
                        view.showProgram(programs.allRows.find(row => row.package === "krita"));
                    manager.opened = true;
                }
            }
        }
    }
    Component {
        id: extraSurface
        Item {
            id: extraRoot
            // The password prompt ignores input for its first second; capture it once it accepts input.
            readonly property bool ready: extra.status === Loader.Ready && (root.shot !== "lock" || extra.item.glassReady) && (root.shot !== "polkit" || extra.item.accepting) && glassSettled
            // Window glass (Welcome, Settings) shows a flat fill until its wallpaper and shader are in;
            // on llvmpipe that can outlast the warmup, so wait for every visible one.
            readonly property bool waitsForGlass: ["welcome", "report", "shortcuts", "polkit"].includes(root.shot)
            property bool glassSettled: !waitsForGlass
            function glassReady(item: var): bool {
                if (!item || !item.visible)
                    return true;
                // The password card's own glassReady is its clock-morph glass, which needs the
                // protected surface's backdrop; this fixture has none, and the grown card does
                // not draw it. Its window glass (the shared canvas) is checked below it.
                if (("glassReady" in item && !item.glassReady && !(item instanceof PolkitPromptCard)) || (item.objectName === "settings-glass" && !item.ready))
                    return false;
                return Array.from(item.children ?? []).every(child => extraRoot.glassReady(child));
            }
            Timer {
                interval: 50
                running: extraRoot.waitsForGlass && !extraRoot.glassSettled
                repeat: true
                onTriggered: extraRoot.glassSettled = extra.status === Loader.Ready && extraRoot.glassReady(extra.item)
            }
            Loader {
                id: extra
                anchors.fill: parent
                sourceComponent: root.shot === "polkit" ? polkitSurface : root.shot === "lock" ? lockSurface : root.shot === "snapshot" ? snapshotSurface : root.shot === "shortcuts" ? shortcutSurface : root.shot === "capture" ? captureSurface : root.shot === "osd" ? osdSurface : root.shot === "welcome" ? welcomeSurface : root.shot === "report" ? reportSurface : privacySurface
            }
        }
    }
    Component {
        id: polkitSurface
        Item {
            readonly property bool accepting: prompt.accepting
            QtObject {
                id: backend
                property var flow: null
                property bool responseRequired: true
                property bool passwordRequest: true
                property string requestKind: "polkit"
                property string iconName: ""
                property string accountName: root.pictureData?.polkit?.account ?? "emaki"
                // A real policy action goes through the production formatting of the polkit adapter.
                readonly property var request: root.pictureData?.polkit ?? null
                property string requestLine: request ? Details.headline(request.action, request.message, request.details, Platform.libexecDir) : "Install the selected program."
                property var detailRows: request ? Details.rows(request.action, request.message, request.details, "", {
                    binDir: Platform.binDir,
                    libexecDir: Platform.libexecDir,
                    libDir: Platform.libDir
                }) : [
                    {
                        label: "Action",
                        value: "org.emaki.programs.install"
                    }
                ]
                signal rejected
                signal interrupted
                function cancel(reason) {
                }
                function submit(value) {
                }
            }
            PolkitPromptController {
                id: prompt
                backend: backend
                locked: false
            }
            PolkitPromptCard {
                controller: prompt
                viewportWidth: root.width
                viewportHeight: root.height
                reducedMotion: true
                sudoKeyInstalled: false
                wallpaperTexture: root.wallpaper
            }
            Timer {
                interval: 100
                running: true
                onTriggered: {
                    backend.flow = {};
                    prompt.sync();
                }
            }
        }
    }
    Component {
        id: lockSurface
        LockSurface {
            id: lock
            auth: AuthController {}
            environment: LockEnvironment {
                layout: "English (US)"
            }
            session: LockSession {
                auth: lock.auth
                started: false
                active: false
                secure: true
                phase: "locked"
                clock: 10
                elapsed: 10
                reducedMotion: true
            }
        }
    }
    Component {
        id: snapshotSurface
        Item {
            SnapshotPrompt {
                anchors.centerIn: parent
                width: 460
                height: implicitHeight
                snapshot: "42"
            }
        }
    }
    Component {
        id: shortcutSurface
        Item {
            ShortcutSheet {
                anchors.centerIn: parent
                width: 760
                height: 620
                loadState: "ready"
                wallpaperTexture: root.wallpaper
                rows: root.pictureData?.shortcuts ?? [
                    {
                        keys: "Super + Space",
                        description: "Open launcher"
                    },
                    {
                        keys: "Super + Return",
                        description: "Open terminal"
                    },
                    {
                        keys: "Super + Q",
                        description: "Close window"
                    }
                ]
            }
        }
    }
    Component {
        id: captureSurface
        CapturePanel {
            controller: CaptureController {
                enabled: false
                locked: false
                opened: true
                ready: true
                outputName: "Display"
                target: "area"
                area: Qt.rect(380, 220, 700, 400)
                outputs: [
                    {
                        name: "Display",
                        width: 1536,
                        height: 960
                    }
                ]
            }
        }
    }
    Component {
        id: osdSurface
        Item {
            DockBackdrop {
                id: backdrop
                mode: "wallpaper"
                wallpaperTexture: root.wallpaper
                screenWidth: root.width
                screenHeight: root.height
                dpr: 1
            }
            Osd {
                x: root.width - 290
                y: 100
                backdrop: backdrop
                kind: "sound"
                value: 64
                label: "Speakers"
                shown: true
                deadline: Date.now() + 600000
            }
        }
    }
    Component {
        id: privacySurface
        Item {
            DockBackdrop {
                id: backdrop
                mode: "wallpaper"
                wallpaperTexture: root.wallpaper
                screenWidth: root.width
                screenHeight: root.height
                dpr: 1
            }
            PrivacyPanel {
                backdrop: backdrop
                opened: true
                expansion: 1
                viewportWidth: root.width
                viewportHeight: root.height
                kinds: ["mic"]
                rows: root.pictureData?.privacy ?? [
                    {
                        what: "mic",
                        label: "Recorder",
                        note: "Microphone",
                        tag: "Audio"
                    }
                ]
            }
        }
    }
    Component {
        id: welcomeSurface
        Item {
            Welcome {
                anchors.centerIn: parent
                width: 800
                height: 660
                wallpaperTexture: root.wallpaper
                page: root.pictureData?.welcomePage ?? 0
            }
        }
    }
    Component {
        id: reportSurface
        // The Report a problem window's content, with a composed report in place of the helper.
        Item {
            QtObject {
                id: reportState
                readonly property var parts: ["shell", "update", "install", "other"]
                readonly property int whatLimit: 2000
                property string part: report.part ?? "other"
                property string state: "ready"
                property var report: root.pictureData?.report ?? ({
                        part: "other",
                        items: [],
                        removed: {}
                    })
                property var items: report.items ?? []
                property var chosen: items.reduce((chosen, item) => Object.assign(chosen, {
                        [item.id]: item.default === true
                    }), {})
                property string what: ""
                property bool previewOpen: false
                property string preview: ""
                property bool previewStale: false
                property string message: ""
                property bool sent: false
                property bool busy: false
                function compose(): void {
                }
                function send(): void {
                }
                function close(): void {
                }
                function setChosen(id: string, value: bool): void {
                }
                function setWhat(text: string): void {
                }
                function togglePreview(): void {
                }
            }
            ReportView {
                anchors.centerIn: parent
                width: 720
                height: 700
                controller: reportState
                wallpaperTexture: root.wallpaper
            }
        }
    }
}
