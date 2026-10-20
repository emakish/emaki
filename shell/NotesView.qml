// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "SystemIcons.js" as SystemIcons

Item {
    id: root
    property bool compact: false
    property bool pinnedOnly: false
    property string selectedId: ""
    property string draft: ""
    property string savedBody: ""
    property string revision: ""
    property bool editing: true
    property bool showList: true
    property bool conflict: false
    property bool confirmDelete: false
    property string pendingId: ""
    property string pendingAction: ""
    property string submittedDraft: ""
    property string message: ""
    property alias quickText: quick.text
    property string submittedQuickText: ""
    onDraftChanged: {
        if (selectedId && draft !== savedBody && !conflict) autosave.restart();
    }
    onConflictChanged: if (conflict) autosave.stop()
    Timer {
        id: autosave
        interval: 600
        onTriggered: if (root.dirty && root.selectedId && !root.conflict && !root.pendingId && NotesService.ready) root.save(false)
    }
    readonly property bool dirty: draft !== savedBody
    readonly property var selected: NotesService.find(selectedId)
    readonly property bool split: !compact && width >= 720
    readonly property var filtered: (pinnedOnly ? NotesService.pinnedNotes : NotesService.notes).filter(note => (note.body || "").toLowerCase().includes(search.text.toLowerCase()))
    signal openRequested(string noteId)
    function select(noteId): void {
        if (noteId && noteId === selectedId) { showList = false; return; }
        if (dirty || pendingId) { message = "Save your draft or reload before changing notes."; return; }
        selectedId = noteId; showList = !noteId; reload();
    }
    function reload(): void {
        const note = NotesService.find(selectedId);
        draft = note ? note.body : ""; savedBody = draft; revision = note ? note.revision : "";
        conflict = false; editing = true; confirmDelete = false; message = "";
    }
    function send(action, fields): void {
        if (pendingId) return;
        pendingAction = action;
        submittedDraft = draft;
        pendingId = NotesService.request(action, fields);
    }
    function save(copy): void {
        send(copy ? "notes.create" : "notes.save", copy ? {body: draft} : {noteId: selectedId, body: draft, revision: revision});
    }
    function create(body): void {
        if (dirty || pendingId) { message = "Save your draft or reload before creating a note."; return; }
        send("notes.create", {body: body || "New note\n"});
    }
    function quickCreate(): void {
        if (!quick.text.trim() || pendingId) return;
        create(quick.text.trim());
        if (pendingId && pendingAction === "notes.create") submittedQuickText = quick.text;
    }
    Connections {
        target: NotesService
        function onReadyChanged(): void {
            if (NotesService.ready && root.dirty && !root.conflict) autosave.restart();
        }
        function onNotesChanged(): void {
            const note = NotesService.find(root.selectedId);
            if (root.pendingId || (note && note.revision === root.revision)) return;
            if (root.dirty) {
                if (!note || note.revision !== root.revision) { root.conflict = true; root.message = "This note changed elsewhere. Your draft is safe. Reload or save a copy."; }
            } else { const editing = root.editing; root.reload(); root.editing = editing; }
        }
        function onCompleted(result): void {
            if (result.id !== root.pendingId) return;
            root.pendingId = "";
            if (!result.ok) { autosave.stop(); root.conflict = !!result.conflict; root.message = result.conflict ? "This note changed elsewhere. Your draft is safe. Reload or save a copy." : (result.error || "The change could not be saved."); return; }
            if (root.pendingAction === "notes.create") {
                root.selectedId = result.noteId;
                root.showList = false;
                if (root.submittedQuickText && quick.text === root.submittedQuickText) quick.text = "";
                root.submittedQuickText = "";
                root.reload();
                root.editing = true;
                titleEditor.forceActiveFocus();
            } else if (root.pendingAction === "notes.delete") {
                root.selectedId = "";
                root.showList = true;
                root.reload();
            } else {
                // A watcher snapshot precedes this response. Adopt its revision while
                // preserving anything typed after the request was sent.
                const note = NotesService.find(root.selectedId);
                const newerDraft = root.draft !== root.submittedDraft;
                root.savedBody = note ? note.body : root.submittedDraft;
                root.revision = note ? note.revision : root.revision;
                if (!newerDraft) root.draft = root.savedBody;
                root.conflict = false;
                root.message = "";
                if (root.dirty && !root.conflict) autosave.restart();
            }
        }
    }
    Shortcut { sequence: "Ctrl+N"; enabled: root.visible; onActivated: root.create() }
    Shortcut { sequence: "Ctrl+F"; enabled: root.visible; onActivated: { root.showList = true; search.forceActiveFocus(); } }
    Shortcut { sequence: "Ctrl+S"; enabled: root.visible && root.dirty && !root.conflict; onActivated: root.save(false) }
    component ActionButton: Button {
        id: action
        property string path
        property bool marked: false
        implicitWidth: 40; implicitHeight: 40
        padding: 10
        opacity: enabled ? 1 : .45
        Accessible.name: text
        ToolTip.visible: hovered || activeFocus
        ToolTip.delay: 500
        ToolTip.text: text
        contentItem: Image {
            source: "data:image/svg+xml;utf8," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + Qt.tint(ShellPalette.managerBackground, action.marked ? ShellPalette.accentForeground : ShellPalette.settingsDim) + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="' + action.path + '"/></svg>')
            sourceSize: Qt.size(40, 40)
        }
        background: Rectangle {
            radius: 10
            color: action.marked ? Qt.alpha(ShellPalette.accent, .15) : action.hovered ? ShellPalette.settingsDropHover : "transparent"
            border.width: action.activeFocus ? 2 : 0
            border.color: ShellPalette.accent
        }
    }
    Rectangle { anchors.fill: parent; visible: !root.compact; color: ShellPalette.managerBackground }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: root.split ? 260 : root.width
            Layout.minimumWidth: 0
            Layout.fillWidth: !root.split
            visible: root.split || root.showList || !root.selectedId
            color: root.compact ? "transparent" : ShellPalette.managerPanel
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: root.compact ? 0 : 16
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: root.compact ? 0 : 8
                    Layout.bottomMargin: 10
                    spacing: 10
                    Image { source: SystemIcons.notesSvg(ShellPalette.accent, ShellPalette.accentStrong, ShellPalette.accentSoft, ShellPalette.accentShade, ShellPalette.managerIconInk); sourceSize: Qt.size(32, 32); Layout.preferredWidth: 32; Layout.preferredHeight: 32; smooth: false }
                    Text { Layout.fillWidth: true; text: "Emaki Notes"; elide: Text.ElideRight; color: ShellPalette.settingsInk; font.family: "Noto Sans"; font.pixelSize: 16; font.weight: Font.DemiBold }
                }
                TextField {
                    id: search
                    objectName: "notesSearch"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    implicitHeight: 42
                    placeholderText: "Search notes"
                    placeholderTextColor: ShellPalette.settingsDim
                    color: ShellPalette.settingsInk
                    font.family: "Noto Sans"; font.pixelSize: 13
                    padding: 12
                    leftPadding: 36
                    background: Rectangle { radius: 10; color: ShellPalette.settingsField; border.width: 1; border.color: search.activeFocus ? ShellPalette.accent : ShellPalette.settingsRim }
                    Image {
                        x: 12; anchors.verticalCenter: parent.verticalCenter; width: 16; height: 16
                        source: "data:image/svg+xml;utf8," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="' + Qt.tint(ShellPalette.managerBackground, ShellPalette.settingsDim) + '" stroke-width="1.8" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>')
                    }
                }
                NotesButton { objectName: "notesNew"; Layout.fillWidth: true; implicitHeight: 40; text: "+  New note"; primary: true; enabled: NotesService.ready && !root.pendingId; onClicked: root.create(); Accessible.name: "New note" }
                RowLayout {
                    visible: root.compact
                    Layout.fillWidth: true
                    TextField {
                        id: quick
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        placeholderText: "Quick new note…"
                        placeholderTextColor: ShellPalette.settingsDim
                        color: ShellPalette.settingsInk
                        padding: 10
                        font.family: "Noto Sans"
                        background: Rectangle { radius: 10; color: ShellPalette.settingsField; border.width: quick.activeFocus ? 1 : 0; border.color: ShellPalette.accent }
                        onAccepted: root.quickCreate()
                    }
                    NotesButton { text: "+"; Accessible.name: "Create quick note"; enabled: !!quick.text.trim() && !root.pendingId && NotesService.ready; onClicked: root.quickCreate() }
                }
                Text { visible: !!root.message && (root.showList || !root.selectedId); Layout.fillWidth: true; text: root.message; wrapMode: Text.Wrap; color: LiquidPalette.danger; font.pixelSize: 12 }
                ListView {
                    id: list
                    objectName: "notesList"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                    clip: true; spacing: 5
                    model: root.filtered
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        id: entry
                        required property var modelData
                        width: list.width; height: 90
                        padding: 12
                        onClicked: root.select(modelData.id)
                        background: Rectangle { radius: 11; color: root.selectedId === entry.modelData.id ? Qt.alpha(ShellPalette.accent, .18) : entry.hovered ? ShellPalette.settingsDrop : "transparent"; border.width: entry.activeFocus ? 2 : 0; border.color: ShellPalette.accent }
                        contentItem: Column {
                            spacing: 5
                            Text { width: parent.width; text: (entry.modelData.pinned ? "⌖  " : "") + entry.modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: ShellPalette.settingsInk; font.family: "Noto Sans"; font.pixelSize: 14; font.weight: Font.DemiBold }
                            Text { width: parent.width; text: entry.modelData.body.split("\n").slice(1).find(line => line.trim()) || "No additional text"; textFormat: Text.PlainText; elide: Text.ElideRight; color: ShellPalette.settingsDim; font.family: "Noto Sans"; font.pixelSize: 12 }
                            Text { text: Qt.formatDateTime(new Date(entry.modelData.updated * 1000), "MMM d, yyyy"); color: ShellPalette.settingsFaint; font.family: "Noto Sans"; font.pixelSize: 11 }
                        }
                    }
                    Text { anchors.centerIn: parent; width: parent.width; visible: !list.count; text: search.text ? "No matching notes" : "A little space for your thoughts.\nCreate your first note."; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; color: ShellPalette.settingsDim; font.family: "Noto Sans"; font.pixelSize: 13; lineHeight: 1.5 }
                }
                Text { Layout.fillWidth: true; visible: !root.compact; text: "Local notes · " + root.filtered.length; color: ShellPalette.settingsFaint; font.family: "Noto Sans"; font.pixelSize: 11 }
            }
        }
        Rectangle { visible: root.split; Layout.fillHeight: true; width: 1; color: ShellPalette.settingsRim }
        ColumnLayout {
            id: mainPane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
            visible: root.split || (!root.showList && !!root.selectedId)
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: root.compact ? 0 : 16
                Layout.rightMargin: root.compact ? 0 : 16
                Layout.topMargin: 8; Layout.bottomMargin: 8
                spacing: 4
                ActionButton { objectName: "notesBack"; visible: !root.split; text: "Back to notes"; path: "M15 5l-7 7 7 7"; onClicked: root.showList = true }
                Text { Layout.fillWidth: true; Layout.minimumWidth: 0; elide: Text.ElideRight; text: root.selectedId ? root.pendingId ? "Saving…" : root.dirty ? "Unsaved draft" : "Saved on this device" : "Notes"; color: ShellPalette.settingsDim; font.family: "Noto Sans"; font.pixelSize: 12 }
                ActionButton { visible: !!root.selectedId; text: root.selected?.pinned ? "Unpin note" : "Pin note"; marked: !!root.selected?.pinned; path: "M8 3h8l-1 7 4 4H5l4-4z M12 14v7"; enabled: !root.pendingId && !root.dirty; onClicked: root.send("notes.pin", {noteId: root.selectedId, pinned: !root.selected.pinned, revision: root.revision}) }
                ActionButton { visible: !!root.selectedId; text: "Delete note"; path: "M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7"; enabled: !root.pendingId; onClicked: root.confirmDelete = !root.confirmDelete }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: ShellPalette.settingsRim }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                visible: root.confirmDelete || !!(root.message || NotesService.error) || root.conflict
                RowLayout {
                    visible: root.confirmDelete; Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: "Delete this note?"; wrapMode: Text.Wrap; color: ShellPalette.settingsInk; font.family: "Noto Sans" }
                    NotesButton { text: "Cancel"; onClicked: root.confirmDelete = false }
                    NotesButton { text: "Delete"; onClicked: root.send("notes.delete", {noteId: root.selectedId, revision: root.revision}) }
                }
                Text { Layout.fillWidth: true; visible: !!(root.message || NotesService.error); text: root.message || NotesService.error; wrapMode: Text.Wrap; color: LiquidPalette.danger; font.family: "Noto Sans"; font.pixelSize: 12 }
                RowLayout {
                    visible: root.conflict || !!root.message
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    NotesButton { text: "Reload"; enabled: !root.pendingId; onClicked: root.reload() }
                    NotesButton { text: root.conflict ? "Save a copy" : "Save"; primary: true; enabled: !root.pendingId; onClicked: root.save(root.conflict) }
                }
            }
            ScrollView {
                id: editScroll
                objectName: "notesEditScroll"
                visible: !!root.selectedId
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                contentWidth: availableWidth
                clip: true
                Column {
                    width: editScroll.availableWidth
                    topPadding: root.compact ? 16 : 32
                    bottomPadding: 32
                    spacing: 18
                    readonly property real inset: root.compact ? 4 : Math.min(38, width * .07)
                    readonly property real writingWidth: Math.max(0, Math.min(720, width - inset * 2))
                    TextArea {
                        id: titleEditor
                        objectName: "notesTitle"
                        width: parent.writingWidth
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: root.draft.split("\n")[0]
                        onTextChanged: if (activeFocus) { const lines = root.draft.split("\n"); lines[0] = text.replace(/\n/g, " "); root.draft = lines.join("\n"); }
                        Keys.onReturnPressed: editor.forceActiveFocus()
                        wrapMode: TextEdit.Wrap
                        selectByMouse: true
                        padding: 0
                        readOnly: !!root.pendingId && root.pendingAction !== "notes.save"
                        color: ShellPalette.settingsInk
                        selectionColor: ShellPalette.accent; selectedTextColor: ShellPalette.accentText
                        font.family: "Noto Sans"; font.pixelSize: root.compact ? 24 : 28; font.weight: Font.DemiBold
                        placeholderText: "Title"; placeholderTextColor: ShellPalette.settingsDim
                        background: Rectangle { color: "transparent" }
                    }
                    TextArea {
                        id: editor
                        objectName: "notesBody"
                        width: parent.writingWidth
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: root.draft.split("\n").slice(1).join("\n")
                        onTextChanged: if (activeFocus) root.draft = root.draft.split("\n")[0] + "\n" + text
                        // Inside the Column the area is no longer the ScrollView's flickable: follow the caret by hand.
                        onCursorRectangleChanged: if (activeFocus) {
                            const flick = editScroll.contentItem;
                            const top = y + cursorRectangle.y - 12;
                            const bottom = y + cursorRectangle.y + cursorRectangle.height + 12;
                            if (top < flick.contentY)
                                flick.contentY = Math.max(0, top);
                            else if (bottom > flick.contentY + flick.height)
                                flick.contentY = bottom - flick.height;
                        }
                        wrapMode: TextEdit.Wrap
                        selectByMouse: true
                        readOnly: !!root.pendingId && root.pendingAction !== "notes.save"
                        padding: 14
                        implicitHeight: Math.max(root.compact ? 140 : 220, contentHeight + topPadding + bottomPadding)
                        color: ShellPalette.settingsInk
                        selectionColor: ShellPalette.accent; selectedTextColor: ShellPalette.accentText
                        font.family: "Noto Sans"; font.pixelSize: 15
                        placeholderTextColor: ShellPalette.settingsDim
                        placeholderText: "Your thoughts…"
                        background: Rectangle { radius: 12; color: ShellPalette.settingsField; border.width: 1; border.color: editor.activeFocus ? ShellPalette.accent : ShellPalette.settingsRim }
                    }
                    NotesChecklist {
                        objectName: "notesChecklist"
                        width: parent.writingWidth
                        anchors.horizontalCenter: parent.horizontalCenter
                        body: root.draft
                        tasksOnly: true
                        interactive: !root.pendingId
                        onToggled: line => {
                            const lines = root.draft.split("\n");
                            lines[line] = lines[line].replace(/\[([ xX])\]/, (_, value) => value === " " ? "[x]" : "[ ]");
                            root.draft = lines.join("\n");
                            if (!root.conflict) root.save(false);
                        }
                    }
                    Text { width: parent.writingWidth; anchors.horizontalCenter: parent.horizontalCenter; text: "Use - [ ] for a task. Tick tasks below the text."; wrapMode: Text.Wrap; color: ShellPalette.settingsFaint; font.family: "Noto Sans"; font.pixelSize: 11 }
                }
            }
            Item {
                visible: !root.selectedId
                Layout.fillWidth: true; Layout.fillHeight: true
                Column {
                    anchors.centerIn: parent
                    width: Math.max(0, parent.width - 48)
                    spacing: 14
                    Icon { kind: "file"; ink: ShellPalette.accentForeground; width: 36; height: 36; anchors.horizontalCenter: parent.horizontalCenter }
                    Text { width: parent.width; text: "A little room to think"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; color: ShellPalette.settingsInk; font.family: "Noto Sans"; font.pixelSize: 22 }
                    Text { width: parent.width; text: "Select a note, or start a new one."; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; color: ShellPalette.settingsDim; font.family: "Noto Sans"; font.pixelSize: 13 }
                }
            }
            NotesButton { visible: root.compact && !!root.selectedId; Layout.alignment: Qt.AlignHCenter; text: "Open in Emaki Notes"; onClicked: root.openRequested(root.selectedId) }
        }
    }
}
