// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "CaptureText.js" as CaptureText

ShellRoot {
    id: fixture
    readonly property int viewportWidth: Number(Quickshell.env("EMAKI_CAPTURE_TEST_WIDTH")) || 800
    readonly property int viewportHeight: Number(Quickshell.env("EMAKI_CAPTURE_TEST_HEIGHT")) || 600
    readonly property string destination: Quickshell.env("EMAKI_CAPTURE_TEST_OUTPUT")
    property int stage: 0
    property bool failed: false
    function check(value, message) {
        if (!value) {
            failed = true;
            console.error("CAPTURE_UI_FAILURE " + message);
        }
    }
    function visibleLabels(item) {
        if (!item.visible)
            return;
        if (item.text !== undefined && item.font !== undefined && item.text !== "") {
            const origin = item.mapToItem(panel, 0, 0);
            check(origin.x >= 0 && origin.y >= 0 && origin.x + item.width <= panel.width + 1 && origin.y + item.height <= panel.height + 1, "label outside viewport: " + item.text);
            check(item.contentWidth <= item.width + 1, "label overflows width: " + item.text);
        }
        for (const child of item.children)
            visibleLabels(child);
    }
    function findButton(item, text) {
        if (item.text === text && item.clicked !== undefined)
            return item;
        for (const child of item.children) {
            const found = findButton(child, text);
            if (found)
                return found;
        }
        return null;
    }
    function exerciseRecognition(mode, label) {
        const button = findButton(panel, label);
        check(button !== null, label + " button exists");
        if (!button)
            return;
        check(button.enabled && button.visible && button.activeFocusOnTab, label + " is keyboard accessible");
        button.forceActiveFocus();
        check(button.activeFocus, label + " receives focus");
        button.clicked();
        check(controller.mode === mode && controller.target === "area" && controller.canCapture, label + " activation");
        check(!controller.selectTarget("window") && !controller.selectTarget("screen"), label + " limits target to area");
        const before = controller.area.x;
        panel.moveSelection(Qt.Key_Right, false);
        check(controller.area.x === before + 1, label + " keyboard area movement");
    }
    function exerciseArea() {
        if (["es", "pt_BR"].indexOf(Quickshell.env("EMAKI_LANGUAGE")) >= 0)
            check(CaptureText.text() !== "Text" && CaptureText.qr() !== "QR code", "recognition labels use compiled translations");
        controller.opened = true;
        controller.ready = true;
        controller.selectOutput("Virtual-1");
        controller.setArea(20, 30, 100, 80);
        panel.moveSelection(Qt.Key_Right, false);
        check(controller.area.x === 21 && controller.area.y === 30 && controller.area.width === 100, "arrow moves area");
        panel.moveSelection(Qt.Key_Down, true);
        check(controller.area.height === 81 && controller.area.y === 30, "shift arrow resizes area");
        controller.setArea(-40, -20, 9999, 9999);
        check(controller.area.x === 0 && controller.area.y === 0 && controller.area.width === viewportWidth && controller.area.height === viewportHeight, "area stays within output");
        controller.setArea(80, 80, viewportWidth / 2, viewportHeight / 3);
        controller.selectMode("recording");
        check(controller.mode === "recording" && controller.canCapture, "recording selectable");
        controller.selectMode("picture");
    }
    CaptureController {
        id: controller
        locked: false
        screens: []
        helperCommand: ["false"]
        outputs: [
            {
                name: "Virtual-1",
                width: fixture.viewportWidth,
                height: fixture.viewportHeight
            },
            {
                name: "Virtual-2",
                width: fixture.viewportWidth,
                height: fixture.viewportHeight
            }
        ]
        outputName: "Virtual-1"
        windows: [
            {
                id: 1,
                title: "Documents",
                app_id: "files"
            },
            {
                id: 2,
                title: "<b>Literal window title</b>",
                app_id: "terminal"
            }
        ]
        windowId: 1
    }
    Window {
        visible: true
        width: fixture.viewportWidth
        height: fixture.viewportHeight
        Rectangle {
            anchors.fill: parent
            color: ShellPalette.background
        }
        CapturePanel {
            id: panel
            anchors.fill: parent
            controller: controller
        }
    }
    Timer {
        id: step
        interval: 180
        running: true
        onTriggered: {
            if (fixture.stage === 0) {
                fixture.exerciseArea();
                fixture.stage++;
                restart();
                return;
            }
            fixture.visibleLabels(panel);
            const name = ["", "area", "window", "screen", "text", "qr"][fixture.stage];
            panel.grabToImage(result => {
                fixture.check(result.saveToFile(fixture.destination + "/" + fixture.viewportWidth + "x" + fixture.viewportHeight + "-" + name + ".png"), "save " + name);
                if (fixture.stage === 1) {
                    controller.selectTarget("window");
                    controller.selectWindow(2);
                    fixture.check(controller.windowId === 2 && controller.target === "window" && controller.canCapture, "window selection");
                } else if (fixture.stage === 2) {
                    controller.selectTarget("screen");
                    controller.selectOutput("Virtual-2");
                    controller.selectMode("recording");
                    fixture.check(controller.outputName === "Virtual-2" && controller.target === "screen" && controller.canCapture, "screen selection");
                } else if (fixture.stage === 3) {
                    fixture.exerciseRecognition("text", CaptureText.text());
                } else if (fixture.stage === 4) {
                    fixture.exerciseRecognition("qr", CaptureText.qr());
                } else {
                    controller.close();
                    fixture.check(!controller.opened && !controller.canCapture, "close cancels selection");
                    console.log(fixture.failed ? "CAPTURE_UI_FAILED" : "CAPTURE_UI_PASSED");
                    Qt.quit();
                    return;
                }
                fixture.stage++;
                step.restart();
            });
        }
    }
}
