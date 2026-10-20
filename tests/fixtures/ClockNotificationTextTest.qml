// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NotificationStore {
        id: store
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 560
        implicitHeight: 900
        ClockBody {
            id: body
            width: 560
            height: 900
            opened: true
            store: store
        }
    }
    TestCase {
        name: "ClockNotificationText"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("CLOCK_NOTIFICATION_TEXT_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function texts(item) {
            let result = item.visible && typeof item.text === "string" ? [item.text] : [];
            for (const child of item.children ?? [])
                result = result.concat(texts(child));
            return result;
        }
        function test_notice_data() {
            return [
                {
                    tag: "disabled",
                    state: "disabled",
                    expected: "Notifications are off"
                },
                {
                    tag: "occupied",
                    state: "owned_elsewhere",
                    expected: "Notifications are shown by another program"
                },
                {
                    tag: "starting",
                    state: "registering",
                    expected: "Notifications are starting…"
                },
                {
                    tag: "unexpected",
                    state: "fixture_transport_fault",
                    expected: "Notifications are unavailable"
                },
                {
                    tag: "active",
                    state: "active",
                    expected: ""
                }
            ];
        }
        function test_notice(data) {
            body.serverState = data.state;
            wait(30);
            const shown = texts(body);
            const notice = findChild(body, "notification-server-notice");
            verify(notice !== null);
            compare(notice.y, body.notesTop + 36);
            const notices = notice.visible ? [notice.text] : [];
            if (JSON.stringify(notices) !== JSON.stringify(data.expected ? [data.expected] : []))
                console.log("MISMATCH", data.state, JSON.stringify(notices), data.expected);
            compare(JSON.stringify(notices), JSON.stringify(data.expected ? [data.expected] : []));
            verify(!shown.some(text => text.includes("fixture_transport_fault")));
        }
    }
}
