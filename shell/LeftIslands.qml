pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// The two left islands of the bar on liquid glass (docs/mockups/liquid-glass/launcher.html,
// BAR): the launcher button, a 36 px Regular island with the Arch arrow (the one colour in
// the bar), and the workspaces island, whose active workspace is a Clear drop that flows
// to the next digit. Hovering the button turns it into a Clear bubble. Both draw through
// the shared GlassShape (shaders/dock.frag) like the dock; the digits and the arrow lie
// under the glass (uIcons), the arrow is drawn again on the glass while the bubble is up.
// Without a GPU (software Qt, headless tests) a flat stand-in is drawn instead.
// Coordinates: the bar's, which are screen coordinates offset by `screenOrigin`.
Item {
    id: left
    property bool skipIntro: false
    required property NiriService niri
    required property string outputName
    required property BarPolicy policy
    property HoverTip tip: null
    // Surfaces hands in the top surface's backdrop on Wayland; headless it stays null.
    property DockBackdrop backdrop: null
    property point screenOrigin: Qt.point(0, 0)
    property bool launcherPresent: false
    // The launcher panel growing over the islands: its right edge and its morph (0..1).
    property real launcherRight: 0
    property real launcherExpansion: 0
    // The button stays drawn under the growing panel (another window, above it) until the
    // panel is half open: hiding it on launcherPresent left 30–60 ms with neither the button
    // nor the panel on screen — the overlay's frames reach the screen a few frames after the
    // bar's (27.09, VM bursts).
    readonly property bool handedOver: launcherPresent && launcherExpansion > .5
    // Overview: the button slides up 70 px and fades; the workspaces stay unless the
    // overview hides them (BarPolicy).
    property real shift: 0
    property real workspaceShift: 0
    signal launch
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property alias logo: logoHit
    readonly property alias workspaces: workspaceHit
    readonly property alias strip: strip
    // For tests/glass-shots.py: the glass draws and the layers under and on them.
    readonly property alias logoGlass: logoGlass
    readonly property alias workspaceGlass: workspaceGlass
    readonly property alias inkLayer: ink
    readonly property alias arrowOnGlass: arrowOnGlass
    readonly property var logoBubble: Liquid.liquid()
    readonly property var workspaceBubble: Liquid.liquid()
    readonly property int radius: Metrics.islandRadius
    readonly property real dpr: Screen.devicePixelRatio || 1
    // Offset from `base` (bar coordinates) such that base + offset lands on a whole device
    // pixel of the screen; `origin` is the bar's own screen offset on that axis.
    function snapAt(base: real, offset: real, origin: real): real {
        return Math.round((origin + base + offset) * dpr) / dpr - origin - base;
    }

    // ---- Geometry ----
    readonly property rect logoRect: Qt.rect(Metrics.logoX, Metrics.top - 70 * shift, Metrics.islandHeight, Metrics.islandHeight)
    readonly property real logoOpacity: 1 - shift
    // 8 px on both sides of the cells. The mockup's island ended flush with the last cell
    // (width n·30 + 6); the right inset is made equal to the left one.
    readonly property rect workspaceRect: Qt.rect(Metrics.workspaceX, Metrics.top - 70 * workspaceShift, strip.implicitWidth + 2 * Metrics.workspaceInset, Metrics.islandHeight)
    // launcher.js surfaces(): the islands fade as the panel reaches them.
    readonly property real workspaceOpacity: (1 - Liquid.clamp((launcherRight - workspaceRect.x) / 60, 0, 1) * Liquid.smooth(launcherExpansion * 2.5)) * (1 - workspaceShift)
    function glassStatus(): var {
        const rect = b => ({
                    alpha: Math.round(Liquid.smooth(b.alpha.x) * 1000) / 1000,
                    x: Math.round(b.x.x),
                    y: Math.round(b.y.x),
                    width: Math.round(b.w.x),
                    height: Math.round(b.h.x)
                });
        return {
            ready: glassReady,
            // Bubble rects are island-local (the button's, the workspaces island's).
            logo: rect(logoBubble),
            workspace: rect(workspaceBubble),
            workspace_island: {
                x: workspaceRect.x,
                width: workspaceRect.width
            }
        };
    }

    // ---- Motion (launcher.js frame) ----
    property int tick: 0
    property bool animating: false
    property bool logoHovered: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: left.animating
        onTriggered: left.frame(Date.now(), frameTime)
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        // The active workspace is a drop 32 × 42 that stands 3 px proud of the island and
        // 2 px beside its cell; hovering does not move it, only a real switch does.
        const position = strip.activePosition;
        if (niri.connected && position >= 0)
            active = Liquid.place(workspaceBubble, [strip.x - workspaceRect.x + strip.cellX(position) - 2, -3, Metrics.workspaceCell + 4, Metrics.islandHeight + 6], radius, now) || active;
        else
            active = Liquid.vanish(workspaceBubble, now) || active;
        if (logoHovered && !launcherPresent)
            active = Liquid.place(logoBubble, Liquid.pad([0, 0, Metrics.islandHeight, Metrics.islandHeight], 3), radius + 3, now) || active;
        else
            Liquid.dissolve(logoBubble, now);
        if (skipIntro) {
            for (const bubble of [workspaceBubble, logoBubble]) {
                for (const axis of [bubble.x, bubble.y, bubble.w, bubble.h]) {
                    axis.x = axis.target;
                    axis.v = 0;
                }
                bubble.alpha.x = bubble.alpha.from = bubble.alpha.target;
            }
        }
        active = Liquid.advance(workspaceBubble, now, dt) || active;
        active = Liquid.advance(logoBubble, now, dt) || active;
        ++tick;
        if (!active)
            animating = false;
    }
    Connections {
        target: left.strip
        function onActivePositionChanged(): void {
            left.wake();
        }
        function onEntriesChanged(): void {
            left.wake();
        }
    }
    Connections {
        target: left.niri
        function onConnectedChanged(): void {
            left.wake();
        }
    }
    onLogoHoveredChanged: wake()
    onSkipIntroChanged: wake()
    onLauncherPresentChanged: wake()
    Component.onCompleted: wake()
    // The drops in bar coordinates (Liquid.drop adds the island's origin).
    readonly property var logoDrops: {
        tick;
        const d = Liquid.drop(logoBubble, logoRect.x, logoRect.y);
        return d ? [d] : [];
    }
    readonly property var workspaceDrops: {
        tick;
        const d = Liquid.drop(workspaceBubble, workspaceRect.x, workspaceRect.y);
        return d ? [d] : [];
    }
    readonly property real logoBubbleAlpha: {
        tick;
        return Liquid.smooth(logoBubble.alpha.x);
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color faint: LiquidPalette.faintOnLight

    // ---- Flat stand-in (no GPU): plate, drops, then the content on top ----
    component FlatPlate: Rectangle {
        required property rect r
        x: r.x
        y: r.y
        width: r.width
        height: r.height
        radius: left.radius
        color: LiquidPalette.flatPlate
        visible: !left.glassReady && opacity > .001
    }
    component FlatDrop: Rectangle {
        required property var drop
        x: drop.rect.x
        y: drop.rect.y
        width: drop.rect.z
        height: drop.rect.w
        radius: drop.params.x
        opacity: drop.params.w
        color: LiquidPalette.flatDrop
        border.width: 1
        border.color: LiquidPalette.flatDropRim
        visible: !left.glassReady
    }
    FlatPlate {
        r: left.logoRect
        opacity: left.logoOpacity
        visible: !left.glassReady && !left.handedOver && opacity > .001
    }
    FlatPlate {
        r: left.workspaceRect
        opacity: left.workspaceOpacity
    }
    Repeater {
        model: left.handedOver ? [] : left.logoDrops
        FlatDrop {
            required property var modelData
            drop: modelData
        }
    }
    Repeater {
        model: left.workspaceDrops
        FlatDrop {
            required property var modelData
            drop: modelData
            opacity: modelData.params.w * left.workspaceOpacity
        }
    }

    // ---- Content under the glass: the arrow (fading while the bubble is up) and the digits ----
    Item {
        id: ink
        width: left.workspaceRect.x + left.workspaceRect.width + 16
        height: Metrics.top + Metrics.islandHeight + 16
        Icon {
            x: left.logoRect.x + 9
            y: left.logoRect.y + 9
            width: 18
            height: 18
            kind: "logo"
            ink: ShellPalette.accent
            opacity: (1 - left.logoBubbleAlpha) * left.logoOpacity
            visible: !left.handedOver
        }
        WorkspaceStrip {
            id: strip
            // The strip origin lands on a whole device pixel so its digits and marks can be
            // pixel-exact at scale 1.25.
            x: left.workspaceRect.x + left.snapAt(left.workspaceRect.x, Metrics.workspaceInset, left.screenOrigin.x)
            y: left.workspaceRect.y + left.snapAt(left.workspaceRect.y, 0, left.screenOrigin.y)
            width: implicitWidth
            height: implicitHeight
            opacity: left.workspaceOpacity
            niri: left.niri
            outputName: left.outputName
            ink: left.ink
            dim: left.dim
            faint: left.faint
        }
    }
    ShaderEffectSource {
        id: inkTexture
        width: ink.width
        height: ink.height
        sourceItem: ink
        hideSource: left.glassReady
        live: true
        visible: false
    }

    // ---- Glass: IslandGlass (launcher.js material) over the ink texture ----
    component BarGlass: IslandGlass {
        backdrop: left.backdrop
        uIcons: inkTexture
        uSceneSize: Qt.point(ink.width, ink.height)
        uCover: left.backdrop ? left.backdrop.coverFor(left.screenOrigin) : Qt.vector4d(0, 0, 1, 1)
        uRadius: left.radius
    }
    BarGlass {
        id: logoGlass
        plate: left.logoRect
        visible: left.glassReady && !left.handedOver && left.logoOpacity > .001
        drops: left.logoDrops
        uOpacity: left.logoOpacity
    }
    BarGlass {
        id: workspaceGlass
        plate: left.workspaceRect
        visible: left.glassReady && left.workspaceOpacity > .001
        drops: left.workspaceDrops
        // The drop is small: no refraction or dispersion, a thin glass, a tight union so
        // the island's edges do not swell (24.09).
        uBulge: Qt.vector4d(5, 0, 1, 0)
        uRefraction: 0
        uDispersion: 0
        uThickness: 1.5
        uOpacity: left.workspaceOpacity
    }
    // The arrow on the glass while the button is a bubble (drawn on the material, not
    // bent by the bubble's own edge).
    Icon {
        id: arrowOnGlass
        x: left.logoRect.x + 9
        y: left.logoRect.y + 9
        width: 18
        height: 18
        kind: "logo"
        ink: ShellPalette.accent
        opacity: left.logoBubbleAlpha * left.logoOpacity
        visible: !left.handedOver && opacity > .001
    }

    // ---- Input: the islands themselves (also the surface's input mask) ----
    Item {
        id: logoHit
        x: left.logoRect.x
        y: left.logoRect.y
        width: left.logoRect.width
        height: left.logoRect.height
        opacity: left.logoOpacity
        visible: !left.launcherPresent && opacity > 0
        TipTarget {
            tip: left.tip
            label: "Launcher"
            shortcut: "Super+D"
        }
        // Passive, like the tooltip's: a hovering MouseArea would take the hover from it.
        HoverHandler {
            onHoveredChanged: left.logoHovered = hovered
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: left.launch()
        }
        Accessible.role: Accessible.Button
        Accessible.name: "Open launcher"
        Accessible.onPressAction: left.launch()
    }
    // Under the digits (z −1): the tooltip's TapHandler keeps a mouse press accepted (Qt 6.11
    // resets it for touch only, qquicktaphandler.cpp), and above the ink layer it took every
    // click from the digit cells — clicking a digit did not switch the workspace (27.09).
    Item {
        id: workspaceHit
        z: -1
        x: left.workspaceRect.x
        y: left.workspaceRect.y
        width: left.workspaceRect.width
        height: left.workspaceRect.height
        opacity: left.workspaceOpacity
        visible: opacity > 0
        TipTarget {
            tip: left.tip
            label: "Workspaces · scroll to switch"
        }
        // Wheel only (the digit cells above it have no wheel handler and pass it on).
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            onWheel: event => {
                event.accepted = true;
                left.strip.wheel(event.angleDelta.y || event.pixelDelta.y);
            }
        }
    }
}
