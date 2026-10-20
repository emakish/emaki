// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Quickshell
import "Keyboard.js" as Keyboard
import "SystemIcons.js" as SystemIcons

// What the clock panel shows (docs/mockups/liquid-glass/clock.js buildView()), in the
// panel's frame: (0, 0) is its top left once grown, 560 wide in the drawer, 440 in a peek.
// Drawer: the island's line larger (time 26 px and the long date on one baseline), media,
// the month, the notifications with Do not disturb. Peek: the notification that just
// arrived (or a summary of several) under the island's line, which the panel draws.
// Everything lies on the plate under the glass; pressable things are GlassTargets.
// The functions are the shell's (NotificationStore, MPRIS, the calendar); the look is new.
Item {
    id: body
    property bool keyboardMode: false
    property bool historyExpanded: false
    property bool showingNotes: false
    property bool notesVisited: false
    onShowingNotesChanged: if (showingNotes)
        notesVisited = true
    onOpenedChanged: if (opened) {
        if (!calendarEvents.editing)
            calendar.showToday();
        showingNotes = false;
        mainScroll.contentY = 0;
    } else {
        historyExpanded = false;
        list.contentY = 0;
    }
    readonly property alias scroller: mainScroll
    function revealHistory(y: real): void {
        historyExpanded = true;
        Qt.callLater(() => {
            mainScroll.contentY = Math.max(0, Math.min(body.listTop - body.header + y, mainScroll.contentHeight - mainScroll.height));
        });
    }
    function findTarget(item: var, key: string): var {
        if (item.key === key)
            return item;
        for (const child of item.children) {
            const found = findTarget(child, key);
            if (found)
                return found;
        }
        return null;
    }
    function focusNewest(): void {
        keyboardMode = true;
        for (const group of store.groups())
            store.expand(group.key, true);
        Qt.callLater(() => {
            const newest = store.entries.slice().sort((a, b) => b.time - a.time)[0];
            const target = newest ? findTarget(body, "note-" + newest.id) : null;
            if (target) {
                target.forceActiveFocus(Qt.TabFocusReason);
                Keyboard.reveal(target);
            } else
                Keyboard.focusFirst(body);
        });
    }
    function focusedKey(item: var): string {
        for (const child of item.children) {
            const found = focusedKey(child);
            if (found)
                return found;
        }
        return item.activeFocus ? item.key ?? "" : "";
    }
    function dismissFocused(all: bool): void {
        const key = focusedKey(body);
        const match = /^note-(-?\d+)/.exec(key);
        if (all)
            store.dismiss(store.entries.map(n => n.id));
        else if (match)
            store.dismiss([Number(match[1])]);
        else
            return;
        Qt.callLater(focusNewest);
    }
    required property NotificationStore store
    // The ClockPanel: colours, drops. Null in tests of the body alone.
    property var glass: null
    property bool opened: false
    property bool mediaEnabled: false
    property Component mediaSource: Component {
        MediaBlock {}
    }
    readonly property alias media: media
    readonly property MediaBlock player: media.item as MediaBlock
    function mediaAction(action: string): bool {
        return player ? player.perform(action) : false;
    }
    property var peekIds: []
    property bool hidePreviewBodies: false
    property string serverState: "disabled"
    onServerStateChanged: {
        // checking and registering are the normal start (NotificationService); only other states are faults.
        if (!["active", "disabled", "owned_elsewhere", "checking", "registering"].includes(serverState))
            console.warn("Notification server state: " + serverState);
    }
    property date today: new Date()
    property string time: ""
    property string longDate: ""
    // The panel may not grow past this (the screen minus margins): fewer notification rows.
    property real maxHeight: 10000
    readonly property var peekNotes: peekIds.map(id => store.entries.find(n => n.id === id)).filter(Boolean).map(n => hidePreviewBodies ? Object.assign({}, n, {
            body: "",
            actions: []
        }) : n)
    readonly property var peekApps: [...new Set(peekNotes.map(n => n.app))]
    readonly property alias calendar: calendar
    readonly property color ink: glass ? glass.ink : LiquidPalette.inkOnDark
    readonly property color dim: glass ? glass.dim : LiquidPalette.dimOnDark
    readonly property color faint: glass ? glass.faint : LiquidPalette.faintOnDark
    readonly property color accent: glass ? glass.accent : LiquidPalette.accentOnDark
    signal activated
    signal openHistory

    // ---- Layout: clock.js buildView(), panel coordinates ----
    readonly property int side: opened ? 22 : 16
    readonly property real inner: width - 2 * side
    readonly property int header: 62
    readonly property bool hasMedia: mediaEnabled && player !== null && player.playerCount > 0
    // The block (56), then 4 + 26 for the players' row and 14 to the month; 14 without the row.
    readonly property real calendarTop: header + (hasMedia ? player.implicitHeight + 14 : 0)
    readonly property real legacyNotesTop: calendarTop + calendar.height + 16
    readonly property real notesTop: legacyNotesTop + calendarEvents.height + 42
    readonly property real addedHeight: notesTop - legacyNotesTop
    readonly property bool serverNote: serverState !== "active"
    readonly property real listTop: notesTop + 36 + (store.scheduleResumeNote ? 22 : 0) + (serverNote ? 22 : 0)
    readonly property string actionNote: store.actionState === "action_unavailable" ? "This notification has no available action" : ""
    // Keep the initial drawer compact; scrolling or the overflow action opens history.
    readonly property var plan: {
        if (keyboardMode || historyExpanded)
            return notePlan(Math.max(1, store.count));
        let chosen = null;
        for (let cap = 6; cap >= 1; --cap) {
            chosen = notePlan(cap);
            if (listTop - addedHeight + chosen.height + (actionNote ? 22 : 0) + 14 <= maxHeight)
                break;
        }
        return chosen;
    }
    // The plate grows with the day's events and the Notes row, so the notifications stay in view;
    // past the screen's height its body scrolls. The Notes view gets room for a list and a note.
    readonly property real fullHeight: listTop + plan.height + (actionNote ? 22 : 0) + 14
    readonly property real drawerHeight: Math.min(maxHeight, showingNotes ? Math.max(fullHeight, 600) : fullHeight)
    readonly property real peekHeight: peekNotes.length === 1 ? 40 + noteHeight(peekNotes[0]) + 10 : peekNotes.length > 1 ? 120 : 36
    readonly property real desiredHeight: opened ? drawerHeight : peekHeight
    function actionsOf(note: var): var {
        return (note?.actions ?? []).filter(a => a.id !== "default");
    }
    // Measure with the same Qt text layout and width as the visible body. Character
    // counts cannot predict wrapping for device names, Unicode or a changed UI font.
    Component {
        id: bodyMeasure
        Text {
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
        }
    }
    // Every body wraps to the card's width. A critical notice (a battery warning, a pairing
    // request, the desktop update, the held Secure Boot notices) shows all of it; any other at
    // most bodyLines lines, the last one elided, its spaces and line breaks run together. A body
    // that fits on one line keeps the one-line row it always had.
    readonly property int bodyLines: 3
    function bodyText(note: var): string {
        return note.critical ? (note.body || "") : (note.body || "").replace(/\s+/g, " ");
    }
    function bodyLineLimit(note: var): int {
        return note.critical ? 2147483647 : bodyLines;
    }
    property var bodyHeights: ({})
    // Measured heights by width, line limit and text: an arrival measures only itself.
    property var measuredHeights: ({})
    Component.onCompleted: Qt.callLater(measureBodies)
    onInnerChanged: Qt.callLater(measureBodies)
    Connections {
        target: body.store
        function onEntriesChanged(): void {
            Qt.callLater(body.measureBodies);
        }
    }
    function measureBodies(): void {
        const width = Math.max(1, inner - 130);
        const known = Object.keys(measuredHeights).length > 512 ? {} : measuredHeights;
        const heights = {};
        for (const note of store.entries) {
            if (!note.body)
                continue;
            const text = bodyText(note);
            const lines = bodyLineLimit(note);
            const key = width + " " + lines + " " + text;
            if (known[key] === undefined) {
                const measure = bodyMeasure.createObject(null, {
                    width: width,
                    maximumLineCount: lines,
                    text: text
                }) as Text;
                known[key] = Math.max(20, Math.ceil(measure.implicitHeight));
                measure.destroy();
            }
            heights[note.id] = known[key];
        }
        measuredHeights = known;
        bodyHeights = heights;
    }
    function noteBodyHeight(note: var): real {
        return note.body ? (bodyHeights[note.id] ?? 20) : 20;
    }
    function noteHeight(note: var): real {
        return 30 + noteBodyHeight(note) + (actionsOf(note).length ? 28 : 0);
    }
    function notePlan(cap: int): var {
        const items = [];
        let y = 0, rows = 0, hidden = 0, lastBucket = "";
        const groups = store.groups();
        if (!groups.length) {
            items.push({
                kind: "empty",
                y: y
            });
            y += 32;
        }
        for (const g of groups) {
            if (rows >= cap) {
                hidden += g.count;
                continue;
            }
            if (g.bucket !== lastBucket) {
                items.push({
                    kind: "bucket",
                    y: y,
                    text: g.bucket.toUpperCase()
                });
                y += 22;
                lastBucket = g.bucket;
            }
            for (const n of g.notes.slice(0, (g.expanded || keyboardMode) ? cap - rows : 1)) {
                items.push({
                    kind: "note",
                    y: y,
                    note: n
                });
                y += noteHeight(n) + 2;
                rows++;
            }
            if (g.count > 1) {
                items.push({
                    kind: "more",
                    y: y,
                    group: g
                });
                y += 30;
            }
            y += 6;
        }
        if (hidden)
            items.push({
                kind: "hidden",
                y: y + 4,
                count: hidden
            });
        if (store.count) {
            items.push({
                kind: "clear",
                y: y + 4
            });
            y += 36;
        }
        return {
            items: items,
            height: y
        };
    }
    function age(time: double): string {
        const minutes = Math.max(0, Math.floor((store.now - (time ?? store.now)) / 60000));
        return minutes < 1 ? "now" : minutes < 60 ? minutes + " min" : minutes < 1440 ? Math.floor(minutes / 60) + " h" : Math.floor(minutes / 1440) + " d";
    }
    // A themed icon by name. Programs & Updates draws its icon in the accent, as the dock
    // and the launcher do; its installed SVG keeps the default accent.
    function themedIcon(name: string): string {
        if (name === "emaki-programs-updates")
            return SystemIcons.programsSvg(ShellPalette.accent, ShellPalette.accentStrong, ShellPalette.accentSoft, ShellPalette.accentShade, ShellPalette.managerIconInk);
        if (name === "emaki-welcome")
            return SystemIcons.welcomeSvg(ShellPalette.accent, ShellPalette.accentStrong, ShellPalette.accentSoft, ShellPalette.managerIconInk);
        if (name === "emaki-notes")
            return SystemIcons.notesSvg(ShellPalette.accent, ShellPalette.accentStrong, ShellPalette.accentSoft, ShellPalette.accentShade, ShellPalette.managerIconInk);
        return name ? Quickshell.iconPath(name, true) : "";
    }
    // The app's icon: the one the notification sent, else its desktop entry by name.
    function iconOf(note: var): string {
        const icon = note?.icon ?? "";
        if (icon.startsWith("/"))
            return "file://" + icon;
        if (icon.startsWith("file:") || icon.startsWith("image:"))
            return icon;
        const path = themedIcon(icon);
        if (path)
            return path;
        const app = String(note?.app ?? "").toLocaleLowerCase();
        const entry = app ? DesktopEntries.heuristicLookup(note.app) || DesktopEntries.applications.values.find(e => e.name.toLocaleLowerCase() === app) : null;
        return themedIcon(entry?.icon ?? "");
    }

    // ---- Drawer head: the island's line, larger ----
    Item {
        visible: body.opened
        width: parent.width
        height: body.header
        Text {
            id: bigTime
            x: body.side
            y: 20
            height: 30
            verticalAlignment: Text.AlignVCenter
            text: body.time
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 26
            font.weight: Font.DemiBold
            color: body.ink
        }
        Text {
            x: bigTime.x + bigTime.implicitWidth + 14
            // clock.js: the date centred 2 px below the time's centre (reads as one baseline).
            y: 22
            height: 30
            verticalAlignment: Text.AlignVCenter
            text: body.longDate
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
            color: body.dim
        }
        Rectangle {
            x: body.side
            y: body.header - 8
            width: body.inner
            height: 1
            color: body.faint
        }
    }

    Flickable {
        id: mainScroll
        visible: body.opened && !body.showingNotes
        y: body.header
        width: body.width
        height: Math.max(1, body.drawerHeight - y)
        contentWidth: width
        contentHeight: body.listTop + list.height + 36 - body.header
        clip: true
        // Scrolling the shade down anywhere opens the older notification groups, as the list did.
        onContentYChanged: if (contentY > 0 && body.opened)
            body.historyExpanded = true
        ScrollBar.vertical: ScrollBar {}
        Item {
            id: scrollContent
            y: -body.header
            width: mainScroll.width
            height: mainScroll.contentHeight + body.header
        }
    }
    Word {
        visible: body.opened && body.showingNotes
        x: body.side
        y: body.header
        key: "notes-back"
        label: "‹ Calendar & notifications"
        onClicked: body.showingNotes = false
    }
    Loader {
        id: notesLoader
        active: body.notesVisited
        visible: body.showingNotes && body.opened
        x: body.side
        y: body.header + 36
        width: body.inner
        height: Math.max(1, body.drawerHeight - y - 14)
        sourceComponent: NotesView {
            objectName: "clock-notes-view"
            compact: true
            onOpenRequested: noteId => Quickshell.execDetached(["emaki-notes", "--note", noteId])
        }
    }

    // ---- Media ----
    Loader {
        id: media
        parent: scrollContent
        x: body.side
        y: body.header
        width: body.inner
        active: body.mediaEnabled
        visible: body.opened && body.hasMedia
        sourceComponent: body.mediaSource
        onLoaded: item.glass = Qt.binding(() => body.glass)
    }

    // ---- Calendar ----
    CalendarMonth {
        id: calendar
        parent: scrollContent
        events: NotesService.events
        visible: body.opened
        x: body.side
        y: body.calendarTop
        width: body.inner
        glass: body.glass
        today: body.today
    }

    CalendarEvents {
        id: calendarEvents
        objectName: "clock-calendar-events"
        parent: scrollContent
        x: body.side
        y: body.legacyNotesTop - 8
        width: body.inner
        selectedDate: calendar.selectedDate
        pickedDate: calendar.picked ? calendar.selectedDate : ""
        onSelectDate: date => calendar.selectDate(date)
        glass: body.glass
    }
    Word {
        parent: scrollContent
        x: body.side
        y: calendarEvents.y + calendarEvents.height + 4
        key: "shade-notes"
        label: "Notes · " + NotesService.notes.length + "  ›"
        onClicked: body.showingNotes = true
    }

    // ---- Notifications: heading, Do not disturb, the groups ----
    Item {
        parent: scrollContent
        visible: body.opened
        y: body.notesTop
        width: parent.width
        height: 36
        Text {
            x: body.side
            height: 28
            verticalAlignment: Text.AlignVCenter
            text: "Notifications · " + body.store.count
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
            color: body.ink
        }
        Text {
            x: body.side + body.inner - 48 - width
            height: 28
            verticalAlignment: Text.AlignVCenter
            text: "Do not disturb"
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.dim
        }
        // The track is flat (the accent when on); the knob is a glass drop the panel draws.
        GlassTarget {
            id: dndToggle
            glass: body.glass
            key: "dnd"
            label: "Do not disturb"
            drop: false
            accent: body.store.effectiveDnd
            x: body.side + body.inner - 38
            y: 4
            width: 38
            height: 20
            onClicked: body.store.setDnd(!body.store.effectiveDnd)
            Rectangle {
                anchors.fill: parent
                radius: 10
                color: body.store.effectiveDnd ? body.accent : body.faint
            }
            Accessible.role: Accessible.CheckBox
            Accessible.checked: body.store.effectiveDnd
        }
    }
    // The knob's place (clock.js 'knob'): 20 × 22 at the track's left or right end.
    readonly property rect knobRect: Qt.rect(side + inner - 38 + (store.effectiveDnd ? 18 : 0), notesTop + 3 - mainScroll.contentY, 20, 22)
    readonly property bool knobShown: opened && !showingNotes && knobRect.y >= header && knobRect.y + knobRect.height <= drawerHeight
    Text {
        parent: scrollContent
        objectName: "dnd-schedule-resume"
        visible: body.opened && body.store.scheduleResumeNote !== ""
        x: body.side
        y: body.notesTop + 36
        width: body.inner
        height: 18
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: body.store.scheduleResumeNote
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        font.weight: Font.Medium
        color: body.dim
    }
    Text {
        parent: scrollContent
        objectName: "notification-server-notice"
        visible: body.opened && body.serverNote
        x: body.side
        y: body.notesTop + 36 + (body.store.scheduleResumeNote ? 22 : 0)
        width: body.inner
        height: 18
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: body.serverState === "owned_elsewhere" ? "Notifications are shown by another program" : body.serverState === "disabled" ? "Notifications are off" : body.serverState === "checking" || body.serverState === "registering" ? "Notifications are starting…" : "Notifications are unavailable"
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        font.weight: Font.Medium
        color: body.dim
    }

    // A notification: the app's icon 28, the summary, the body, "App · age" on the right;
    // hovering adds a close button on the body line. App actions follow as accent words.
    component NoteRow: GlassTarget {
        id: row
        required property var note
        property bool peek: false
        readonly property var actions: body.actionsOf(note)
        // The row or one of its own targets (close, an action) holds the hover drop.
        readonly property bool open: pointerInside || activeFocus || dismiss.activeFocus || body.focusedKey(row).length > 0 || (body.glass ? body.glass.hoverKey === key || body.glass.hoverKey.startsWith(key + ":") : false)
        key: (peek ? "peek-" : "note-") + note.id
        label: note.summary
        glass: body.glass
        height: body.noteHeight(note)
        bubblePad: 0
        bubbleRadius: 12
        onClicked: {
            if (row.peek)
                body.openHistory();
            else if (body.store.activate(row.note.id, "default"))
                body.activated();
        }
        Image {
            id: appIcon
            x: 10
            y: 25 - 14
            width: 28
            height: 28
            sourceSize: Qt.size(56, 56)
            source: body.iconOf(row.note)
            smooth: true
            mipmap: true
        }
        Text {
            anchors.centerIn: appIcon
            visible: appIcon.status !== Image.Ready
            text: (row.note.app || "?").slice(0, 1).toLocaleUpperCase()
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 15
            font.weight: Font.DemiBold
            color: body.dim
        }
        // The summary ends 8 px before "App · age": a long app name shortens it, it never runs
        // under the label.
        Text {
            objectName: "note-summary"
            x: 50
            y: 25 - 8 - 10
            width: Math.max(1, Math.min(row.width - 130, appAge.x - 8 - x))
            height: 20
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: row.note.summary || row.note.app
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
            color: body.ink
        }
        Text {
            id: message
            objectName: "note-body"
            x: 50
            y: 25
            width: Math.max(1, row.width - 130)
            height: body.noteBodyHeight(row.note)
            verticalAlignment: row.note.critical ? Text.AlignTop : Text.AlignVCenter
            elide: row.note.critical ? Text.ElideNone : Text.ElideRight
            wrapMode: Text.Wrap
            maximumLineCount: body.bodyLineLimit(row.note)
            text: body.bodyText(row.note)
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.dim
        }
        Text {
            id: appAge
            x: row.width - 12 - width
            y: 25 - 8 - 10
            height: 20
            verticalAlignment: Text.AlignVCenter
            text: row.note.app + " · " + body.age(row.note.time)
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: body.dim
        }
        GlassTarget {
            id: dismiss
            glass: body.glass
            owner: row
            key: row.key + ":x"
            label: "Dismiss"
            x: row.width - 30
            y: 25 + 10 - 12
            width: 24
            height: 24
            bubblePad: 0
            bubbleRadius: 12
            // A held notice's preview stays until it is acted on or dismissed: its close is
            // always there.
            visible: row.peek ? row.note.held === true : row.open
            onClicked: body.store.dismiss([row.note.id])
            SymbolIcon {
                id: closeIcon
                x: 5
                y: 5
                width: 14
                height: 14
                name: "window-close-symbolic"
                ink: body.dim
            }
            Text {
                anchors.fill: parent
                visible: !closeIcon.found
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: "×"
                textFormat: Text.PlainText
                font.pixelSize: 14
                color: body.dim
            }
        }
        Row {
            x: 40
            y: message.y + message.height + 5
            height: 26
            spacing: 2
            Repeater {
                model: row.actions
                GlassTarget {
                    id: actionWord
                    required property var modelData
                    required property int index
                    glass: body.glass
                    owner: row
                    key: row.key + ":a" + index
                    label: modelData.text
                    width: actionLabel.implicitWidth + 20
                    height: 26
                    onClicked: {
                        if (body.store.activate(row.note.id, actionWord.modelData.id))
                            body.activated();
                    }
                    Text {
                        id: actionLabel
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: .5
                        text: actionWord.modelData.text
                        textFormat: Text.PlainText
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 13
                        font.weight: Font.Medium
                        color: body.accent
                    }
                }
            }
        }
    }
    component Word: GlassTarget {
        id: word
        property color color: body.accent
        glass: body.glass
        implicitWidth: wordLabel.implicitWidth + 20
        implicitHeight: 26
        width: implicitWidth
        height: 26
        Text {
            id: wordLabel
            anchors.centerIn: parent
            anchors.verticalCenterOffset: .5
            text: word.label
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: word.color
        }
    }
    Flickable {
        id: list
        parent: scrollContent
        visible: body.opened
        y: body.listTop
        width: parent.width
        height: body.plan.height
        contentHeight: body.plan.height
        contentWidth: width
        clip: true
        interactive: false
        WheelHandler {
            target: null
            onWheel: event => {
                const delta = event.pixelDelta.y || event.angleDelta.y / 3;
                if (delta < 0)
                    body.historyExpanded = true;
                Qt.callLater(() => {
                    mainScroll.contentY = Math.max(0, Math.min(mainScroll.contentHeight - mainScroll.height, mainScroll.contentY - delta));
                });
                event.accepted = true;
            }
        }
        Repeater {
            model: body.opened ? body.plan.items : []
            Item {
                id: entry
                required property var modelData
                readonly property string kind: modelData.kind
                y: modelData.y
                width: list.width
                height: 50
                Text {
                    visible: entry.kind === "bucket"
                    x: body.side
                    height: 18
                    verticalAlignment: Text.AlignVCenter
                    text: entry.modelData.text ?? ""
                    textFormat: Text.PlainText
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: body.dim
                }
                Text {
                    visible: entry.kind === "empty"
                    x: body.side
                    width: body.inner
                    height: 28
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: "No notifications"
                    textFormat: Text.PlainText
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    color: body.dim
                }
                Loader {
                    active: entry.kind === "note"
                    x: body.side
                    width: body.inner
                    sourceComponent: NoteRow {
                        width: body.inner
                        note: entry.modelData.note
                    }
                }
                Text {
                    visible: entry.kind === "more"
                    x: body.side + 10
                    width: body.inner - 200
                    height: 26
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    text: entry.kind === "hidden" ? "+" + entry.modelData.count + " more" : entry.kind === "more" ? (entry.modelData.group.expanded ? entry.modelData.group.count + " from " + entry.modelData.group.app : "+" + (entry.modelData.group.count - 1) + " more from " + entry.modelData.group.app) : ""
                    textFormat: Text.PlainText
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    color: body.dim
                }
                Word {
                    visible: entry.kind === "hidden"
                    x: body.side
                    key: entry.kind === "hidden" ? "history-more" : ""
                    label: entry.kind === "hidden" ? "+" + entry.modelData.count + " more" : ""
                    color: body.dim
                    onClicked: body.revealHistory(entry.modelData.y)
                }
                Loader {
                    active: entry.kind === "more"
                    x: body.side + body.inner - implicitWidth
                    sourceComponent: Row {
                        spacing: 4
                        Word {
                            key: "more-" + entry.modelData.group.key
                            label: entry.modelData.group.expanded ? "Show less" : "Show all"
                            onClicked: body.store.expand(entry.modelData.group.key, !entry.modelData.group.expanded)
                        }
                        Word {
                            key: "clear-" + entry.modelData.group.key
                            label: "Clear"
                            onClicked: body.store.dismiss(entry.modelData.group.ids)
                        }
                    }
                }
                Loader {
                    active: entry.kind === "clear"
                    x: body.side + body.inner - implicitWidth
                    sourceComponent: Word {
                        key: "clear-all"
                        label: "Clear all"
                        onClicked: body.store.dismiss(body.store.entries.map(n => n.id))
                    }
                }
            }
        }
    }
    Text {
        parent: scrollContent
        visible: body.opened && body.actionNote !== ""
        x: body.side
        y: body.listTop + list.height
        width: body.inner
        height: 18
        verticalAlignment: Text.AlignVCenter
        text: body.actionNote
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        font.weight: Font.Medium
        color: body.dim
    }

    // ---- Peek: under the island's line (the panel draws it), what just arrived ----
    Loader {
        active: !body.opened && body.peekNotes.length === 1
        x: body.side
        y: 40
        width: body.inner
        sourceComponent: NoteRow {
            width: body.inner
            peek: true
            note: body.peekNotes[0]
        }
    }
    Item {
        visible: !body.opened && body.peekNotes.length > 1
        x: body.side
        y: 40
        width: body.inner
        height: 70
        Text {
            width: parent.width
            height: 20
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: body.peekApps.length === 1 ? body.peekApps[0] + " · " + body.peekNotes.length + " new" : body.peekNotes.length + " new notifications"
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
            color: body.ink
        }
        Text {
            y: 22
            width: parent.width
            height: 18
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: body.peekApps.length === 1 ? (body.peekNotes[body.peekNotes.length - 1]?.summary ?? "") : body.peekApps.slice(0, 3).join(", ")
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.dim
        }
        Word {
            x: -10
            y: 44
            key: "peek-all"
            label: "Click to see all"
            onClicked: body.openHistory()
        }
    }
}
