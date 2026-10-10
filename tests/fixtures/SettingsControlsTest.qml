// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtTest
import Quickshell
import "../../shell/settings" as Settings

ShellRoot {
    QtObject {
        id: fakeCatalog
        property bool writing: false
        property var rows: [
            {
                key: "example",
                value: false,
                default: true
            }
        ]
        property var writes: []
        function row(key) {
            return rows.find(row => row.key === key) || null;
        }
        function set(key, value) {
            writes = writes.concat([[key, value]]);
        }
        function reset(key) {
            writes = writes.concat([[key]]);
        }
    }
    Window {
        id: window
        visible: true
        width: 800
        height: 750
        color: "#fff6f0"
        Column {
            anchors.fill: parent
            Settings.SettingsPageHeader {
                width: parent.width
                title: "Controls"
                explanation: "Settings controls"
            }
            Settings.SettingsCard {
                id: card
                property int headerClicks: 0
                property int footerClicks: 0
                headerAction: Component {
                    Settings.SettingsButton {
                        text: "Header action"
                        onClicked: card.headerClicks++
                    }
                }
                footer: Component {
                    Settings.SettingsButton {
                        text: "Footer action"
                        onClicked: card.footerClicks++
                    }
                }
                width: parent.width
                title: "Settings"
                Settings.SettingsToggleRow {
                    id: boundToggle
                    visible: false
                    catalog: fakeCatalog
                    settingKey: "example"
                }
                Settings.SettingsToggleRow {
                    id: toggle
                    width: parent.width
                    title: "Toggle"
                    explanation: "One explanation."
                    value: false
                    defaultValue: false
                    onValueRequested: next => value = next
                    onResetRequested: value = defaultValue
                }
                Settings.SettingsSliderRow {
                    id: slider
                    property int requests: 0
                    property bool delayResponse: false
                    property real requestedValue: 0
                    width: parent.width
                    title: "Slider"
                    value: 50
                    defaultValue: 50
                    onValueRequested: next => {
                        requests++;
                        requestedValue = next;
                        if (delayResponse)
                            busy = true;
                        else
                            value = next;
                    }
                }
                Settings.SettingsChoiceRow {
                    id: choice
                    width: parent.width
                    title: "Choice"
                    options: [
                        {
                            label: "One",
                            value: 1
                        },
                        {
                            label: "Two",
                            value: 2
                        }
                    ]
                    value: 1
                    defaultValue: 1
                    onValueRequested: next => value = next
                }
                Settings.SettingsSegmentedRow {
                    id: segmented
                    width: parent.width
                    title: "Segmented"
                    options: choice.options
                    value: 1
                    defaultValue: 1
                    onValueRequested: next => value = next
                }
                Settings.SettingsListRow {
                    id: list
                    width: parent.width
                    title: "List"
                    entries: choice.options
                    property var lastEntry: null
                    property var lastAction: null
                    onActionRequested: (entry, action) => lastAction = [entry, action]
                    onActivated: entry => lastEntry = entry
                }
            }
        }
        Component {
            id: leadingSlot
            Item {
                implicitWidth: 24
                implicitHeight: 24
                property var observedEntry: parent.entry
            }
        }
        Component {
            id: trailingSlot
            Settings.SettingsButton {
                text: parent.entry.label
                onClicked: parent.requestAction("inspect")
            }
        }
        TestCase {
            name: "SettingsControls"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SETTINGS_CONTROLS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function equal(actual, expected) {
                if (actual !== expected)
                    console.error("Compare " + actual + " expected " + expected + " " + new Error().stack);
                compare(actual, expected);
            }
            function init() {
                window.requestActivate();
                toggle.managed = false;
                toggle.busy = false;
                toggle.unavailable = false;
                toggle.value = false;
                wait(30);
            }
            function test_toggle_reset_keyboard() {
                equal(toggle.formatValue(false), "Off");
                equal(toggle.formatValue(true), "On");
                equal(choice.formatValue(2), "Two");
                equal(segmented.formatValue(1), "One");
                slider.unit = "px";
                equal(slider.formatValue(50), "50 px");
                verify(!toggle.resetItem.visible);
                toggle.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Space);
                equal(toggle.value, true);
                verify(toggle.resetItem.visible);
                toggle.resetItem.forceActiveFocus();
                keyClick(Qt.Key_Return);
                equal(toggle.value, false);
                toggle.value = true;
                toggle.resetItem.forceActiveFocus();
                keyClick(Qt.Key_Enter);
                equal(toggle.value, false);
                verify(!toggle.resetItem.visible);
                toggle.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Tab);
                verify(slider.controlItem.activeFocus);
            }
            function test_readonly_and_busy() {
                equal(boundToggle.value, false);
                equal(boundToggle.defaultValue, true);
                equal(boundToggle.objectName, "setting-example");
                boundToggle.requestValue(true);
                equal(JSON.stringify(fakeCatalog.writes), '[["example","true"]]');
                fakeCatalog.writing = true;
                boundToggle.requestReset();
                equal(fakeCatalog.writes.length, 1);
                fakeCatalog.writing = false;
                boundToggle.requestReset();
                equal(fakeCatalog.writes.length, 2);
                boundToggle.settingKey = "missing";
                verify(boundToggle.unavailable);
                boundToggle.settingKey = "example";
                const originalRows = fakeCatalog.rows;
                fakeCatalog.rows = [
                    {
                        key: "example",
                        value: null,
                        default: true,
                        editable: false
                    }
                ];
                verify(boundToggle.unavailable);
                verify(!boundToggle.changed);
                boundToggle.requestValue(true);
                boundToggle.requestReset();
                equal(fakeCatalog.writes.length, 2);
                fakeCatalog.rows = originalRows;
                toggle.value = true;
                toggle.managed = true;
                verify(!toggle.editable);
                verify(!toggle.resetItem.visible);
                mouseClick(toggle.controlItem);
                toggle.requestValue(false);
                toggle.requestReset();
                equal(toggle.value, true);
                toggle.managed = false;
                toggle.busy = true;
                verify(!toggle.controlItem.enabled);
                verify(!toggle.resetItem.enabled);
                toggle.requestReset();
                equal(toggle.value, true);
                toggle.busy = false;
                toggle.unavailable = true;
                verify(!toggle.resetItem.visible);
                verify(!toggle.controlItem.enabled);
            }
            function test_slider_drag() {
                slider.value = 50;
                slider.requests = 0;
                const control = slider.controlItem;
                mouseWheel(control, control.width / 2, control.height / 2, 0, -120);
                equal(slider.value, 50);
                equal(slider.requests, 0);
                mousePress(control, control.width / 2, control.height / 2);
                mouseMove(control, control.width * .65, control.height / 2);
                mouseMove(control, control.width * .8, control.height / 2);
                equal(slider.requests, 0);
                equal(slider.value, 50);
                mouseRelease(control, control.width * .8, control.height / 2);
                equal(slider.requests, 1);
                verify(slider.value > 50);
                slider.value = 24;
                equal(control.value, 24);
            }
            function test_slider_pending_write() {
                slider.value = 50;
                slider.requests = 0;
                slider.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Right);
                slider.busy = true;
                wait(450);
                equal(slider.requests, 0);
                equal(slider.controlItem.value, 52);
                slider.busy = false;
                equal(slider.requests, 1);
                equal(slider.value, 52);

                slider.delayResponse = true;
                slider.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Right);
                tryCompare(slider, "requests", 2);
                equal(slider.value, 52);
                equal(slider.controlItem.value, 53);
                slider.value = slider.requestedValue;
                slider.busy = false;
                equal(slider.controlItem.value, 53);
                slider.delayResponse = false;
            }
            function test_other_controls() {
                slider.value = 50;
                slider.requests = 0;
                slider.controlItem.forceActiveFocus();
                for (let i = 0; i < 10; i++)
                    keyClick(Qt.Key_Right);
                equal(slider.value, 50);
                equal(slider.requests, 0);
                tryCompare(slider, "requests", 1);
                equal(slider.value, 60);
                choice.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Down);
                equal(choice.value, 2);
                const originalOptions = choice.options;
                choice.options = Array.from({
                    length: 60
                }, (_, index) => ({
                            label: "Option " + index,
                            value: index
                        }));
                choice.value = 55;
                choice.controlItem.popup.open();
                wait(40);
                verify(choice.controlItem.popup.height <= window.height - 16);
                verify(choice.controlItem.popup.contentItem.contentY > 0);
                choice.controlItem.popup.close();
                choice.options = originalOptions;
                const longLabel = "American English — United States (en_US.UTF-8)";
                choice.options = [
                    {
                        label: longLabel,
                        value: 1
                    }
                ];
                choice.value = 1;
                choice.controlItem.forceActiveFocus();
                wait(30);
                equal(choice.controlItem.contentItem.truncated, true);
                equal(choice.controlItem.ToolTip.text, longLabel);
                tryCompare(choice.controlItem.ToolTip, "visible", true);
                toggle.controlItem.forceActiveFocus();
                mouseMove(choice.controlItem);
                tryCompare(choice.controlItem, "hovered", true);
                tryCompare(choice.controlItem.ToolTip, "visible", true);
                choice.controlItem.popup.open();
                wait(30);
                const longOption = choice.controlItem.popup.contentItem.itemAtIndex(0);
                verify(longOption !== null);
                equal(longOption.contentItem.text, longLabel);
                equal(longOption.contentItem.truncated, false);
                equal(longOption.contentItem.height >= longOption.contentItem.contentHeight, true);
                choice.controlItem.popup.close();
                choice.options = originalOptions;
                wait(200);
                const segments = segmented.controlItem.children.find(child => child.itemAt !== undefined);
                const second = segments.itemAt(1);
                second.forceActiveFocus();
                keyClick(Qt.Key_Space);
                equal(segmented.value, 2);
                const firstEntry = list.controlItem.children.find(child => child.itemAt !== undefined).itemAt(0);
                mouseClick(firstEntry);
                equal(list.lastEntry, 1);
                list.managed = true;
                mouseClick(firstEntry);
                equal(list.lastEntry, 1);
                list.managed = false;
                list.controlWidth = 400;
                list.entries = [
                    {
                        label: "Device",
                        value: "device",
                        subtitle: "Connected",
                        error: "Try again",
                        actions: [
                            {
                                label: "Disconnect",
                                id: "disconnect"
                            }
                        ]
                    }
                ];
                wait(30);
                const richEntry = list.controlItem.children.find(child => child.itemAt !== undefined).itemAt(0);
                const action = findChild(richEntry, "list-action-disconnect");
                verify(action !== null);
                mouseClick(action);
                equal(JSON.stringify(list.lastAction), '["device","disconnect"]');
                list.lastAction = null;
                list.entries = [
                    {
                        label: "Device",
                        value: "device",
                        busy: true,
                        actions: [
                            {
                                label: "Disconnect",
                                id: "disconnect"
                            }
                        ]
                    }
                ];
                wait(30);
                list.requestAction(list.entries[0], "disconnect");
                equal(list.lastAction, null);
                list.entries = [
                    {
                        label: "Device",
                        value: "device",
                        subtitle: "Connected"
                    }
                ];
                list.itemLeading = leadingSlot;
                list.itemTrailing = trailingSlot;
                wait(30);
                const customEntry = list.controlItem.children.find(child => child.itemAt !== undefined).itemAt(0);
                const loaders = customEntry.children.filter(child => child.item !== undefined);
                equal(loaders[0].item.observedEntry.value, "device");
                mouseClick(loaders[1].item);
                equal(JSON.stringify(list.lastAction), '["device","inspect"]');
                verify(card.headerActionItem.y + card.headerActionItem.height <= card.headerHeight);
                verify(card.footerItem.parent.y > list.y + list.height);
                mouseClick(card.headerActionItem);
                mouseClick(card.footerItem);
                equal(card.headerClicks, 1);
                equal(card.footerClicks, 1);
            }
        }
    }
}
