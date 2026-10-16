// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Widgets
import "Switcher.js" as Switcher

// Alt+Tab: a centered panel on the Settings glass with a picture of every window, most recent
// first, its icon and title under it. Holding Alt and pressing Tab (or Shift+Tab) moves the
// selection; releasing Alt focuses the chosen window, on whatever workspace it is; Esc closes
// without switching; a click picks a window. The binding reaches the shell through IPC
// (step()), the panel takes the keyboard, and when the keyboard arrives it asks which
// modifiers are still held (Quickshell.queryKeyboardModifiers, quickshell-emaki 0009): a quick
// tap released before that switches to the previous window at once, with no panel at all.
// Pictures: a live cut-out of this output's capture for windows on its screen, the last cut-out
// for the others (kept while the shell runs), the application's icon when there is none.
FocusScope {
    id: switcher
    objectName: "window-switcher"
    required property NiriService niri
    required property WindowLabels labels
    required property AppIdentity identity
    property real viewportWidth: 1280
    property real viewportHeight: 800
    property string outputName: ""
    property string wallpaperTexture: ""
    property real dpr: 1
    // The capture the live pictures are cut from (SwitcherCapture on Wayland, a stand-in in
    // tests); null or not ready: recent pictures and icons only.
    property Item pictureSource: null
    property bool pictureSourceReady: false
    // Quickshell.queryKeyboardModifiers() of quickshell-emaki 0009; -1 where it is missing, and
    // the switcher then switches at once, as Alt+Tab did before it existed.
    property var modifierProbe: () => typeof Quickshell.queryKeyboardModifiers === "function" ? Quickshell.queryKeyboardModifiers() : -1
    // A quick tap shows nothing; a held chord shows the panel this long after the keyboard
    // arrives (a release in between still switches, without a flash).
    property int revealDelay: 40
    // Keyboard that never arrives (another surface holds it): switch as a tap would.
    property int keyboardTimeout: 400

    // Keep OnDemand until niri confirms the target. Its focus action transfers the keyboard
    // atomically; releasing the layer first would expose the window just left.
    property int handoffTimeout: 150

    property bool armed: false
    property bool shown: false
    property bool keyboardReady: false
    // The cards exist once the chord is known to be held: a quick tap builds none.
    property bool populated: false
    property bool appOnly: false
    property var rows: []
    property int index: -1
    // {id: rect} of the windows whose pixels the capture shows, fixed when the switcher opens.
    property var live: ({})
    // {id: {result, width, height}}: each window's last live picture, kept while the shell runs.
    property var recent: ({})
    // The last outcome, for tests and status: "", "switched", "cancelled", "stayed".
    property string outcome: ""
    // When the last switcher closed (Date.now()): a binding call sent before it belonged to it.
    property double closedAt: 0
    // The window being focused after a switch, until niri reports it (-1: none).
    property double pendingFocus: -1
    property int focusTicket: -1
    property var focusSnapshot: null
    // Where the pointer was when the panel appeared: only a real move selects by hovering.
    property point pointerOrigin: Qt.point(-1, -1)
    readonly property alias panel: panel
    readonly property alias cards: cards
    readonly property int count: rows.length
    readonly property var current: index >= 0 && index < rows.length ? rows[index] : null
    // The panel is on screen or fading out: its live pictures still need the capture.
    readonly property bool presenting: panel.visible
    readonly property bool handedOff: pendingFocus >= 0 && niri.windows !== focusSnapshot && niri.windows.some(w => w.id === pendingFocus && w.is_focused)
    onHandedOffChanged: if (handedOff)
        Qt.callLater(() => {
            if (handedOff)
                finishHandoff();
        })
    signal finished
    signal focusRequested(double id)

    // Alt+Tab (delta 1), Alt+Shift+Tab (-1); sameApp: Alt+` lists the focused application only.
    // The first call opens the switcher; later ones move the selection, and since the binding
    // needs its modifier, they also prove it is held. A call during a hand-off is a new chord
    // that must acquire its own keyboard focus.
    function step(delta: int, sameApp: bool): bool {
        if (!armed) {
            const holding = pendingFocus >= 0;
            const list = Switcher.entries(niri.windows, labels.values, labels.focusTimes, sameApp, holding ? pendingFocus : -1);
            if (!list.length)
                return false;
            handoff.stop();
            pendingFocus = -1;
            focusTicket = -1;
            outcome = "";
            appOnly = sameApp;
            rows = list;
            index = Switcher.start(list, delta);
            live = Switcher.visible(niri.windows, niri.workspaces, outputName, viewportWidth, viewportHeight, niri.overviewOpen, labels.positions);
            pruneRecent();
            keyboardReady = false;
            populated = false;
            shown = false;
            pointerOrigin = Qt.point(-1, -1);
            armed = true;
            syncCards();
            keyboardDeadline.restart();
            return true;
        }
        index = Switcher.step(index, rows.length, delta);
        if (keyboardReady)
            reveal();
        return true;
    }
    // The panel's surface has the keyboard: a released chord was a tap.
    function focusArrived(): void {
        if (!armed || keyboardReady)
            return;
        keyboardReady = true;
        keyboardDeadline.stop();
        forceActiveFocus();
        const modifiers = modifierProbe();
        if (modifiers === -1)
            commit();
        else if (Switcher.held(modifiers))
            hold();
        else
            // A modifier state sent with the focus may still be on its way: look once more.
            tapCheck.restart();
    }
    function hold(): void {
        populated = true;
        revealTimer.restart();
        releasePoll.restart();
    }
    function reveal(): void {
        if (armed && !shown) {
            revealTimer.stop();
            populated = true;
            shown = true;
        }
    }
    function select(row: int): void {
        if (armed && row >= 0 && row < rows.length)
            index = row;
    }
    function commit(): void {
        if (!armed)
            return;
        const row = current;
        if (row && !row.focused)
            close("switched", row.id);
        else
            close("stayed", -1);
    }
    // Closes without switching, and ends a hand-off at once (the keyboard went elsewhere).
    function cancel(): void {
        if (armed)
            close("cancelled", -1);
        else if (pendingFocus >= 0)
            finishHandoff();
    }
    function close(result: string, target: double): void {
        armed = false;
        shown = false;
        keyboardReady = false;
        outcome = result;
        closedAt = Date.now();
        revealTimer.stop();
        releasePoll.stop();
        tapCheck.stop();
        keyboardDeadline.stop();
        focusSnapshot = niri.windows;
        pendingFocus = target;
        if (target >= 0) {
            // Start the deadline before emitting: a refused request can finish synchronously.
            handoff.restart();
            focusRequested(target);
        } else {
            finished();
        }
    }
    function finishHandoff(): void {
        if (pendingFocus < 0)
            return;
        handoff.stop();
        pendingFocus = -1;
        focusTicket = -1;
        focusSnapshot = null;
        finished();
    }
    // A modifier key went up: the switcher stays only while another chord modifier is held.
    function modifierReleased(flag: int): void {
        if (!armed || !keyboardReady)
            return;
        const modifiers = modifierProbe();
        if (modifiers === -1 || !Switcher.held(modifiers & ~flag))
            commit();
    }
    function pruneRecent(): void {
        const kept = {};
        let dropped = false;
        for (const id in recent) {
            if (niri.windows.some(w => String(w.id) === id))
                kept[id] = recent[id];
            else
                dropped = true;
        }
        if (dropped)
            recent = kept;
    }
    // These rectangles describe the scene at opening. Once it changes, use the saved picture
    // or icon until the next opening rather than cropping different pixels under an old title.
    function invalidatePictures(): void {
        if (!Object.keys(live).length)
            return;
        const current = Switcher.visible(niri.windows, niri.workspaces, outputName, viewportWidth, viewportHeight, niri.overviewOpen, labels.positions);
        if (JSON.stringify(current) !== JSON.stringify(live))
            live = {};
    }
    Connections {
        target: switcher.niri
        function onWindowsChanged(): void {
            switcher.pruneRecent();
            switcher.invalidatePictures();
        }
        function onWorkspacesChanged(): void {
            switcher.invalidatePictures();
        }
        function onOverviewOpenChanged(): void {
            switcher.invalidatePictures();
        }
    }
    Connections {
        target: switcher.labels
        function onPositionsChanged(): void {
            switcher.invalidatePictures();
        }
    }
    function storeRecent(id: double, result: var, width: real, height: real, rect: var): void {
        // grabToImage completes later, possibly after the scene changed or a new chord began.
        if (live[id] !== rect || !niri.windows.some(w => w.id === id))
            return;
        const next = Object.assign({}, recent);
        next[id] = {
            result: result,
            width: width,
            height: height
        };
        recent = next;
    }
    function syncCards(): void {
        cards.currentIndex = index;
    }
    onIndexChanged: syncCards()
    Timer {
        id: revealTimer
        interval: switcher.revealDelay
        onTriggered: switcher.reveal()
    }
    Timer {
        id: tapCheck
        interval: 24
        onTriggered: {
            if (!switcher.armed)
                return;
            if (Switcher.held(switcher.modifierProbe()))
                switcher.hold();
            else
                switcher.commit();
        }
    }
    // The release of the chord arrives as a key event. Nothing held without one means the
    // keyboard went elsewhere (Qt answers "nothing" without focus, before the window learns it
    // lost the focus): close without switching, never guess a window.
    Timer {
        id: releasePoll
        property int released: 0
        interval: 30
        repeat: true
        onRunningChanged: released = 0
        onTriggered: {
            if (!switcher.armed || !switcher.keyboardReady) {
                stop();
                return;
            }
            released = Switcher.held(switcher.modifierProbe()) ? 0 : released + 1;
            if (released >= 2)
                switcher.cancel();
        }
    }
    Timer {
        id: keyboardDeadline
        interval: switcher.keyboardTimeout
        onTriggered: switcher.commit()
    }
    Timer {
        id: handoff
        interval: switcher.handoffTimeout
        onTriggered: switcher.finishHandoff()
    }
    Keys.onPressed: event => {
        if (!armed)
            return;
        if (event.key === Qt.Key_Escape) {
            cancel();
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
            commit();
        } else if (event.key === Qt.Key_Right || event.key === Qt.Key_Tab) {
            index = Switcher.step(index, rows.length, 1);
            reveal();
        } else if (event.key === Qt.Key_Left || event.key === Qt.Key_Backtab) {
            index = Switcher.step(index, rows.length, -1);
            reveal();
        } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Up) {
            const next = index + (event.key === Qt.Key_Down ? 1 : -1) * Math.max(1, gridShape.columns);
            if (next >= 0 && next < rows.length)
                index = next;
            reveal();
        } else if (event.key === Qt.Key_Home) {
            index = 0;
            reveal();
        } else if (event.key === Qt.Key_End) {
            index = rows.length - 1;
            reveal();
        } else {
            return;
        }
        event.accepted = true;
    }
    Keys.onReleased: event => {
        const flag = event.key === Qt.Key_Alt || event.key === Qt.Key_AltGr ? Qt.AltModifier : event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L || event.key === Qt.Key_Super_R ? Qt.MetaModifier : event.key === Qt.Key_Control ? Qt.ControlModifier : 0;
        if (flag) {
            modifierReleased(flag);
            event.accepted = true;
        }
    }

    // ---- The panel ----
    readonly property int cardWidth: 216
    readonly property int cardHeight: 176
    readonly property int pictureWidth: cardWidth - 16
    readonly property int pictureHeight: 125
    readonly property int pad: 20
    readonly property int margin: 48
    readonly property var gridShape: Switcher.grid(rows.length, cardWidth, cardHeight, viewportWidth - 2 * margin - 2 * pad, viewportHeight - 2 * margin - 2 * pad)
    width: viewportWidth
    height: viewportHeight
    Item {
        id: panel
        objectName: "window-switcher-panel"
        readonly property real radius: 24
        width: switcher.gridShape.columns * switcher.cardWidth + 2 * switcher.pad
        height: switcher.gridShape.rows * switcher.cardHeight + 2 * switcher.pad
        x: Math.round((switcher.viewportWidth - width) / 2)
        y: Math.round((switcher.viewportHeight - height) / 2)
        visible: opacity > 0
        opacity: switcher.shown ? 1 : 0
        scale: switcher.shown ? 1 : .96
        Behavior on opacity {
            NumberAnimation {
                duration: switcher.shown ? 140 : 110
                easing.type: Easing.OutCubic
            }
        }
        Behavior on scale {
            NumberAnimation {
                duration: 160
                easing.type: Easing.OutCubic
            }
        }
        // The Settings material: the Regular glass under the Settings canvas tint.
        WelcomeGlass {
            anchors.fill: parent
            radius: panel.radius
            wallpaperTexture: switcher.wallpaperTexture
            dpr: switcher.dpr
        }
        CanvasTint {
            shown: true
            anchors.fill: parent
            radius: panel.radius
        }
        // Presses on the plate stay in the switcher (outside it, the scene cancels).
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
        }
        GridView {
            id: cards
            objectName: "window-switcher-cards"
            x: switcher.pad
            y: switcher.pad
            width: switcher.gridShape.columns * switcher.cardWidth
            height: switcher.gridShape.rows * switcher.cardHeight
            cellWidth: switcher.cardWidth
            cellHeight: switcher.cardHeight
            clip: switcher.gridShape.scrolls
            interactive: false
            model: switcher.populated ? switcher.rows : []
            highlightFollowsCurrentItem: true
            highlightMoveDuration: switcher.shown ? 120 : 0
            highlight: Rectangle {
                radius: 16
                color: LiquidPalette.selectedDrop
                border.width: 1
                border.color: LiquidPalette.flatDropRim
            }
            onCountChanged: switcher.syncCards()
            onCurrentIndexChanged: if (currentIndex >= 0)
                positionViewAtIndex(currentIndex, GridView.Contain)
            delegate: Item {
                id: card
                required property var modelData
                required property int index
                readonly property var row: modelData
                readonly property bool selected: index === switcher.index
                readonly property var liveRect: switcher.live[row.id] ?? null
                readonly property bool liveShown: liveRect !== null && switcher.pictureSource !== null && switcher.pictureSourceReady
                readonly property var kept: switcher.recent[row.id] ?? null
                readonly property var entry: switcher.identity.resolve(row.appId)
                readonly property string iconSource: entry?.icon ? Quickshell.iconPath(entry.icon, true) : ""
                readonly property string letter: (entry?.name || row.appId || "#").charAt(0).toUpperCase()
                // The window fitted into the 200 × 125 picture box.
                readonly property real fit: liveRect ? Math.min(switcher.pictureWidth / liveRect.width, switcher.pictureHeight / liveRect.height) : 1
                readonly property real liveWidth: liveRect ? Math.max(1, Math.round(liveRect.width * fit)) : 1
                readonly property real liveHeight: liveRect ? Math.max(1, Math.round(liveRect.height * fit)) : 1
                objectName: "window-switcher-card"
                width: switcher.cardWidth
                height: switcher.cardHeight
                // Keep the picture for when this window is off screen.
                function keep(): void {
                    const picture = livePicture.item as Item;
                    if (!liveShown || !picture)
                        return;
                    const id = row.id, w = liveWidth, h = liveHeight, rect = liveRect;
                    picture.grabToImage(result => switcher.storeRecent(id, result, w, h, rect), Qt.size(Math.round(w * switcher.dpr), Math.round(h * switcher.dpr)));
                }
                Timer {
                    running: card.liveShown && switcher.armed
                    interval: 150
                    onTriggered: card.keep()
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    onPositionChanged: mouse => {
                        const at = mapToItem(null, mouse.x, mouse.y);
                        if (switcher.pointerOrigin.x < 0) {
                            switcher.pointerOrigin = at;
                        } else if (switcher.shown && Math.abs(at.x - switcher.pointerOrigin.x) + Math.abs(at.y - switcher.pointerOrigin.y) > 3) {
                            switcher.select(card.index);
                        }
                    }
                    onClicked: {
                        switcher.select(card.index);
                        switcher.commit();
                    }
                }
                // No picture of this window yet: its icon on a plain tile.
                Rectangle {
                    visible: !card.liveShown && card.kept === null
                    x: 8
                    y: 8
                    width: switcher.pictureWidth
                    height: switcher.pictureHeight
                    radius: 10
                    color: LiquidPalette.flatDrop
                    border.width: 1
                    border.color: LiquidPalette.flatDropRim
                    Image {
                        id: bigIcon
                        anchors.centerIn: parent
                        width: 56
                        height: 56
                        sourceSize: Qt.size(112, 112)
                        source: card.iconSource
                        visible: status === Image.Ready
                        asynchronous: true
                        smooth: true
                        mipmap: true
                    }
                    Text {
                        anchors.centerIn: parent
                        visible: !bigIcon.visible
                        text: card.letter
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 40
                        font.weight: Font.DemiBold
                        color: LiquidPalette.plateDim
                    }
                }
                // Rounded on the GPU; the software renderer (offscreen tests) clips square.
                Loader {
                    id: livePicture
                    active: card.liveShown
                    x: 8 + (switcher.pictureWidth - width) / 2
                    y: 8 + (switcher.pictureHeight - height) / 2
                    width: card.liveWidth
                    height: card.liveHeight
                    sourceComponent: GraphicsInfo.api === GraphicsInfo.Software ? squareLive : roundLive
                }
                Component {
                    id: squareLive
                    Item {
                        clip: true
                        ShaderEffectSource {
                            anchors.fill: parent
                            sourceItem: switcher.pictureSource
                            sourceRect: Qt.rect(card.liveRect.x, card.liveRect.y, card.liveRect.width, card.liveRect.height)
                            // Rendered at twice the card's pixels and mipmapped: a window shrunk seven times
                            // keeps its text from shimmering.
                            textureSize: Qt.size(Math.round(width * switcher.dpr * 2), Math.round(height * switcher.dpr * 2))
                            mipmap: true
                            live: true
                            smooth: true
                        }
                    }
                }
                Component {
                    id: roundLive
                    ClippingRectangle {
                        radius: 8
                        color: "transparent"
                        ShaderEffectSource {
                            width: card.liveWidth
                            height: card.liveHeight
                            sourceItem: switcher.pictureSource
                            sourceRect: Qt.rect(card.liveRect.x, card.liveRect.y, card.liveRect.width, card.liveRect.height)
                            // Rendered at twice the card's pixels and mipmapped: a window shrunk seven times
                            // keeps its text from shimmering.
                            textureSize: Qt.size(Math.round(width * switcher.dpr * 2), Math.round(height * switcher.dpr * 2))
                            mipmap: true
                            live: true
                            smooth: true
                        }
                    }
                }
                // The last live picture of a window that is off this screen now.
                Loader {
                    active: !card.liveShown && card.kept !== null
                    x: 8 + (switcher.pictureWidth - width) / 2
                    y: 8 + (switcher.pictureHeight - height) / 2
                    width: card.kept ? card.kept.width : 1
                    height: card.kept ? card.kept.height : 1
                    sourceComponent: GraphicsInfo.api === GraphicsInfo.Software ? squareKept : roundKept
                }
                Component {
                    id: squareKept
                    Image {
                        source: card.kept?.result?.url ?? ""
                        cache: false
                        smooth: true
                    }
                }
                Component {
                    id: roundKept
                    ClippingRectangle {
                        radius: 8
                        color: "transparent"
                        Image {
                            width: card.kept?.width ?? 1
                            height: card.kept?.height ?? 1
                            source: card.kept?.result?.url ?? ""
                            cache: false
                            smooth: true
                        }
                    }
                }
                // A light rim, as the icon tiles have, so a dark or white window keeps its edge.
                Rectangle {
                    readonly property real shownWidth: card.liveShown ? card.liveWidth : card.kept?.width ?? 0
                    readonly property real shownHeight: card.liveShown ? card.liveHeight : card.kept?.height ?? 0
                    visible: card.liveShown || card.kept !== null
                    x: 8 + (switcher.pictureWidth - width) / 2
                    y: 8 + (switcher.pictureHeight - height) / 2
                    width: shownWidth
                    height: shownHeight
                    radius: 8
                    color: "transparent"
                    border.width: 1
                    border.color: LiquidPalette.flatDropRim
                }
                Row {
                    x: 12
                    y: 8 + switcher.pictureHeight + 10
                    width: card.width - 24
                    spacing: 8
                    Item {
                        width: 20
                        height: 20
                        Image {
                            id: smallIcon
                            anchors.fill: parent
                            sourceSize: Qt.size(40, 40)
                            source: card.iconSource
                            visible: status === Image.Ready
                            asynchronous: true
                            smooth: true
                            mipmap: true
                        }
                        Rectangle {
                            anchors.fill: parent
                            visible: !smallIcon.visible
                            radius: 6
                            color: LiquidPalette.flatDrop
                            Text {
                                anchors.centerIn: parent
                                text: card.letter
                                font.family: ShellPalette.uiFont
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: LiquidPalette.ink
                            }
                        }
                    }
                    Text {
                        objectName: "window-switcher-title"
                        width: parent.width - 28
                        height: 20
                        verticalAlignment: Text.AlignVCenter
                        text: card.row.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 1
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 13
                        font.weight: card.selected ? Font.DemiBold : Font.Normal
                        color: card.selected ? LiquidPalette.ink : LiquidPalette.plateDim
                    }
                }
                // A window asking for attention: a dot in its picture's corner.
                Rectangle {
                    visible: card.row.urgent
                    x: card.width - 22
                    y: 12
                    width: 10
                    height: 10
                    radius: 5
                    color: LiquidPalette.danger
                }
            }
        }
    }
}
