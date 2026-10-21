// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Liquid.js" as Liquid

Item {
    id: root
    property var taskDrafts: ({})
    property var pendingAdds: ({})
    function unpin(note): void { NotesService.request("notes.pin", {noteId: note.id, pinned: false, revision: note.revision}); }
    function addTask(note, text): void {
        const id = NotesService.request("notes.addTask", {noteId: note.id, text: text.trim(), revision: note.revision});
        if (id) pendingAdds[id] = {noteId: note.id, text: text};
    }
    Connections {
        target: NotesService
        function onCompleted(result): void {
            const pending = root.pendingAdds[result.id];
            if (!pending) return;
            if (result.ok && root.taskDrafts[pending.noteId] === pending.text) {
                const next = Object.assign({}, root.taskDrafts); delete next[pending.noteId]; root.taskDrafts = next;
            }
            delete root.pendingAdds[result.id];
        }
    }
    property bool expanded: false
    property real expansion: expanded ? 1 : 0
    // Keyboard focus on the bar's cell for this island (StaticBar): the island draws it.
    property bool keyboardFocused: false
    property DockBackdrop backdrop: null
    property point screenOrigin: Qt.point(0, 0)
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    // The list arrives on the second half of the morph, as in the other panels.
    readonly property real contentAlpha: Liquid.smooth((expansion - .55) / .45)
    implicitWidth: 40 + 320 * expansion
    implicitHeight: 36 + 364 * expansion
    visible: NotesService.pinnedNotes.length > 0
    signal openRequested(string noteId)
    signal toggleRequested()
    readonly property real radius: 14 + 2 * expansion
    function takeFocus(keyboard): void { if (keyboard) glyph.forceActiveFocus(); else root.forceActiveFocus(); }
    Behavior on expansion {
        NumberAnimation {
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    onVisibleChanged: if (!visible) expanded = false
    Keys.onEscapePressed: expanded = false
    Rectangle { anchors.fill: parent; radius: 16; visible: !root.glassReady; color: LiquidPalette.flatPanel; border.width: 1; border.color: LiquidPalette.flatDropRim }
    IslandGlass {
        visible: root.glassReady
        backdrop: root.backdrop
        plate: Qt.rect(0, 0, root.width, root.height)
        uHasIcons: 0
        uRadius: 16
        uSceneSize: Qt.point(root.width, root.height)
        uCover: root.backdrop ? root.backdrop.coverFor(root.screenOrigin) : Qt.vector4d(0, 0, 1, 1)
    }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }
    Item {
        anchors.fill: parent
        clip: true
        CanvasTint { x: 0; y: 36; width: parent.width; height: Math.max(0, parent.height - 36); opacity: root.expansion; radius: 16 }
        Button {
            id: glyph
            x: 0; y: 0; width: 40; height: 36
            Accessible.name: root.expanded ? qsTranslate("NotesIsland", "Collapse pinned notes") : qsTranslate("NotesIsland", "Open pinned notes")
            onClicked: { root.toggleRequested(); root.forceActiveFocus(); }
            contentItem: Text { text: "▤"; color: LiquidPalette.ink; font.pixelSize: 22; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            // The bar's keyboard focus gets the hover drop (FocusRing's look), drawn here above the bar.
            background: Rectangle {
                radius: 14
                color: root.keyboardFocused && !root.expanded ? LiquidPalette.flatDrop : glyph.hovered ? Qt.alpha(LiquidPalette.ink, .08) : "transparent"
                border.width: glyph.activeFocus ? 2 : root.keyboardFocused && !root.expanded ? 1 : 0
                border.color: glyph.activeFocus ? LiquidPalette.accent : LiquidPalette.flatDropRim
            }
        }
        Text { x: 48; y: 10; text: qsTranslate("NotesIsland", "Pinned notes"); opacity: root.contentAlpha; color: LiquidPalette.ink; font.family: "Noto Sans"; font.pixelSize: 13; font.weight: Font.DemiBold }
        // Built only while open: the collapsed glyph carries no note controls on any output.
        Loader {
            x: 16; y: 48; width: 328; height: Math.max(0, root.height - 64)
            active: root.expanded || root.expansion > 0
            opacity: root.contentAlpha
            visible: opacity > .001
            sourceComponent: list
        }
    }
    Component {
        id: list
        ScrollView {
            clip: true
            Column {
                id: content
                width: 312
                spacing: 18
                // Updated in place by note id: a snapshot (autosave, tick, add) keeps every
                // delegate, so focus and the add-task field survive it.
                ListModel { id: rows }
                function sync(): void {
                    const notes = NotesService.pinnedNotes;
                    for (let i = 0; i < notes.length; ++i) {
                        const note = notes[i];
                        let j = i;
                        while (j < rows.count && rows.get(j).noteId !== note.id) ++j;
                        if (j === rows.count) {
                            rows.insert(i, {noteId: note.id, body: note.body, revision: note.revision});
                            continue;
                        }
                        if (j !== i) rows.move(j, i, 1);
                        rows.set(i, {body: note.body, revision: note.revision});
                    }
                    if (rows.count > notes.length) rows.remove(notes.length, rows.count - notes.length);
                }
                Component.onCompleted: sync()
                Connections {
                    target: NotesService
                    function onPinnedNotesChanged(): void { content.sync(); }
                }
                Text { width: parent.width; visible: !!NotesService.error; text: NotesService.error; wrapMode: Text.Wrap; color: LiquidPalette.danger; font.pixelSize: 12 }
                Repeater {
                    model: rows
                    delegate: Column {
                        id: note
                        required property string noteId
                        required property string body
                        required property string revision
                        readonly property var ref: ({id: noteId, revision: revision})
                        // A ticked line rebuilds the checklist's rows: give the keyboard its box back.
                        property int refocusLine: -1
                        objectName: "pinnedNote:" + noteId
                        width: 312
                        spacing: 10
                        onBodyChanged: if (refocusLine >= 0) Qt.callLater(() => {
                            for (const row of checklist.children)
                                if (row.index === note.refocusLine && row.children.length)
                                    row.children[0].forceActiveFocus();
                            note.refocusLine = -1;
                        })
                        NotesChecklist {
                            id: checklist
                            width: parent.width
                            body: note.body
                            onToggled: line => {
                                const keyboard = Window.activeFocusItem && Window.activeFocusItem.parent && Window.activeFocusItem.parent.parent === checklist;
                                note.toggleId = NotesService.request("notes.toggle", {noteId: note.noteId, line: line, revision: note.revision});
                                note.refocusLine = keyboard && note.toggleId ? line : -1;
                            }
                        }
                        // A refused tick leaves the body as it was: forget the box to return to.
                        property string toggleId: ""
                        Connections {
                            target: NotesService
                            function onCompleted(result): void {
                                if (result.id !== note.toggleId) return;
                                note.toggleId = "";
                                if (!result.ok) note.refocusLine = -1;
                            }
                        }
                        RowLayout {
                            width: parent.width
                            TextField {
                                id: task
                                objectName: "pinnedTask"
                                Layout.fillWidth: true
                                text: root.taskDrafts[note.noteId] || ""
                                onTextEdited: { const next = Object.assign({}, root.taskDrafts); next[note.noteId] = text; root.taskDrafts = next; }
                                placeholderText: qsTranslate("NotesIsland", "Add a task…")
                                placeholderTextColor: LiquidPalette.plateDim
                                color: LiquidPalette.ink
                                font.pixelSize: 12
                                implicitHeight: 40
                                padding: 12
                                background: Rectangle { color: Qt.alpha(LiquidPalette.ink, .05); radius: 8; border.width: task.activeFocus ? 1 : 0; border.color: LiquidPalette.accent }
                                onAccepted: add.clicked()
                            }
                            NotesButton {
                                id: add
                                implicitHeight: 40
                                text: "+"
                                Accessible.name: qsTranslate("NotesIsland", "Add task")
                                enabled: !!task.text.trim()
                                onClicked: root.addTask(note.ref, task.text)
                            }
                        }
                        RowLayout {
                            width: parent.width
                            NotesButton { text: qsTranslate("NotesIsland", "Open in Emaki Notes"); onClicked: root.openRequested(note.noteId) }
                            Item { Layout.fillWidth: true }
                            NotesButton { text: qsTranslate("NotesIsland", "Unpin"); onClicked: root.unpin(note.ref) }
                        }
                        Rectangle { width: parent.width; height: 1; color: Qt.alpha(LiquidPalette.ink, .10) }
                    }
                }
            }
        }
    }
}
