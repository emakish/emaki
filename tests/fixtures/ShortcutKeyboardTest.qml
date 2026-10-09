// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    Window {
        id: window
        width: 700
        height: 600
        visible: true
        property int closed: 0
        ShortcutSheet {
            id: sheet
            anchors.fill: parent
            onCloseRequested: window.closed++
        }
        TestCase {
            name: "ShortcutKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SHORTCUT_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function test_keys() {
                window.requestActivate();
                sheet.takeFocus();
                tryCompare(sheet, "loadState", "ready");
                const search = findChild(sheet, "shortcutSearch");
                const list = findChild(sheet, "shortcutList");
                const close = findChild(sheet, "shortcutClose");
                verify(search.activeFocus);
                keyClick(Qt.Key_D);
                verify(sheet.filtered.length < sheet.rows.length);
                keyClick(Qt.Key_Backspace);
                keyClick(Qt.Key_Tab);
                verify(list.activeFocus);
                keyClick(Qt.Key_Down);
                compare(list.currentIndex, 1);
                keyClick(Qt.Key_PageDown);
                compare(list.currentIndex, 6);
                keyClick(Qt.Key_Tab);
                verify(close.activeFocus);
                keyClick(Qt.Key_Tab);
                verify(search.activeFocus);
                keyClick(Qt.Key_Backtab);
                verify(close.activeFocus);
                keyClick(Qt.Key_Escape);
                compare(window.closed, 1);
            }
        }
    }
}
