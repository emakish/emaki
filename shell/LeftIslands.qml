// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
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
    id: leftObject
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
    readonly property alias strip: stripObject
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
    readonly property rect workspaceRect: Qt.rect(Metrics.workspaceX, Metrics.top - 70 * workspaceShift, stripObject.implicitWidth + 2 * Metrics.workspaceInset, Metrics.islandHeight)
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
        running: leftObject.animating
        onTriggered: leftObject.frame(Date.now(), frameTime)
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        // The active workspace is a drop 32 × 42 that stands 3 px proud of the island and
        // 2 px beside its cell; hovering does not move it, only a real switch does.
        const position = stripObject.activePosition;
        if (niri.connected && position >= 0)
            active = Liquid.place(workspaceBubble, [stripObject.x - workspaceRect.x + stripObject.cellX(position) - 2, -3, Metrics.workspaceCell + 4, Metrics.islandHeight + 6], radius, now) || active;
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
        target: leftObject.strip
        function onActivePositionChanged(): void {
            leftObject.wake();
        }
        function onEntriesChanged(): void {
            leftObject.wake();
        }
    }
    Connections {
        target: leftObject.niri
        function onConnectedChanged(): void {
            leftObject.wake();
        }
    }
    onLogoHoveredChanged: wake()
    onSkipIntroChanged: wake()
    onLauncherPresentChanged: wake()
    Component.onCompleted: wake()
    // The drops in bar coordinates (Liquid.drop adds the island's origin).
    readonly property var logoDrops: {
        void tick;
        const d = Liquid.drop(logoBubble, logoRect.x, logoRect.y);
        return d ? [d] : [];
    }
    readonly property var workspaceDrops: {
        void tick;
        const d = Liquid.drop(workspaceBubble, workspaceRect.x, workspaceRect.y);
        return d ? [d] : [];
    }
    readonly property real logoBubbleAlpha: {
        void tick;
        return Liquid.onGlass(logoBubble);
    }

    // Ink follows the selected theme independently of the backdrop.
    readonly property color ink: LiquidPalette.ink
    readonly property color dim: LiquidPalette.plateDim
    readonly property color faint: LiquidPalette.faint

    // ---- Flat stand-in (no GPU): plate, drops, then the content on top ----
    component FlatPlate: Rectangle {
        required property rect r
        x: r.x
        y: r.y
        width: r.width
        height: r.height
        radius: leftObject.radius
        color: LiquidPalette.flatPlate
        visible: !leftObject.glassReady && opacity > .001
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
        visible: !leftObject.glassReady
    }
    FlatPlate {
        r: leftObject.logoRect
        opacity: leftObject.logoOpacity
        visible: !leftObject.glassReady && !leftObject.handedOver && opacity > .001
    }
    FlatPlate {
        r: leftObject.workspaceRect
        opacity: leftObject.workspaceOpacity
    }
    Repeater {
        model: leftObject.handedOver ? [] : leftObject.logoDrops
        FlatDrop {
            required property var modelData
            drop: modelData
        }
    }
    Repeater {
        model: leftObject.workspaceDrops
        FlatDrop {
            required property var modelData
            drop: modelData
            opacity: modelData.params.w * leftObject.workspaceOpacity
        }
    }

    // ---- Content under the glass: the arrow (fading while the bubble is up) and the digits ----
    Item {
        id: ink
        width: leftObject.workspaceRect.x + leftObject.workspaceRect.width + 16
        height: Metrics.top + Metrics.islandHeight + 16
        Icon {
            x: leftObject.logoRect.x + 9
            y: leftObject.logoRect.y + 9
            width: 18
            height: 18
            kind: "logo"
            ink: ShellPalette.accent
            opacity: (1 - leftObject.logoBubbleAlpha) * leftObject.logoOpacity
            visible: !leftObject.handedOver
        }
        WorkspaceStrip {
            id: stripObject
            // The strip origin lands on a whole device pixel so its digits and marks can be
            // pixel-exact at scale 1.25.
            x: leftObject.workspaceRect.x + leftObject.snapAt(leftObject.workspaceRect.x, Metrics.workspaceInset, leftObject.screenOrigin.x)
            y: leftObject.workspaceRect.y + leftObject.snapAt(leftObject.workspaceRect.y, 0, leftObject.screenOrigin.y)
            width: implicitWidth
            height: implicitHeight
            opacity: leftObject.workspaceOpacity
            niri: leftObject.niri
            outputName: leftObject.outputName
            ink: leftObject.ink
            dim: leftObject.dim
            faint: leftObject.faint
        }
    }
    ShaderEffectSource {
        id: inkTexture
        width: ink.width
        height: ink.height
        sourceItem: ink
        hideSource: leftObject.glassReady
        live: true
        visible: false
    }

    // ---- Glass: IslandGlass (launcher.js material) over the ink texture ----
    component BarGlass: IslandGlass {
        backdrop: leftObject.backdrop
        uIcons: inkTexture
        uSceneSize: Qt.point(ink.width, ink.height)
        uCover: leftObject.backdrop ? leftObject.backdrop.coverFor(leftObject.screenOrigin) : Qt.vector4d(0, 0, 1, 1)
        uRadius: leftObject.radius
    }
    BarGlass {
        id: logoGlass
        plate: leftObject.logoRect
        visible: leftObject.glassReady && !leftObject.handedOver && leftObject.logoOpacity > .001
        drops: leftObject.logoDrops
        uOpacity: leftObject.logoOpacity
    }
    BarGlass {
        id: workspaceGlass
        plate: leftObject.workspaceRect
        visible: leftObject.glassReady && leftObject.workspaceOpacity > .001
        drops: leftObject.workspaceDrops
        // The drop is small: no refraction or dispersion, a thin glass, a tight union so
        // the island's edges do not swell (24.09).
        uBulge: Qt.vector4d(5, 0, 1, 0)
        uRefraction: 0
        uDispersion: 0
        uThickness: 1.5
        uOpacity: leftObject.workspaceOpacity
    }
    // The arrow on the glass while the button is a bubble (drawn on the material, not
    // bent by the bubble's own edge).
    Icon {
        id: arrowOnGlass
        x: leftObject.logoRect.x + 9
        y: leftObject.logoRect.y + 9
        width: 18
        height: 18
        kind: "logo"
        ink: ShellPalette.accent
        opacity: leftObject.logoBubbleAlpha * leftObject.logoOpacity
        visible: !leftObject.handedOver && opacity > .001
    }

    // ---- Input: the islands themselves (also the surface's input mask) ----
    Item {
        id: logoHit
        KeyboardTarget {
            property bool keyboardFirst: true
            label: qsTranslate("LeftIslands", "Open launcher")
            onActivated: leftObject.launch()
        }
        x: leftObject.logoRect.x
        y: leftObject.logoRect.y
        width: leftObject.logoRect.width
        height: leftObject.logoRect.height
        opacity: leftObject.logoOpacity
        visible: !leftObject.launcherPresent && opacity > 0
        TipTarget {
            tip: leftObject.tip
            label: qsTranslate("LeftIslands", "Launcher")
            shortcut: "Super+D"
        }
        // Passive, like the tooltip's: a hovering MouseArea would take the hover from it.
        HoverHandler {
            onHoveredChanged: leftObject.logoHovered = hovered
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: leftObject.launch()
        }
        Accessible.role: Accessible.Button
        Accessible.name: qsTranslate("LeftIslands", "Open launcher")
        Accessible.onPressAction: leftObject.launch()
    }
    // Under the digits (z −1): the tooltip's TapHandler keeps a mouse press accepted (Qt 6.11
    // resets it for touch only, qquicktaphandler.cpp), and above the ink layer it took every
    // click from the digit cells — clicking a digit did not switch the workspace (27.09).
    Item {
        id: workspaceHit
        z: -1
        x: leftObject.workspaceRect.x
        y: leftObject.workspaceRect.y
        width: leftObject.workspaceRect.width
        height: leftObject.workspaceRect.height
        opacity: leftObject.workspaceOpacity
        visible: opacity > 0
        TipTarget {
            tip: leftObject.tip
            label: qsTranslate("LeftIslands", "Workspaces · scroll to switch")
        }
        // Wheel only (the digit cells above it have no wheel handler and pass it on).
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            onWheel: event => {
                event.accepted = true;
                leftObject.strip.wheel(event.angleDelta.y || event.pixelDelta.y);
            }
        }
    }
}
