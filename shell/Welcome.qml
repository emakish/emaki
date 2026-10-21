// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"
import "WelcomeContent.js" as Content
import "LockWordmark.js" as Motion
import "LockWordmarkData.js" as Data
import "SystemIcons.js" as SystemIcons
import "Keyboard.js" as Keyboard

// "Welcome to Emaki": a short first-run tour on the Settings material (SettingsGlass, the
// Settings cards, buttons and type). One idea per page: a live picture, a big title, a line
// or two. Left and Right turn the pages, Enter goes on, Esc closes; Tab, Up and Down walk
// the buttons.
FocusScope {
    id: welcome
    readonly property bool keyboardBoundary: true
    property int page: 0
    property string wallpaperTexture: ""
    property real dpr: 1
    property string error: ""
    property var pages: Content.pages()
    property string heading: qsTranslate("Welcome", "Getting started")
    property string closeLabel: qsTranslate("Welcome", "Close welcome")
    property string doneLabel: qsTranslate("Welcome", "Start using Emaki")
    property bool showShortcuts: true
    property string actionLabel: ""
    property real artworkHeight: 0
    readonly property int count: welcome.pages.length
    readonly property var current: welcome.pages[page]
    readonly property bool last: page === count - 1
    readonly property bool glassReady: glass.ready
    readonly property bool narrow: width < 640
    readonly property real inset: narrow ? 24 : 40
    signal closeRequested
    signal shortcutsRequested
    signal actionRequested
    signal whatsNewRequested
    property string whatsNewLabel: ""
    implicitWidth: 800
    implicitHeight: 660
    focus: true
    function takeFocus(): void {
        next.forceActiveFocus();
    }
    function go(index: int): void {
        const target = Math.max(0, Math.min(count - 1, index));
        if (target === page)
            return;
        const backFocused = back.activeFocus;
        const shortcutsFocused = shortcuts.activeFocus;
        const actionFocused = action.activeFocus;
        const whatsNewFocused = whatsNew.activeFocus;
        page = target;
        if ((backFocused && !back.visible) || (shortcutsFocused && !shortcuts.visible) || (actionFocused && !action.visible) || (whatsNewFocused && !whatsNew.visible))
            next.forceActiveFocus();
    }
    function turn(delta: int): void {
        go(page + delta);
    }
    function focusButtons(): var {
        return Keyboard.targets(welcome);
    }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            closeRequested();
            event.accepted = true;
        } else if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
            turn(event.key === Qt.Key_Left ? -1 : 1);
            event.accepted = true;
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            // Nothing focused (a click on the plate): Enter goes on, as the Next button does.
            next.clicked();
            event.accepted = true;
        } else if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
            body.contentY = Math.max(0, Math.min(body.contentHeight - body.height, body.contentY + (event.key === Qt.Key_PageUp ? -body.height : body.height)));
            event.accepted = true;
        } else {
            Keyboard.handle(welcome, event);
        }
    }
    onPageChanged: {
        body.contentY = 0;
        arrival.restart();
    }
    Component.onCompleted: takeFocus()

    // The Settings window's material and its accent edge.
    SettingsGlass {
        id: glass
        anchors.fill: parent
        wallpaperTexture: welcome.wallpaperTexture
        dpr: welcome.dpr
    }
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        radius: 16
        antialiasing: true
        border.width: 2
        border.color: SettingsTheme.edge
        z: 2
    }

    component Copy: Text {
        color: SettingsTheme.ink
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 15
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }
    component Keycap: Rectangle {
        id: keycap
        required property string label
        property real size: 13
        implicitWidth: Math.max(implicitHeight, keyLabel.implicitWidth + 18)
        implicitHeight: 30
        radius: 8
        antialiasing: true
        color: Appearance.dark ? Qt.rgba(1, 1, 1, .08) : Qt.rgba(1, 1, 1, .72)
        border.width: 1
        border.color: SettingsTheme.rim
        Copy {
            id: keyLabel
            anchors.centerIn: parent
            text: keycap.label
            font.pixelSize: keycap.size
            font.weight: Font.DemiBold
            wrapMode: Text.NoWrap
        }
    }
    // Super, then the keys that go with it: [Super] + [←] [→].
    component Combo: Row {
        id: combo
        required property var keys
        spacing: 5
        Keycap {
            label: combo.keys[0]
        }
        Copy {
            anchors.verticalCenter: parent.verticalCenter
            text: "+"
            color: SettingsTheme.dim
            font.pixelSize: 13
            wrapMode: Text.NoWrap
        }
        Repeater {
            model: combo.keys.slice(1)
            Keycap {
                required property string modelData
                label: modelData
            }
        }
    }
    // A Settings section tile: the gradient square with a light glyph.
    component Tile: Rectangle {
        id: tile
        property color from: SettingsTheme.accent
        property color to: SettingsTheme.accent
        property string glyph: ""
        property real glyphSize: 28
        width: 48
        height: 48
        radius: 14
        antialiasing: true
        border.width: 1
        border.color: SettingsTheme.whiteRim
        gradient: Gradient {
            GradientStop {
                position: 0
                color: tile.from
            }
            GradientStop {
                position: 1
                color: tile.to
            }
        }
        Image {
            anchors.centerIn: parent
            width: tile.glyphSize
            height: tile.glyphSize
            sourceSize: Qt.size(64, 64)
            source: tile.glyph
        }
    }

    // ── Pictures, one per page ─────────────────────────────────────────────
    // The real logo: the pixel wordmark of the lock and the startup screen, assembling from
    // its ribbons, then shimmering like the lock's idle "lake".
    component WordmarkPicture: Item {
        id: mark
        property bool running: false
        property real dpr: 1
        readonly property int pixel: Math.max(1, Math.round((width >= 2 * Data.W + 40 ? 2 : 1) * dpr))
        readonly property real cell: pixel / Math.max(.1, dpr)
        readonly property int cols: Math.floor(width / cell)
        readonly property int rows: Math.floor(height / cell)
        readonly property int originX: Math.max(0, Math.floor((cols - Data.W) / 2))
        readonly property int originY: Math.max(3, Math.floor((rows - Data.H) / 2))
        property double started: 0
        property real age: 0
        objectName: "welcome-wordmark"
        Accessible.role: Accessible.Graphic
        Accessible.name: "Emaki"
        function restart(): void {
            started = Date.now();
            age = 0;
            canvas.requestPaint();
        }
        onRunningChanged: if (running)
            restart()
        onWidthChanged: canvas.requestPaint()
        onHeightChanged: canvas.requestPaint()
        Timer {
            interval: mark.age < .6 ? 16 : 66
            repeat: true
            running: mark.running && mark.visible
            onTriggered: {
                mark.age = (Date.now() - mark.started) / 1000;
                canvas.requestPaint();
            }
        }
        Canvas {
            id: canvas
            anchors.fill: parent
            renderTarget: Canvas.Image
            renderStrategy: Canvas.Immediate
            antialiasing: false
            smooth: false
            onAvailableChanged: requestPaint()
            onPaint: {
                const c = getContext("2d");
                c.reset();
                c.clearRect(0, 0, width, height);
                const s = mark.cell, ox = mark.originX, oy = mark.originY;
                if (mark.running && mark.age < .5) {
                    // The lock's ribbon intro in the picture's own pixel grid.
                    const logo = {
                        x: ox,
                        y: oy,
                        dw: mark.cols,
                        dh: mark.rows,
                        dpr: 1
                    };
                    for (const row of Motion.ROWS) {
                        for (let i = 0; i < row.length; i++) {
                            const p = row[i], x = Motion.introX(p, i, row, mark.age, logo);
                            if (x >= mark.cols)
                                continue;
                            c.fillStyle = Data.HOT[p[4]];
                            c.fillRect(x * s, (oy + p[1]) * s, s, s);
                        }
                    }
                    return;
                }
                const t = Math.max(0, mark.age - .5);
                const buckets = [[], [], [], []];
                for (const p of Motion.PIXELS) {
                    const shift = mark.running ? Motion.lake(p, t) : ({});
                    buckets[shift.cyc === undefined ? p[4] : shift.cyc].push(p);
                }
                for (let k = 0; k < buckets.length; k++) {
                    c.fillStyle = Data.HOT[k];
                    for (const p of buckets[k])
                        c.fillRect((ox + p[0]) * s, (oy + p[1]) * s, s, s);
                }
                if (!mark.running)
                    return;
                c.fillStyle = "#ffffff";
                for (const spark of Motion.sparks(t)) {
                    const x = (ox + spark[0]) * s, y = (oy + spark[1]) * s;
                    c.globalAlpha = 1;
                    c.fillRect(x, y, s, s);
                    for (let d = 1; d <= spark[2]; d++) {
                        c.globalAlpha = d === 1 ? .9 : .55;
                        c.fillRect(x + d * s, y, s, s);
                        c.fillRect(x - d * s, y, s, s);
                        c.fillRect(x, y + d * s, s, s);
                        c.fillRect(x, y - d * s, s, s);
                    }
                }
                c.globalAlpha = 1;
            }
        }
    }

    // Two keys that are the same Super: the Windows key and the Mac's Command.
    component KeyboardsPicture: Item {
        id: keyboards
        property bool running: false
        property bool narrow: false
        property var keys: []
        property int pressed: -1
        implicitHeight: 210
        Timer {
            interval: 1100
            repeat: true
            running: keyboards.running && keyboards.visible
            onTriggered: keyboards.pressed = keyboards.pressed === 0 ? 1 : 0
        }
        Row {
            anchors.centerIn: parent
            spacing: keyboards.narrow ? 22 : 44
            Repeater {
                model: keyboards.keys
                Column {
                    id: keyboard
                    required property int index
                    required property var modelData
                    spacing: 6
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 104
                        height: 96
                        radius: 18
                        antialiasing: true
                        scale: keyboards.pressed === keyboard.index ? .94 : 1
                        color: keyboards.pressed === keyboard.index ? Qt.rgba(SettingsTheme.accent.r, SettingsTheme.accent.g, SettingsTheme.accent.b, Appearance.dark ? .22 : .18) : Appearance.dark ? Qt.rgba(1, 1, 1, .08) : Qt.rgba(1, 1, 1, .72)
                        border.width: 2
                        border.color: SettingsTheme.edge
                        Behavior on scale {
                            NumberAnimation {
                                duration: 160
                                easing.type: Easing.OutCubic
                            }
                        }
                        Behavior on color {
                            ColorAnimation {
                                duration: 160
                            }
                        }
                        // A window of four panes on the PC key.
                        Grid {
                            visible: keyboard.modelData.kind === "pc"
                            anchors.centerIn: parent
                            columns: 2
                            spacing: 4
                            Repeater {
                                model: 4
                                Rectangle {
                                    width: 15
                                    height: 15
                                    radius: 3
                                    color: SettingsTheme.ink
                                }
                            }
                        }
                        // The Command loop sign and its word, as Mac keys print them.
                        Image {
                            visible: keyboard.modelData.kind === "mac"
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 20
                            width: 34
                            height: 34
                            sourceSize: Qt.size(68, 68)
                            source: 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + SettingsTheme.ink + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3"/></svg>')
                        }
                        Copy {
                            visible: keyboard.modelData.kind === "mac"
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 62
                            text: qsTranslate("Welcome", "command")
                            font.pixelSize: 12
                            wrapMode: Text.NoWrap
                        }
                    }
                    Item {
                        width: 1
                        height: 6
                    }
                    Copy {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: keyboard.modelData.key
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        wrapMode: Text.NoWrap
                    }
                    Copy {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: keyboard.modelData.keyboard
                        color: SettingsTheme.dim
                        font.pixelSize: 13
                        wrapMode: Text.NoWrap
                    }
                }
            }
        }
    }

    // A small Emaki screen: windows side by side on a strip that scrolls, a second workspace
    // below, then the overview with both. The focused window has the accent border, as in niri.
    component StripPicture: Item {
        id: strip
        property bool running: false
        property int step: 0
        readonly property real screenW: Math.min(width - 40, (height - 28) * 2.3)
        readonly property real screenH: height - 28
        readonly property real barSpace: 20
        readonly property real gap: 7
        readonly property var columns: [[.46, .3, .58, .38], [.52, .4]]
        readonly property int workspace: step >= 4 ? 1 : 0
        readonly property int column: step < 4 ? step : 0
        readonly property bool overview: step === 5
        function columnX(row: int, index: int): real {
            let x = gap;
            for (let i = 0; i < index; ++i)
                x += columns[row][i] * screenW + gap;
            return x;
        }
        // Keep the focused column in view, as niri scrolls the strip.
        readonly property real viewX: {
            if (overview)
                return 0;
            const x = columnX(workspace, column), w = columns[workspace][column] * screenW;
            return Math.max(0, x + w + gap - screenW);
        }
        readonly property real viewY: overview ? screenH / 2 : workspace * screenH
        Timer {
            interval: 1500
            repeat: true
            running: strip.running && strip.visible
            onTriggered: strip.step = (strip.step + 1) % 6
        }
        Rectangle {
            id: screen
            anchors.centerIn: parent
            width: strip.screenW
            height: strip.screenH
            radius: 12
            antialiasing: true
            clip: true
            color: Appearance.dark ? Qt.rgba(0, 0, 0, .2) : Qt.rgba(1, 1, 1, .4)
            border.width: 1
            border.color: SettingsTheme.rim
            gradient: Gradient {
                GradientStop {
                    position: 0
                    color: Qt.rgba(SettingsTheme.accent.r, SettingsTheme.accent.g, SettingsTheme.accent.b, Appearance.dark ? .26 : .34)
                }
                GradientStop {
                    position: 1
                    color: Appearance.dark ? Qt.rgba(0, 0, 0, .18) : Qt.rgba(1, 1, 1, .45)
                }
            }
            Item {
                id: world
                width: screen.width
                height: screen.height
                scale: strip.overview ? .5 : 1
                transformOrigin: Item.Center
                Behavior on scale {
                    NumberAnimation {
                        duration: 420
                        easing.type: Easing.OutCubic
                    }
                }
                Item {
                    x: -strip.viewX
                    y: -strip.viewY
                    Behavior on x {
                        NumberAnimation {
                            duration: 420
                            easing.type: Easing.OutCubic
                        }
                    }
                    Behavior on y {
                        NumberAnimation {
                            duration: 420
                            easing.type: Easing.OutCubic
                        }
                    }
                    Repeater {
                        model: 2
                        Item {
                            id: row
                            required property int index
                            y: row.index * (screen.height + (strip.overview ? 10 : 0))
                            Repeater {
                                model: strip.columns[row.index]
                                Rectangle {
                                    id: window
                                    required property int index
                                    required property real modelData
                                    readonly property bool focused: row.index === strip.workspace && index === strip.column
                                    x: strip.columnX(row.index, index)
                                    y: strip.barSpace
                                    width: modelData * strip.screenW
                                    height: screen.height - strip.barSpace - strip.gap
                                    radius: 7
                                    antialiasing: true
                                    color: Appearance.dark ? Qt.rgba(.2, .14, .13, focused ? .95 : .78) : Qt.rgba(1, .985, .97, focused ? .96 : .74)
                                    border.width: focused ? 2 : 1
                                    border.color: focused ? SettingsTheme.accent : SettingsTheme.rim
                                    Column {
                                        x: 9
                                        y: 10
                                        spacing: 6
                                        Repeater {
                                            model: [.62, .84, .48]
                                            Rectangle {
                                                required property real modelData
                                                width: (window.width - 18) * modelData
                                                height: 5
                                                radius: 2.5
                                                color: window.focused ? Qt.rgba(SettingsTheme.accent.r, SettingsTheme.accent.g, SettingsTheme.accent.b, .55) : SettingsTheme.rim
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            // The bar's three islands stay on top, as on the real screen.
            Row {
                x: 7
                y: 6
                spacing: 4
                visible: !strip.overview
                Rectangle {
                    width: 9
                    height: 9
                    radius: 3
                    color: SettingsTheme.accent
                }
                Rectangle {
                    width: 30
                    height: 9
                    radius: 4.5
                    color: SettingsTheme.card
                    border.width: 1
                    border.color: SettingsTheme.rim
                }
            }
            Rectangle {
                visible: !strip.overview
                anchors.horizontalCenter: parent.horizontalCenter
                y: 6
                width: 38
                height: 9
                radius: 4.5
                color: SettingsTheme.card
                border.width: 1
                border.color: SettingsTheme.rim
            }
            Rectangle {
                visible: !strip.overview
                anchors.right: parent.right
                anchors.rightMargin: 7
                y: 6
                width: 34
                height: 9
                radius: 4.5
                color: SettingsTheme.card
                border.width: 1
                border.color: SettingsTheme.rim
            }
        }
    }

    // The three places of the system, with their own icons.
    component PlacesPicture: Item {
        id: places
        property var items: []
        readonly property bool stacked: width < 600
        implicitHeight: grid.implicitHeight + 36
        // One height for the three cards: the tallest text decides. Each card reports its own.
        property var heights: []
        readonly property real tallest: Math.max(0, ...heights)
        Grid {
            id: grid
            anchors.centerIn: parent
            columns: places.stacked ? 1 : 3
            spacing: 12
            Repeater {
                id: cards
                model: places.items
                Rectangle {
                    id: place
                    objectName: "welcome-card"
                    required property int index
                    required property var modelData
                    width: places.stacked ? places.width - 32 : (places.width - 32 - 24) / 3
                    readonly property real wordsHeight: words.implicitHeight
                    function report(): void {
                        const heights = places.heights.slice(0, places.items.length);
                        heights[index] = wordsHeight;
                        places.heights = heights;
                    }
                    onWordsHeightChanged: report()
                    Component.onCompleted: report()
                    height: places.stacked ? Math.max(74, words.implicitHeight + 24) : 16 + 48 + 12 + places.tallest + 18
                    radius: 14
                    antialiasing: true
                    color: SettingsTheme.drop
                    border.width: 1
                    border.color: SettingsTheme.rim
                    Item {
                        id: icon
                        x: 16
                        y: places.stacked ? (place.height - height) / 2 : 16
                        width: 48
                        height: 48
                        Image {
                            visible: place.modelData.kind === "programs"
                            anchors.fill: parent
                            smooth: false
                            sourceSize: Qt.size(64, 64)
                            source: SystemIcons.programsSvg(ShellPalette.accent, ShellPalette.accentStrong, ShellPalette.accentSoft, ShellPalette.accentShade, ShellPalette.managerIconInk)
                        }
                        Tile {
                            visible: place.modelData.kind === "settings"
                            from: ShellPalette.accentSoft
                            to: ShellPalette.accent
                            glyph: SystemIcons.settingsSvg("#fffaf5")
                            glyphSize: 36
                        }
                        Tile {
                            visible: place.modelData.kind === "report"
                            from: ShellPalette.sectionRedStart
                            to: ShellPalette.sectionRedEnd
                            glyph: 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#fffaf5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3.5h11a2.5 2.5 0 0 1 2.5 2.5v7.5a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 4v-4A2.5 2.5 0 0 1 4 13.5V6a2.5 2.5 0 0 1 2.5-2.5z"/><path d="M12 7v4"/><path d="M12 13.6h.01"/></svg>')
                        }
                    }
                    Column {
                        id: words
                        x: places.stacked ? icon.x + icon.width + 14 : 16
                        y: places.stacked ? (place.height - height) / 2 : icon.y + icon.height + 12
                        width: place.width - x - 16
                        spacing: 3
                        Copy {
                            width: parent.width
                            text: place.modelData.name
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }
                        Copy {
                            width: parent.width
                            text: place.modelData.what
                            color: SettingsTheme.dim
                            font.pixelSize: 13
                        }
                        Copy {
                            width: parent.width
                            text: place.modelData.where
                            color: SettingsTheme.accentInk
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            topPadding: 3
                        }
                    }
                }
            }
        }
    }

    // A few shortcuts as keys, in two columns.
    component ShortcutsPicture: Item {
        id: shortcuts
        property var items: []
        readonly property bool single: width < 560
        implicitHeight: grid.implicitHeight + 40
        Grid {
            id: grid
            anchors.centerIn: parent
            columns: shortcuts.single ? 1 : 2
            rowSpacing: 10
            columnSpacing: 36
            Repeater {
                model: shortcuts.items
                Row {
                    id: shortcut
                    required property var modelData
                    spacing: 14
                    Item {
                        width: 132
                        height: 30
                        Combo {
                            keys: shortcut.modelData.keys
                        }
                    }
                    Copy {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 132
                        text: shortcut.modelData.label
                        font.pixelSize: 14
                    }
                }
            }
        }
    }

    // ── Layout ─────────────────────────────────────────────────────────────
    Item {
        id: band
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
        }
        height: 56
        Copy {
            x: welcome.inset
            anchors.verticalCenter: parent.verticalCenter
            text: welcome.heading.toUpperCase()
            color: SettingsTheme.dim
            font.pixelSize: 12
            font.weight: Font.DemiBold
            font.letterSpacing: .84
            wrapMode: Text.NoWrap
        }
        WelcomeButton {
            id: close
            objectName: "welcome-close"
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            round: true
            implicitHeight: 32
            labelSize: 18
            text: "×"
            Accessible.name: welcome.closeLabel
            onClicked: welcome.closeRequested()
        }
    }
    Flickable {
        id: body
        objectName: "welcome-body"
        anchors {
            top: band.bottom
            bottom: footer.top
            left: parent.left
            right: parent.right
        }
        clip: true
        contentWidth: width
        contentHeight: content.height + 20
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        Column {
            id: content
            x: welcome.inset
            width: body.width - 2 * welcome.inset
            spacing: 0
            NumberAnimation {
                id: arrival
                target: content
                property: "opacity"
                from: .35
                to: 1
                duration: 180
                easing.type: Easing.OutCubic
            }
            Rectangle {
                id: stage
                objectName: "welcome-stage"
                width: parent.width
                // Half the room under the band, within limits; a taller picture grows it.
                height: welcome.artworkHeight > 0 ? welcome.artworkHeight : Math.max(welcome.narrow ? 176 : 214, Math.min(300, Math.round(body.height * .5)), (picture.item as Item)?.implicitHeight ?? 0)
                radius: SettingsTheme.radius
                antialiasing: true
                color: SettingsTheme.card
                border.color: SettingsTheme.rim
                // A warm haze rising from the bottom of the card, in the accent.
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    radius: parent.radius - 1
                    antialiasing: true
                    gradient: Gradient {
                        GradientStop {
                            position: .35
                            color: "transparent"
                        }
                        GradientStop {
                            position: 1
                            color: Qt.rgba(SettingsTheme.accent.r, SettingsTheme.accent.g, SettingsTheme.accent.b, Appearance.dark ? .13 : .16)
                        }
                    }
                }
                Loader {
                    id: picture
                    anchors.fill: parent
                    sourceComponent: ({
                            "welcome": wordmarkPicture,
                            "super": keyboardsPicture,
                            "move": stripPicture,
                            "system": placesPicture,
                            "keys": shortcutsPicture,
                            "release": releasePicture
                        })[welcome.current.id]
                }
                Component {
                    id: releasePicture
                    Item {
                        Tile {
                            anchors.centerIn: parent
                            width: 96
                            height: 96
                            radius: 26
                            glyphSize: 56
                            from: ShellPalette.accentSoft
                            to: ShellPalette.accent
                            glyph: 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#fffaf5" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + ({
                                "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
                                "language": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 6.5h14M5 17.5h14"/>',
                                "updates": '<path d="M20 9a8 8 0 0 0-14-3L3 9m0-6v6h6M4 15a8 8 0 0 0 14 3l3-3m0 6v-6h-6"/>',
                                "fixes": '<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>'
                            })[welcome.current.icon] + '</svg>')
                        }
                    }
                }
                Component {
                    id: wordmarkPicture
                    WordmarkPicture {
                        running: welcome.visible && welcome.page === 0
                        dpr: welcome.dpr
                    }
                }
                Component {
                    id: keyboardsPicture
                    KeyboardsPicture {
                        running: welcome.visible
                        narrow: welcome.narrow
                        keys: welcome.current.keyboards ?? []
                    }
                }
                Component {
                    id: stripPicture
                    StripPicture {
                        running: welcome.visible
                    }
                }
                Component {
                    id: placesPicture
                    PlacesPicture {
                        items: welcome.current.places ?? []
                    }
                }
                Component {
                    id: shortcutsPicture
                    ShortcutsPicture {
                        items: welcome.current.shortcuts ?? []
                    }
                }
            }
            Item {
                width: 1
                height: 26
            }
            Copy {
                objectName: "welcome-title"
                width: parent.width
                text: welcome.current.title
                font.pixelSize: welcome.narrow ? 26 : 30
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Item {
                width: 1
                height: 8
            }
            Copy {
                width: parent.width
                text: welcome.current.text
                color: SettingsTheme.dim
                font.pixelSize: 16
                lineHeight: 1.15
            }
            Item {
                width: 1
                height: keys.visible ? 18 : 0
            }
            Flow {
                id: keys
                visible: (welcome.current.keys ?? []).length > 0
                width: parent.width
                spacing: 10
                Repeater {
                    model: welcome.current.keys ?? []
                    Rectangle {
                        id: chip
                        required property var modelData
                        width: chipRow.implicitWidth + 20
                        height: 44
                        radius: 13
                        antialiasing: true
                        color: SettingsTheme.drop
                        border.color: SettingsTheme.rim
                        Row {
                            id: chipRow
                            anchors.centerIn: parent
                            spacing: 10
                            Combo {
                                keys: chip.modelData.keys
                            }
                            Copy {
                                anchors.verticalCenter: parent.verticalCenter
                                text: chip.modelData.label
                                font.pixelSize: 14
                                font.weight: Font.Medium
                                wrapMode: Text.NoWrap
                            }
                        }
                    }
                }
            }
            Item {
                width: 1
                height: note.visible ? 14 : 0
            }
            Copy {
                id: note
                visible: text !== ""
                width: parent.width
                text: welcome.current.note
                color: SettingsTheme.dim
                font.pixelSize: 13
            }
            Item {
                width: 1
                height: shortcuts.visible || action.visible || whatsNew.visible ? 16 : 0
            }
            WelcomeButton {
                id: shortcuts
                objectName: "welcome-shortcuts"
                visible: welcome.showShortcuts && welcome.last
                text: qsTranslate("Welcome", "All shortcuts")
                onClicked: welcome.shortcutsRequested()
            }
            WelcomeButton {
                id: whatsNew
                objectName: "welcome-whatsnew"
                visible: welcome.page === 0 && welcome.whatsNewLabel !== ""
                text: welcome.whatsNewLabel
                onClicked: welcome.whatsNewRequested()
            }
            WelcomeButton {
                id: action
                objectName: "welcome-action"
                visible: welcome.actionLabel !== ""
                text: welcome.actionLabel
                onClicked: welcome.actionRequested()
            }
            Item {
                width: 1
                height: errorLine.visible ? 12 : 0
            }
            Copy {
                id: errorLine
                width: parent.width
                visible: welcome.error !== ""
                text: welcome.error
                color: SettingsTheme.error
                font.pixelSize: 13
                Accessible.role: Accessible.AlertMessage
            }
        }
    }
    // The Settings footer: the page dots, then Back and Next.
    Rectangle {
        id: footer
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        height: 68
        color: SettingsTheme.strip
        bottomLeftRadius: 16
        bottomRightRadius: 16
        Rectangle {
            width: parent.width
            height: 1
            color: SettingsTheme.rim
        }
        Row {
            id: dots
            x: welcome.inset - 6
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Repeater {
                model: welcome.count
                Item {
                    id: dot
                    required property int index
                    objectName: "welcome-dot-" + index
                    width: (welcome.page === index ? 26 : 8) + 12
                    height: 24
                    Accessible.role: Accessible.PageTab
                    Accessible.name: qsTranslate("Welcome", "Page %1 of %2: %3").arg(index + 1).arg(welcome.count).arg(welcome.pages[index].title)
                    Accessible.selected: welcome.page === index
                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width - 12
                        height: 8
                        radius: 4
                        antialiasing: true
                        color: welcome.page === dot.index ? SettingsTheme.accent : dotPointer.containsMouse ? SettingsTheme.dim : SettingsTheme.rim
                        Behavior on width {
                            NumberAnimation {
                                duration: 200
                                easing.type: Easing.OutCubic
                            }
                        }
                    }
                    MouseArea {
                        id: dotPointer
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: welcome.go(dot.index)
                    }
                }
            }
        }
        Row {
            anchors.right: parent.right
            anchors.rightMargin: welcome.inset - 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            WelcomeButton {
                id: back
                objectName: "welcome-back"
                visible: welcome.page > 0
                text: qsTranslate("Welcome", "Back")
                onClicked: welcome.turn(-1)
            }
            WelcomeButton {
                id: next
                objectName: "welcome-next"
                primary: true
                text: welcome.last ? welcome.doneLabel : qsTranslate("Welcome", "Next")
                onClicked: welcome.last ? welcome.closeRequested() : welcome.turn(1)
            }
        }
    }
}
