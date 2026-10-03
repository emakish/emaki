pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// What the privacy pill opens, on liquid glass: one Regular plate that grows out of the pill
// (as the clock and system panels grow out of their islands) to 300 px, its right edge at the
// pill's, so the pill's line keeps its pixels as the panel's head. Under it "In use right now"
// and one row per use — the symbol in the alarm colour, the app or the screen, what it uses.
// Information only: the pill's place pressed again, Esc, a press outside or the overview close
// it, and it shrinks back into the pill. Without a GPU the same layers are drawn flat.
// The item is the plate's rectangle on the screen (its parent sits at the screen origin): the
// overlay's input and blur regions and the status read it.
Item {
    id: panel
    property var rows: []
    property bool opened: false
    // The scene's morph (0..1).
    property real expansion: 0
    property real viewportWidth: 1536
    property real viewportHeight: 960
    // What the glass refracts (Surfaces sets it; headless: flat stand-in).
    property DockBackdrop backdrop: null
    // The pill: where the plate starts (screen), its line (kinds, the dot's pulse) and its
    // hover drop (pill-local, carried while the morph is young).
    property real islandX: viewportWidth - 400
    property real islandWidth: 49
    property var islandBubble: null
    property var kinds: []
    property real pulse: 1
    signal toggle
    // For tests/glass-shots.py.
    readonly property alias panelGlass: panelGlass
    readonly property alias contentLayer: layer
    readonly property alias onGlassLayer: onGlass
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property real dpr: Screen.devicePixelRatio || 1

    // ---- Geometry, screen coordinates ----
    readonly property real morph: Math.max(0, Math.min(1, expansion))
    readonly property real targetWidth: Math.min(300, viewportWidth - 20)
    // The grown plate's right edge is the pill's, on a whole device pixel (8 px from the
    // screen's left edge at the least, as the list was).
    readonly property real frameX: Math.max(8, Math.round((islandX + islandWidth - targetWidth) * dpr) / dpr)
    readonly property real maxHeight: Math.max(Metrics.islandHeight, viewportHeight - 2 * Metrics.top)
    // The head (the pill's 36 px line), then the rows from 44 on, 8 below the last.
    readonly property real targetHeight: Math.min(maxHeight, 44 + rows.length * 46 + 8)
    readonly property var heightSpring: Liquid.spring(Metrics.islandHeight)
    // Opening from the pill: the plate grows straight to its height; the spring only takes
    // later changes (an app starting or stopping a capture while the panel is open).
    function snapIfClosed(): void {
        if (morph >= .001)
            return;
        heightSpring.x = targetHeight;
        heightSpring.v = 0;
        ++tick;
    }
    onOpenedChanged: {
        snapIfClosed();
        wake();
    }
    onMorphChanged: wake()
    onTargetHeightChanged: wake()
    readonly property real shownWidth: islandWidth + (targetWidth - islandWidth) * morph
    readonly property real shownHeight: {
        tick;
        return Metrics.islandHeight + (Math.max(Metrics.islandHeight, heightSpring.x) - Metrics.islandHeight) * morph;
    }
    readonly property real shownX: islandX + (frameX - islandX) * morph
    readonly property real shownRadius: Liquid.mix(Metrics.islandRadius, Metrics.panelRadius, morph)
    readonly property real radius: shownRadius
    // The list arrives on the second half of the morph; the pill's line stays whole.
    readonly property real contentAlpha: Liquid.smooth((morph - .55) / .45)
    x: shownX
    y: Metrics.top
    width: shownWidth
    height: shownHeight

    // ---- Motion: the height spring and the pill's drop while the morph is young ----
    property int tick: 0
    property bool animating: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: panel.animating
        onTriggered: panel.frame(frameTime)
    }
    function frame(elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        heightSpring.target = targetHeight;
        // Closed: the next morph starts from the right size, not from the last one.
        snapIfClosed();
        let active = Liquid.step(heightSpring, dt, 420, .8);
        // The pill advances its drop; keep drawing it while the morph is young.
        if (islandBubble && islandBubble.alpha.x > .001 && morph > 0 && morph < .6)
            active = true;
        ++tick;
        if (!active)
            animating = false;
    }
    readonly property var drops: {
        tick;
        if (morph >= .6 || !islandBubble)
            return [];
        const d = Liquid.drop(islandBubble, islandX, Metrics.top);
        return d ? [d] : [];
    }
    readonly property real islandAlpha: {
        tick;
        return islandBubble && morph < .6 ? Liquid.smooth(islandBubble.alpha.x) : 0;
    }
    function glassStatus(): var {
        return {
            ready: glassReady,
            width: Math.round(shownWidth),
            height: Math.round(shownHeight),
            drops: drops.length
        };
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color alarm: LiquidPalette.dangerOnLight
    readonly property var symbols: ({
            mic: "audio-input-microphone-symbolic",
            cam: "camera-web-symbolic",
            cast: "screen-shared-symbolic"
        })

    // ---- Input on the plate: presses stay in the panel; the pill's place closes it ----
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }
    MouseArea {
        x: panel.islandX - panel.shownX
        width: panel.islandWidth
        height: Metrics.islandHeight
        cursorShape: Qt.PointingHandCursor
        onClicked: panel.toggle()
    }

    // ---- Flat stand-in (no GPU): the plate and the drop, under the content ----
    Rectangle {
        visible: !panel.glassReady
        width: panel.shownWidth
        height: panel.shownHeight
        radius: panel.shownRadius
        color: LiquidPalette.flatPlate
    }
    Repeater {
        model: panel.glassReady ? [] : panel.drops
        Rectangle {
            required property var modelData
            x: modelData.rect.x - panel.shownX
            y: modelData.rect.y - Metrics.top
            width: modelData.rect.z
            height: modelData.rect.w
            radius: modelData.params.x
            opacity: modelData.params.w
            color: LiquidPalette.flatDrop
            border.width: 1
            border.color: LiquidPalette.flatDropRim
        }
    }

    // ---- Under the glass. The layer starts at the screen origin, so its texture lies on
    // whole device pixels; the pill's line sits at the pill, the list in its frame ----
    Item {
        id: layer
        x: -panel.shownX
        y: -Metrics.top
        width: Math.max(panel.frameX + panel.targetWidth, panel.islandX + panel.islandWidth) + 16
        height: Metrics.top + panel.targetHeight + 16
        PrivacyCompactRow {
            x: panel.islandX
            y: Metrics.top
            kinds: panel.kinds
            pulse: panel.pulse
            alarm: panel.alarm
            opacity: 1 - panel.islandAlpha
            visible: opacity > .001
        }
        Item {
            // Flat stand-in only: the list ends where the plate does.
            x: panel.shownX
            y: Metrics.top
            width: panel.shownWidth
            height: panel.shownHeight
            clip: !panel.glassReady
            Item {
                x: panel.frameX - panel.shownX
                width: panel.targetWidth
                height: panel.targetHeight
                opacity: panel.contentAlpha
                visible: opacity > .001
                // On the head's line, left of the pill.
                Text {
                    x: 22
                    y: (Metrics.islandHeight - height) / 2
                    height: 20
                    verticalAlignment: Text.AlignVCenter
                    text: "IN USE RIGHT NOW"
                    textFormat: Text.PlainText
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: panel.dim
                }
                Repeater {
                    model: panel.rows
                    Item {
                        id: use
                        required property var modelData
                        required property int index
                        x: 22
                        y: 44 + index * 46
                        width: panel.targetWidth - 44
                        height: 44
                        Item {
                            y: 12
                            width: 20
                            height: 20
                            SymbolIcon {
                                id: symbol
                                width: 20
                                height: 20
                                name: panel.symbols[use.modelData.what] ?? ""
                                ink: panel.alarm
                            }
                            Icon {
                                visible: !symbol.found
                                width: 20
                                height: 20
                                kind: use.modelData.what || "mic"
                                ink: panel.alarm
                            }
                        }
                        Text {
                            x: 34
                            y: 4
                            width: parent.width - 34 - tag.width - 8
                            height: 20
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            text: use.modelData.label
                            textFormat: Text.PlainText
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 14
                            font.weight: Font.Medium
                            color: panel.ink
                        }
                        Text {
                            x: 34
                            y: 23
                            width: parent.width - 34
                            height: 18
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            text: use.modelData.note
                            textFormat: Text.PlainText
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 13
                            font.weight: Font.Medium
                            color: panel.dim
                        }
                        Text {
                            id: tag
                            x: parent.width - width
                            y: 4
                            height: 20
                            verticalAlignment: Text.AlignVCenter
                            text: use.modelData.tag
                            textFormat: Text.PlainText
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 11
                            font.weight: Font.Medium
                            color: panel.dim
                        }
                    }
                }
            }
        }
    }
    ShaderEffectSource {
        id: contentTexture
        width: layer.width
        height: layer.height
        sourceItem: layer
        hideSource: panel.glassReady
        live: true
        visible: false
    }

    // ---- The glass itself ----
    IslandGlass {
        id: panelGlass
        visible: panel.glassReady
        backdrop: panel.backdrop
        sceneOffset: Qt.point(panel.shownX, Metrics.top)
        plate: Qt.rect(panel.shownX, Metrics.top, panel.shownWidth, panel.shownHeight)
        drops: panel.drops
        uRadius: panel.shownRadius
        uIcons: contentTexture
        uSceneSize: Qt.point(layer.width, layer.height)
        uCover: panel.backdrop ? panel.backdrop.coverFor(Qt.point(0, 0)) : Qt.vector4d(0, 0, 1, 1)
    }

    // ---- On the glass: the pill's line while the pill's drop rides along ----
    Item {
        id: onGlass
        x: -panel.shownX
        y: -Metrics.top
        width: layer.width
        height: layer.height
        PrivacyCompactRow {
            x: panel.islandX
            y: Metrics.top
            kinds: panel.kinds
            pulse: panel.pulse
            alarm: panel.alarm
            opacity: panel.islandAlpha
            visible: opacity > .001
        }
    }
}
