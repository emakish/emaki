// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "Keyboard.js" as Keyboard
import "settings" as Settings

ShellRoot {
    NiriService {
        id: niri
        binary: ""
    }
    SystemService {
        id: service
        live: false
        helpersEnabled: false
    }
    QtObject {
        id: catalog
        property bool writing: false
        property bool busy: false
        property string state: "ready"
        property string lastStatus: ""
        property bool sessionApplied: true
        property var history: []
        function row(key) {
            return {
                key: key,
                value: false,
                default: false
            };
        }
    }
    CaptureController {
        id: captureController
        enabled: false
    }
    QtObject {
        id: warningBackend
        property bool installed: true
        property bool connected: true
        property bool btrfs: false
        function acknowledgeWarning() {
        }
    }
    QtObject {
        id: reportController
        property string part: "other"
        property string what: "First line\nSecond line"
        property int whatLimit: 1000
        property string state: "ready"
        property string message: ""
        property var items: []
        property var chosen: ({})
        property var report: ({})
        property bool previewOpen: false
        property bool previewStale: false
        property string preview: "Preview"
        property bool sending: false
        property bool canSend: true
        function setWhat(value) {
            what = value;
        }
        function togglePreview() {
            previewOpen = !previewOpen;
        }
        function send() {
        }
    }
    Window {
        id: window
        width: 1440
        height: 1000
        visible: true
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
        SettingsView {
            id: view
            anchors.fill: parent
            visible: false
            catalog: catalog
            service: service
            niri: niri
            availablePages: ["panel", "windows"]
            embedded: true
        }
        Item {
            id: controlScope
            visible: false
            width: 600
            height: 400
            readonly property bool keyboardBoundary: true
            function keyboardNavigate(event) {
                Keyboard.handle(controlScope, event);
            }
            Keys.onPressed: event => keyboardNavigate(event)
            Column {
                width: parent.width
                Settings.SettingsToggleRow {
                    id: toggle
                    width: parent.width
                    title: "Toggle"
                    value: false
                    defaultValue: false
                    onValueRequested: next => value = next
                }
                Settings.SettingsSliderRow {
                    id: slider
                    width: parent.width
                    title: "Slider"
                    value: 50
                    defaultValue: 50
                    onValueRequested: next => value = next
                }
                Settings.SettingsChoiceRow {
                    id: choice
                    width: parent.width
                    title: "Choice"
                    value: 1
                    defaultValue: 1
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
                    onValueRequested: next => value = next
                }
            }
        }
        CapturePanel {
            id: capturePanel
            anchors.fill: parent
            visible: false
            controller: captureController
        }
        Welcome {
            id: welcome
            anchors.fill: parent
            visible: false
        }
        SudoKeyWarning {
            id: warning
            anchors.fill: parent
            visible: false
            backend: warningBackend
        }
        ReportView {
            id: report
            anchors.fill: parent
            visible: false
            controller: reportController
        }
        TestCase {
            name: "SurfaceSpatial"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SURFACE_SPATIAL_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function check(value, message) {
                if (!value)
                    console.error("FAILED " + message + new Error().stack);
                verify(value, message);
            }
            function equal(actual, expected) {
                if (actual !== expected)
                    console.error("FAILED " + actual + " expected " + expected);
                compare(actual, expected);
            }
            function init() {
                window.requestActivate();
                scene.closeAll();
                scene.visible = false;
                view.visible = false;
                controlScope.visible = false;
                capturePanel.visible = false;
                welcome.visible = false;
                warning.visible = false;
                report.visible = false;
                wait(40);
            }
            function test_welcome_and_warning() {
                welcome.visible = true;
                welcome.page = 1;
                const next = findChild(welcome, "welcome-next");
                const back = findChild(welcome, "welcome-back");
                const close = findChild(welcome, "welcome-close");
                // The tour's arrows turn its pages; Up and Down walk the buttons by place.
                next.forceActiveFocus();
                keyClick(Qt.Key_Left);
                equal(welcome.page, 0);
                keyClick(Qt.Key_Right);
                equal(welcome.page, 1);
                back.forceActiveFocus();
                keyClick(Qt.Key_Up);
                check(close.activeFocus, "Welcome Up from Back reaches the close button");
                keyClick(Qt.Key_Down);
                check(next.activeFocus, "Welcome Down from close returns to Next");
                back.forceActiveFocus();
                keyClick(Qt.Key_Return);
                equal(welcome.page, 0);
                welcome.visible = false;
                warning.visible = true;
                const dismiss = findChild(warning, "sudo-key-warning-dismiss");
                const accept = findChild(warning, "sudo-key-warning-accept");
                dismiss.forceActiveFocus();
                keyClick(Qt.Key_Right);
                check(accept.activeFocus, "Warning Right reaches acknowledgement");
                keyClick(Qt.Key_Left);
                check(dismiss.activeFocus, "Warning Left returns to dismiss");
            }
            function test_report_editor() {
                report.visible = true;
                wait(30);
                const field = findChild(report, "report-what");
                field.forceActiveFocus();
                field.cursorPosition = 0;
                keyClick(Qt.Key_Down);
                check(field.activeFocus, "Down inside the report preserves text editing");
                field.cursorPosition = field.length;
                keyClick(Qt.Key_Down);
                check(!field.activeFocus, "Down at the end leaves the report editor");
                check(Keyboard.boundary(window.activeFocusItem) === report);
            }
            function test_system_back() {
                scene.visible = true;
                scene.systemBody.confirmation = "reboot";
                check(scene.systemBody.stepBack());
                equal(scene.systemBody.confirmation, "");
                scene.systemBody.hiddenOpen = true;
                scene.systemBody.selectedNetwork = "fixture";
                check(scene.systemBody.stepBack());
                equal(scene.systemBody.hiddenOpen, false);
                equal(scene.systemBody.selectedNetwork, "fixture");
                check(scene.systemBody.stepBack());
                equal(scene.systemBody.selectedNetwork, "");
            }
            function test_capture_toolbar() {
                capturePanel.visible = true;
                captureController.outputName = "Fixture";
                captureController.outputs = [
                    {
                        name: "Fixture",
                        width: 1440,
                        height: 1000
                    }
                ];
                captureController.ready = true;
                captureController.area = Qt.rect(10, 10, 100, 100);
                capturePanel.forceActiveFocus();
                keyClick(Qt.Key_Right);
                equal(captureController.area.x, 11);
                keyClick(Qt.Key_Tab);
                check(window.activeFocusItem !== capturePanel, "Tab enters capture toolbar");
                const before = window.activeFocusItem;
                keyClick(Qt.Key_Right);
                check(window.activeFocusItem !== before, "Right traverses capture toolbar");
                equal(captureController.area.x, 11);
                keyClick(Qt.Key_Escape);
                check(capturePanel.activeFocus, "Escape returns from the toolbar to the area");
                check(captureController.ready, "Returning to the area does not cancel capture");
                keyClick(Qt.Key_Down);
                equal(captureController.area.y, 11);
                keyClick(Qt.Key_Right, Qt.ShiftModifier);
                equal(captureController.area.width, 101);
                keyClick(Qt.Key_Escape);
                check(!captureController.ready, "Escape from the area closes capture");
            }
            function test_controls() {
                controlScope.visible = true;
                wait(30);
                toggle.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Return);
                equal(toggle.value, true);
                slider.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Down);
                check(choice.controlItem.activeFocus, "Down leaves a slider for the next row");
                equal(slider.controlItem.value, 50);
                keyClick(Qt.Key_Up);
                check(slider.controlItem.activeFocus, "Up leaves a closed choice for the previous row");
                equal(choice.value, 1);
                keyClick(Qt.Key_Right);
                equal(slider.controlItem.value, 51);
                choice.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Return);
                wait(100);
                check(choice.controlItem.popup.visible, "choice popup opens");
                keyClick(Qt.Key_Down);
                keyClick(Qt.Key_Return);
                equal(choice.value, 2);
                keyClick(Qt.Key_Return);
                check(choice.controlItem.popup.visible, "choice popup reopens");
                keyClick(Qt.Key_Escape);
                wait(100);
                check(!choice.controlItem.popup.visible, "choice popup closes");
            }
            function test_settings_columns() {
                view.visible = true;
                wait(150);
                view.search.forceActiveFocus();
                keyClick(Qt.Key_Down);
                const section = window.activeFocusItem;
                equal(section.objectName, "section-panel");
                keyClick(Qt.Key_Up);
                check(view.search.activeFocus, "Up from first section returns to search");
                section.forceActiveFocus();
                keyClick(Qt.Key_Right);
                check(window.activeFocusItem !== section, "Right reaches page controls");
                check(Keyboard.boundary(window.activeFocusItem) === view);
            }
            function test_dock_popup() {
                scene.visible = true;
                wait(200);
                scene.dock.takeFocus();
                tryVerify(() => scene.dock.iconKeys.length > 0, 4000, "dock icons loaded");
                keyClick(Qt.Key_Up);
                check(scene.dock.popupOpen, "Up opens the menu above the icon");
                check(scene.dock.keyboardRow >= 0);
                keyClick(Qt.Key_Down);
                check(!scene.dock.popupOpen, "Down from the bottom menu row returns to its icon");
                check(scene.dock.keyboardActive);
            }
        }
    }
}
