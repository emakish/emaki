// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "Keyboard.js" as Keyboard

ShellRoot {
    Window {
        id: window
        width: 500
        height: 400
        visible: true
        Item {
            id: grid
            anchors.fill: parent
            property bool keyboardBoundary: true
            property bool keyboardMode: false
            Rectangle {
                x: 20
                y: 20
                width: 80
                height: 40
                KeyboardTarget {
                    id: a
                    label: "A"
                }
            }
            Rectangle {
                x: 140
                y: 20
                width: 80
                height: 40
                KeyboardTarget {
                    id: b
                    label: "B"
                }
            }
            Rectangle {
                x: 20
                y: 100
                width: 80
                height: 40
                KeyboardTarget {
                    id: c
                    label: "C"
                }
            }
            Rectangle {
                x: 140
                y: 100
                width: 80
                height: 40
                KeyboardTarget {
                    id: d
                    label: "D"
                }
            }
            Rectangle {
                x: 110
                y: 20
                width: 20
                height: 40
                visible: false
                KeyboardTarget {
                    id: hidden
                }
            }
            Rectangle {
                x: 110
                y: 100
                width: 20
                height: 40
                enabled: false
                KeyboardTarget {
                    id: disabled
                }
            }
            Item {
                x: 230
                y: 20
                width: 20
                height: 40
                clip: true
                Item {
                    x: 30
                    width: 30
                    height: 40
                    KeyboardTarget {
                        id: clipped
                    }
                }
            }
        }
        Item {
            x: 300
            y: 20
            width: 150
            height: 110
            clip: true
            Flickable {
                id: scroll
                anchors.fill: parent
                contentHeight: 250
                clip: true
                property bool keyboardBoundary: true
                property bool keyboardMode: false
                Rectangle {
                    y: 0
                    width: 100
                    height: 40
                    KeyboardTarget {
                        id: first
                    }
                }
                Rectangle {
                    y: 160
                    width: 100
                    height: 40
                    KeyboardTarget {
                        id: last
                    }
                }
            }
        }
        TestCase {
            name: "SpatialKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SPATIAL_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function test_neighbours() {
                window.requestActivate();
                a.forceActiveFocus();
                keyClick(Qt.Key_Down);
                verify(c.activeFocus, "Down follows the same column, not flattened document order");
                keyClick(Qt.Key_Right);
                verify(d.activeFocus);
                keyClick(Qt.Key_Up);
                verify(b.activeFocus);
                keyClick(Qt.Key_Left);
                verify(a.activeFocus);
                keyClick(Qt.Key_Tab);
                verify(b.activeFocus, "Tab keeps reading order");
                keyClick(Qt.Key_Backtab);
                verify(a.activeFocus);
                verify(!Keyboard.targets(grid).includes(hidden));
                verify(!Keyboard.targets(grid).includes(disabled));
                verify(!Keyboard.targets(grid).includes(clipped), "unscrollable clipped targets are excluded");
                first.forceActiveFocus();
                keyClick(Qt.Key_Down);
                verify(last.activeFocus);
                const point = last.mapToItem(scroll, 0, 0);
                verify(point.y >= 0 && point.y + last.height <= scroll.height, "scroll navigation reveals the entire focused target");
                keyClick(Qt.Key_Up);
                verify(first.activeFocus);
                compare(scroll.contentY, 0);
            }
        }
    }
}
