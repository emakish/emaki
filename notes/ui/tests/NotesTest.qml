// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
ShellRoot {
    Window {
        id: window
        width: 960; height: 700; visible: true
        color: ShellPalette.managerBackground
        Rectangle { anchors.fill: parent; color: ShellPalette.managerBackground }
        NotesView { id: view; anchors.fill: parent }
        NotesIsland { id: island; x: 570; y: 10; width: implicitWidth; height: implicitHeight }
        TestCase {
            name: "Notes"
            when: window.visible
            function test_notes() {
                SettingsBridge.values = [{key: "appearance.theme", value: Quickshell.env("NOTES_TEST_THEME") || "light"}];
                tryCompare(NotesService, "ready", true, 5000);
                compare(island.visible, false);
                view.create();
                tryVerify(() => !!view.selectedId, 5000);
                const id = view.selectedId;
                view.draft = "Weekend\n- [ ] Read a book\n- [x] Walk outside";
                view.save(false);
                tryCompare(view, "pendingId", "", 5000);
                compare(NotesService.find(id).body, view.draft);
                view.editing = true;
                view.draft += "\nSubmitted text";
                const submitted = view.draft;
                view.save(false);
                verify(!!view.pendingId);
                view.draft += "\nTyped while saving";
                tryCompare(view, "pendingId", "", 5000);
                compare(view.savedBody, submitted);
                verify(view.draft.endsWith("Typed while saving"));
                verify(view.dirty);
                verify(view.editing);
                view.save(false);
                tryCompare(view, "pendingId", "", 5000);
                verify(!view.dirty);
                verify(view.editing);
                view.send("notes.pin", {noteId: id, revision: view.revision, pinned: true});
                tryCompare(view, "pendingId", "", 5000);
                compare(island.visible, true);
                compare(island.width, 40);
                island.expanded = true;
                tryCompare(island, "expansion", 1, 1000);
                compare(island.width, 360);
                view.draft += "\nLocal draft";
                NotesService.request("notes.addTask", {noteId: id, revision: view.revision, text: "Elsewhere"});
                tryCompare(view, "conflict", true, 5000);
                verify(view.draft.endsWith("Local draft"));
                wait(750);
                verify(view.conflict);
                verify(!NotesService.find(id).body.includes("Local draft"));
                view.save(true);
                tryCompare(view, "pendingId", "", 5000);
                verify(view.selectedId !== id);
                verify(NotesService.find(view.selectedId).body.endsWith("Local draft"));
                compare(NotesService.notes.length, 2);
                island.taskDrafts = ({[id]: "Submitted task"});
                island.addTask(NotesService.find(id), "Submitted task");
                island.taskDrafts = ({[id]: "Next task"});
                tryVerify(() => !Object.keys(island.pendingAdds).length, 5000);
                compare(island.taskDrafts[id], "Next task");
                // Enter adds the task; the snapshot and result keep the field focused and empty it.
                island.taskDrafts = ({});
                const row = findChild(island, "pinnedNote:" + id);
                verify(row !== null);
                const task = findChild(row, "pinnedTask");
                task.forceActiveFocus();
                for (const key of "Buy milk")
                    keyClick(key);
                compare(task.text, "Buy milk");
                keyClick(Qt.Key_Return);
                tryVerify(() => NotesService.find(id).body.includes("- [ ] Buy milk"), 5000);
                tryCompare(task, "text", "", 5000);
                verify(findChild(island, "pinnedNote:" + id) === row);
                verify(task.activeFocus, "the add-task field keeps focus after Enter");
                island.unpin(NotesService.find(id));
                tryCompare(island, "visible", false, 5000);
                view.draft += "\nSaved as I type";
                tryVerify(() => !view.dirty && !view.pendingId, 5000);
                verify(NotesService.find(view.selectedId).body.endsWith("Saved as I type"));
                const previous = view.selectedId;
                view.compact = true;
                view.select("");
                view.quickText = "A quick thought";
                if (Quickshell.env("NOTES_TEST_SHOT")) {
                    window.width = 560;
                    wait(50);
                    let compactSaved = false;
                    verify(window.contentItem.grabToImage(result => compactSaved = result.saveToFile(Quickshell.env("NOTES_TEST_SHOT").replace(".png", "-compact.png"))));
                    tryVerify(() => compactSaved, 5000);
                    window.width = 960;
                }
                view.quickCreate();
                tryVerify(() => !!view.selectedId && view.selectedId !== previous && !view.pendingId, 5000);
                compare(NotesService.find(view.selectedId).body, "A quick thought");
                compare(view.quickText, "");
                view.compact = false;
                view.select(previous);
                wait(100);
                if (Quickshell.env("NOTES_TEST_SHOT")) {
                    let saved = false;
                    verify(window.contentItem.grabToImage(result => saved = result.saveToFile(Quickshell.env("NOTES_TEST_SHOT"))));
                    tryVerify(() => saved, 5000);
                }
                // The in-place editor and rendered tasks share one scroll surface.
                view.draft = "Long note\n" + Array.from({length: 80}, (_, i) => "- [ ] Line " + i).join("\n");
                const writer = findChild(view, "notesEditScroll");
                tryVerify(() => writer.visible && writer.contentHeight > writer.height + 100, 2000, "the editor scrolls a long note");
                tryVerify(() => !view.dirty && !view.pendingId, 5000);
                // Narrow columns keep a full-width list and a reachable editor/back action.
                window.width = 390;
                view.select("");
                tryVerify(() => !view.split && findChild(view, "notesList").visible, 2000);
                view.select(previous);
                tryVerify(() => writer.visible && !findChild(view, "notesList").visible, 2000);
                const back = findChild(view, "notesBack");
                verify(back.visible);
                mouseClick(back);
                tryVerify(() => findChild(view, "notesList").visible, 2000);
                view.select(previous);
                window.width = 320;
                wait(50);
                const body = findChild(view, "notesBody");
                verify(body.width > 240 && body.width <= window.width);
                body.forceActiveFocus();
                body.cursorPosition = body.length;
                keyClick(Qt.Key_End, Qt.ControlModifier);
                keyClick("!");
                tryVerify(() => NotesService.find(previous).body.endsWith("!"), 5000);
                // Task actions keep their source-line identity after the title/body split.
                view.draft = "Tasks\nA short introduction\n- [ ] One task";
                tryVerify(() => !view.dirty && !view.pendingId, 5000);
                findChild(view, "notesChecklist").toggled(2);
                tryVerify(() => NotesService.find(previous).body.includes("- [x] One task"), 5000);
                console.log("NOTES_TEST_PASS");
            }
            onCompletedChanged: if (completed) console.log("NOTES_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
