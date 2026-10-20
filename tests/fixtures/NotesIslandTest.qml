// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        width: 1280
        height: 800
        visible: true
        Rectangle {
            anchors.fill: parent
            color: ShellPalette.dark ? "#25222b" : "#ddd7df"
        }
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: niri
            headless: true
            testWidth: window.width
            testHeight: window.height
            borderMode: "soft"
            reservedSpace: 52
            skipIntro: true
        }
        TestCase {
            id: test
            name: "NotesIsland"
            when: window.visible
            function test_visibility_morph_and_outside() {
                // The absent helper exits at once: restarts back off and stop with the service.
                tryVerify(() => NotesService.retryMs >= 4000, 5000);
                verify(NotesService.reconnect.running);
                verify(NotesService.reconnect.interval >= 2000);
                NotesService.active = false;
                verify(!NotesService.reconnect.running);
                verify(!NotesService.startupTimeout.running);
                NotesService.notes = [];
                wait(50);
                NotesService.error = "";
                verify(!scene.pinnedNotes.visible);
                NotesService.notes = [
                    {
                        id: "fixture",
                        body: "Weekend plans\n- [ ] Visit the library\n- [x] Pack a notebook",
                        title: "Weekend plans",
                        revision: "one",
                        pinned: true
                    }
                ];
                wait(40);
                verify(scene.pinnedNotes.visible);
                compare(scene.pinnedNotes.width, 40);
                scene.openDrawer();
                wait(900);
                verify(scene.drawerOpen);
                verify(scene.pinnedNotes.x >= scene.clockPanel.x + scene.clockPanel.width + 11, "the collapsed notes glyph stays beyond the open shade");
                const clickedX = scene.pinnedNotes.x;
                scene.outsidePress(clickedX + 20, scene.pinnedNotes.y + 18);
                verify(Math.abs(scene.pinnedNotes.x - clickedX) < 1, "opening starts at the clicked glyph without a horizontal jump");
                verify(scene.pinnedNotesOpen);
                verify(scene.modalOpen);
                wait(Metrics.morphMs / 2);
                verify(scene.pinnedNotes.width > 40 && scene.pinnedNotes.width < 360, "the island grows over the shared morph time");
                tryCompare(scene.pinnedNotes, "width", 360, 1000);
                compare(scene.pinnedNotes.height, 400);
                // A snapshot updates the open island in place: the same rows, focus kept.
                const row = findChild(scene.pinnedNotes, "pinnedNote:fixture");
                verify(row !== null);
                const task = findChild(row, "pinnedTask");
                task.forceActiveFocus();
                verify(task.activeFocus);
                NotesService.notes = [
                    {
                        id: "fixture",
                        body: "Weekend plans\n- [x] Visit the library\n- [x] Pack a notebook",
                        title: "Weekend plans",
                        revision: "two",
                        pinned: true
                    }
                ];
                wait(20);
                verify(findChild(scene.pinnedNotes, "pinnedNote:fixture") === row, "a snapshot keeps the note's controls");
                compare(row.revision, "two");
                verify(task.activeFocus, "a snapshot keeps the add-task field's focus");
                verify(scene.pinnedNotes.x + scene.pinnedNotes.width <= window.width - Metrics.side + 1, "the growing plate remains inside the output");
                if (Quickshell.env("NOTES_SHOT")) {
                    let saved = false;
                    verify(window.contentItem.grabToImage(result => {
                        saved = result.saveToFile(Quickshell.env("NOTES_SHOT"));
                    }));
                    tryVerify(() => saved);
                }
                scene.outsidePress(20, 700);
                verify(!scene.pinnedNotesOpen);
                tryCompare(scene.pinnedNotes, "width", 40, 1000);
                verify(!scene.pinnedNotesPresent);
                compare(findChild(scene.pinnedNotes, "pinnedNote:fixture"), null, "the collapsed glyph builds no note controls");
                scene.togglePinnedNotes(true);
                compare(scene.keyboardSurface, "notes");
                tryCompare(scene.pinnedNotes, "width", 360, 1000);
                NotesService.notes = [];
                wait(Metrics.morphMs);
                verify(!scene.pinnedNotes.visible);
                verify(!scene.pinnedNotesOpen);
                verify(scene.keyboardSurface !== "notes", "the last unpin hands the keyboard back");
            }
            onCompletedChanged: if (completed)
                console.log("NOTES_ISLAND_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
