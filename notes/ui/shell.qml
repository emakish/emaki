// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//@ pragma UseQApplication
//@ pragma AppId emaki-notes
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: root
    readonly property bool translationsReady: Shell.Translation.initialize()
    property string initialNote: Quickshell.env("EMAKI_NOTES_ID") || ""
    Component.onCompleted: Quickshell.watchFiles = false
    Shell.SettingsCatalog { active: false; helper: Shell.Platform.dataDir + "/shell/helpers/launcher-tools.py" }
    FloatingWindow {
        id: window
        title: view.dirty ? qsTranslate("shell", "Emaki Notes — Unsaved draft") : qsTranslate("shell", "Emaki Notes")
        implicitWidth: 1040
        implicitHeight: 740
        minimumSize: Qt.size(320, 400)
        color: Shell.ShellPalette.managerBackground
        // Closing retains the window and draft until this process is opened again.
        onClosed: visible = false
        Shell.NotesView { id: view; anchors.fill: parent }
    }
    Connections {
        target: Shell.NotesService
        function onReadyChanged(): void {
            if (Shell.NotesService.ready && root.initialNote) { view.select(root.initialNote); root.initialNote = ""; }
        }
    }
    IpcHandler {
        target: "notes"
        function present(noteId: string): string {
            window.visible = false; window.visible = true; window.minimized = false;
            if (noteId) view.select(noteId);
            return "presented";
        }
    }
}
