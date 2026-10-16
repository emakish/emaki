// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    QtObject {
        id: compositor
        property bool connected: true
        property var model: ({
                outputs: {
                    PANEL: {
                        scale: 1
                    }
                }
            })
    }
    DisplaysService {
        id: service
        Component.onCompleted: {
            if ("active" in service)
                service.active = false;
            if ("niri" in service)
                service.niri = compositor;
        }
        helper: Quickshell.env("DISPLAYS_HELPER")
    }
    SignalSpy {
        id: replies
        target: service
        signalName: "outputsChanged"
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 20
        implicitHeight: 20
    }
    TestCase {
        name: "DisplaysIdle"
        when: window.backingWindowVisible
        function test_visibility_and_live_changes() {
            tryCompare(service, "ready", true);
            replies.clear();
            wait(2250);
            console.log("Closed-page refreshes over 2250 ms:", replies.count);
            compare(replies.count, 0, "closed Displays must not poll");
            compositor.model = {
                outputs: {
                    PANEL: {
                        scale: 1.25
                    }
                }
            };
            wait(100);
            compare(replies.count, 0, "closed page ignores normal output changes");
            service.active = true;
            tryCompare(replies, "count", 1);
            replies.clear();
            compositor.model = {
                outputs: {
                    PANEL: {
                        scale: 1.5
                    }
                }
            };
            tryCompare(replies, "count", 1);
            replies.clear();
            wait(2250);
            compare(replies.count, 0, "shared compositor updates need no display poll");
            service.active = false;
            wait(2250);
            compare(replies.count, 0, "closing stops periodic work");
        }
        onCompletedChanged: if (completed) {
            console.log("DISPLAY_IDLE_RESULT", qtest_results.passCount, qtest_results.failCount);
            Qt.quit();
        }
    }
}
