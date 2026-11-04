// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    QtObject {
        id: catalog
        property bool writing: false
        property string lastStatus: ""
        property bool sessionApplied: true
        property var history: []
        property var values: ({})
        property var styles: JSON.parse(Quickshell.env("SETTINGS_TEST_STYLES"))
        property string requestedStyle: ""
        property int calls: 0
        function value(key) {
            return values[key] ?? null;
        }
        function row(key) {
            return {
                value: value(key),
                default: null,
                editable: true
            };
        }
        function set(key, value) {
            if (key !== "appearance.style")
                throw new Error("Unexpected setting: " + key);
            requestedStyle = value;
            calls++;
            writing = true;
            reply.start();
        }
        function applyReply() {
            const style = styles.find(entry => entry.id === requestedStyle);
            values = {
                "appearance.style": style.id,
                "appearance.accent": style.accent,
                "appearance.wallpaper": style.wallpaper,
                "appearance.overview_video": style.overview_video,
                "appearance.overview_still": style.overview_still
            };
            // The core updates selected metadata in the newly decoded apply array.
            styles = styles.map(entry => Object.assign({}, entry, {
                    selected: entry.id === style.id
                }));
            lastStatus = "committed";
            writing = false;
            refresh.start();
        }
    }
    Timer {
        id: reply
        interval: 50
        onTriggered: catalog.applyReply()
    }
    Timer {
        id: refresh
        interval: 50
        onTriggered: catalog.styles = JSON.parse(JSON.stringify(catalog.styles))
    }
    SystemService {
        id: service
        live: false
        helpersEnabled: false
    }
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 1100
        height: 700
        SettingsView {
            id: view
            anchors.fill: parent
            catalog: catalog
            service: service
            niri: niri
            availablePages: ["themes"]
            page: "themes"
        }
        TestCase {
            name: "ThemeScroll"
            when: window.visible
            function equal(actual, expected, message) {
                if (actual !== expected)
                    console.error(message + ": " + actual + " != " + expected);
                compare(actual, expected, message);
            }
            function test_activation_data() {
                const cases = [];
                for (const id of ["emaki", "07-blue-light"])
                    for (const input of ["pointer", "Return", "Space"])
                        cases.push({
                            tag: id + "-" + input,
                            id: id,
                            input: input
                        });
                return cases;
            }
            function visibleInViewport(card) {
                const point = card.mapToItem(view.scroller, 0, 0);
                return point.y >= 0 && point.y + card.height <= view.scroller.height && point.x >= 0 && point.x + card.width <= view.scroller.width;
            }
            function test_activation(data) {
                window.requestActivate();
                catalog.values = ({});
                catalog.calls = 0;
                view.search.forceActiveFocus();
                catalog.styles = catalog.styles.map(style => Object.assign({}, style, {
                        selected: false
                    }));
                verify(waitForPolish(window));
                const card = findChild(view, "theme-choice-" + data.id);
                verify(card);
                const top = card.mapToItem(view.scroller.contentItem, 0, 0).y;
                view.scroller.contentY = data.id === "emaki" ? 0 : top - 80;
                verify(waitForPolish(window));
                verify(visibleInViewport(card));
                if (data.input !== "pointer") {
                    card.forceActiveFocus(Qt.TabFocusReason);
                    tryCompare(card, "activeFocus", true);
                }
                const before = view.scroller.contentY;
                if (data.input === "pointer")
                    mouseClick(card);
                else
                    keyClick(data.input === "Return" ? Qt.Key_Return : Qt.Key_Space);
                compare(catalog.calls, 1);
                compare(catalog.requestedStyle, data.id);
                compare(catalog.writing, true);
                equal(view.scroller.contentY, before, "Saving must preserve scroll position");
                verify(visibleInViewport(card), "Saving must keep the card visible");
                // No scroll repairs after input: exercise busy, apply, and list refresh.
                wait(200);
                verify(waitForPolish(window));
                compare(catalog.writing, false);
                equal(view.scroller.contentY, before, "Activation must preserve scroll position");
                const current = findChild(view, "theme-choice-" + data.id);
                verify(visibleInViewport(current), "Activated card must remain fully visible");
                verify(current.selected);
                compare(findChild(view, "theme-status-" + data.id).text, "Current theme");
                equal(current, card, "Catalog refresh must preserve the card");
                verify(current.activeFocus, "Activation must preserve card focus");
            }
            function cleanup() {
                // Let replies settle even when an assertion fails before their timers fire.
                tryCompare(catalog, "writing", false);
                tryCompare(refresh, "running", false);
            }
            function test_metadata_refresh() {
                catalog.values = ({});
                const card = findChild(view, "theme-choice-07-blue-light");
                const original = catalog.styles;
                catalog.styles = original.map(style => Object.assign({}, style, {
                        available: style.id === "07-blue-light" ? false : style.available
                    }));
                verify(!card.enabled);
                compare(findChild(view, "theme-status-07-blue-light").text, "Not installed");
                catalog.styles = original;
                verify(card.enabled);
                equal(findChild(view, "theme-choice-07-blue-light"), card, "Catalog refresh must preserve the card");
            }
            onCompletedChanged: if (completed)
                console.log("THEME_SCROLL_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        }
    }
}
