pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Ordinary Item for tests/render-shell.cpp. No session, real mic, or native service.
Item {
    id: root
    width: 1536
    height: 960
    property real level: .4
    property string samples: "idle"
    property int sampleCount: 0
    property int meterUpdates: 0
    property int startMilliseconds: 1000
    property int sampleMilliseconds: 16
    property real scrollPosition: 0
    // Freeze representative points of the open/close morph for pixel comparison.
    property real expansion: 1
    property bool cycleClip: false
    property int clipCycles: 0
    property int clipInterval: 1000
    readonly property Item retainedLayer: cycleClip ? panel.panelGlass : null
    // Optional probe data: the renderer samples this at both measurement edges.
    readonly property var renderStats: ({
            sample_count: sampleCount,
            meter_updates: meterUpdates,
            separate_meter: panel.separateMeter ?? false,
            panel_cache_enabled: panel.panelGlass.layer.enabled,
            meter_patch: panel.meterPatch ? [panel.meterPatch.x, panel.meterPatch.y, panel.meterPatch.width, panel.meterPatch.height] : null,
            panel_glass_rect: [panel.panelGlass.uOrigin.x, panel.panelGlass.uOrigin.y, panel.panelGlass.width, panel.panelGlass.height],
            viewport_size: [root.width, root.height],
            clip_cycles: clipCycles,
            scroll_position: panel.body.scroller.contentY
        })
    readonly property bool ready: backdrop.ready && !panel.animating && Math.abs(panel.heightSpring.x - panel.targetHeight) < .01
    readonly property alias panel: panel
    Connections {
        target: panel.body.micMeterItem ?? null
        ignoreUnknownSignals: true
        function onShownWidthChanged(): void {
            ++root.meterUpdates;
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "#514055"
    }
    Image {
        anchors.fill: parent
        source: Quickshell.env("EMAKI_FIXTURE_WALLPAPER")
        fillMode: Image.Stretch
    }
    SystemFixture {
        id: backend
        micLevel: root.level
    }
    SystemService {
        id: service
        backend: backend
        live: false
        helpersEnabled: false
    }
    NiriService {
        id: niri
        binary: ""
    }
    DockBackdrop {
        id: backdrop
        mode: "wallpaper"
        wallpaperTexture: Quickshell.env("EMAKI_FIXTURE_WALLPAPER")
        screenWidth: root.width
        screenHeight: root.height
        dpr: Screen.devicePixelRatio || 1
    }
    SystemPanel {
        id: panel
        service: service
        niri: niri
        page: "sound"
        opened: true
        expansion: root.expansion
        viewportWidth: root.width
        viewportHeight: root.height
        backdrop: backdrop
    }
    Binding {
        target: panel.body.scroller
        property: "contentY"
        value: root.scrollPosition
    }
    Timer {
        interval: root.clipInterval
        running: root.cycleClip && root.ready
        repeat: true
        onTriggered: {
            ++root.clipCycles;
            root.scrollPosition = root.clipCycles % 2 ? 125 : 0;
        }
    }
    Timer {
        interval: root.startMilliseconds
        running: root.samples !== "idle"
        onTriggered: samples.start()
    }
    Timer {
        id: samples
        interval: root.sampleMilliseconds
        repeat: true
        onTriggered: {
            ++root.sampleCount;
            if (root.samples === "varying")
                root.level = .5 + Math.sin(root.sampleCount * .13) * .45;
            else if (root.samples === "clamped")
                root.level = 1.1 + (root.sampleCount % 2) * .1;
            else if (root.samples === "constant")
                root.level = .4;
            else if (root.samples === "microjitter")
                root.level = .4 + (root.sampleCount % 2 ? 1e-7 : -1e-7);
        }
    }
}
