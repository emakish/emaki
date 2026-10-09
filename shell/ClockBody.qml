pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "Keyboard.js" as Keyboard

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
    readonly property alias scroller: list
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
    readonly property real notesTop: calendarTop + calendar.height + 16
    readonly property bool serverNote: serverState !== "active"
    readonly property real listTop: notesTop + 36 + (serverNote ? 22 : 0)
    readonly property string actionNote: store.actionState === "action_unavailable" ? "This notification has no available action" : ""
    // The panel is not a page (clock.js): at most six rows, fewer if the screen is short;
    // the rest is a count.
    readonly property var plan: {
        if (keyboardMode)
            return notePlan(Math.max(1, store.count));
        let chosen = null;
        for (let cap = 6; cap >= 1; --cap) {
            chosen = notePlan(cap);
            if (listTop + chosen.height + (actionNote ? 22 : 0) + 14 <= maxHeight)
                break;
        }
        return chosen;
    }
    readonly property real drawerHeight: Math.min(maxHeight, listTop + plan.height + (actionNote ? 22 : 0) + 14)
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
    property var bodyHeights: ({})
    Component.onCompleted: Qt.callLater(measureBodies)
    onInnerChanged: Qt.callLater(measureBodies)
    Connections {
        target: body.store
        function onEntriesChanged(): void {
            Qt.callLater(body.measureBodies);
        }
    }
    function measureBodies(): void {
        const heights = {};
        for (const note of store.entries) {
            if (!note.critical || !note.body)
                continue;
            const measure = bodyMeasure.createObject(null, {
                width: Math.max(1, inner - 130),
                text: note.body
            }) as Text;
            heights[note.id] = Math.max(20, Math.ceil(measure.implicitHeight));
            measure.destroy();
        }
        bodyHeights = heights;
    }
    function noteBodyHeight(note: var): real {
        return note.critical && note.body ? (bodyHeights[note.id] ?? 20) : 20;
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
    // The app's icon: the one the notification sent, else its desktop entry by name.
    function iconOf(note: var): string {
        const icon = note?.icon ?? "";
        if (icon.startsWith("/"))
            return "file://" + icon;
        if (icon.startsWith("file:") || icon.startsWith("image:"))
            return icon;
        const path = icon ? Quickshell.iconPath(icon, true) : "";
        if (path)
            return path;
        const app = String(note?.app ?? "").toLocaleLowerCase();
        const entry = app ? DesktopEntries.heuristicLookup(note.app) || DesktopEntries.applications.values.find(e => e.name.toLocaleLowerCase() === app) : null;
        return entry?.icon ? Quickshell.iconPath(entry.icon, true) : "";
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

    // ---- Media ----
    Loader {
        id: media
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
        visible: body.opened
        x: body.side
        y: body.calendarTop
        width: body.inner
        glass: body.glass
        today: body.today
    }

    // ---- Notifications: heading, Do not disturb, the groups ----
    Item {
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
            x: body.side + body.inner - 38
            y: 4
            width: 38
            height: 20
            onClicked: body.store.dnd = !body.store.dnd
            Rectangle {
                anchors.fill: parent
                radius: 10
                color: body.store.dnd ? body.accent : body.faint
            }
            Accessible.role: Accessible.CheckBox
            Accessible.checked: body.store.dnd
        }
    }
    // The knob's place (clock.js 'knob'): 20 × 22 at the track's left or right end.
    readonly property rect knobRect: Qt.rect(side + inner - 38 + (store.dnd ? 18 : 0), notesTop + 3, 20, 22)
    readonly property bool knobShown: opened
    Text {
        visible: body.opened && body.serverNote
        x: body.side
        y: body.notesTop + 36
        width: body.inner
        height: 18
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: body.serverState === "owned_elsewhere" ? "Notifications are shown by another program" : body.serverState === "disabled" ? "Notification server disabled" : "Notification service: " + body.serverState
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
        readonly property bool open: activeFocus || dismiss.activeFocus || body.focusedKey(row).length > 0 || (body.glass ? body.glass.hoverKey === key || body.glass.hoverKey.startsWith(key + ":") : false)
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
        Text {
            x: 50
            y: 25 - 8 - 10
            width: row.width - 130
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
            x: 50
            y: 25
            width: Math.max(1, row.width - 130)
            height: body.noteBodyHeight(row.note)
            verticalAlignment: row.note.critical ? Text.AlignTop : Text.AlignVCenter
            elide: row.note.critical ? Text.ElideNone : Text.ElideRight
            wrapMode: row.note.critical ? Text.Wrap : Text.NoWrap
            maximumLineCount: row.note.critical ? 2147483647 : 1
            text: row.note.critical ? (row.note.body || "") : (row.note.body || "").replace(/\s+/g, " ")
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.dim
        }
        Text {
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
            visible: row.open && !row.peek
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
        visible: body.opened
        y: body.listTop
        width: parent.width
        height: body.keyboardMode ? Math.max(48, body.maxHeight - body.listTop - 14 - (body.actionNote ? 22 : 0)) : body.plan.height
        contentHeight: body.plan.height
        contentWidth: width
        clip: body.keyboardMode
        interactive: body.keyboardMode
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
                    visible: entry.kind === "more" || entry.kind === "hidden"
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
        visible: body.opened && body.actionNote !== ""
        x: body.side
        y: body.listTop + body.plan.height
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
