// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import ".." as UI
import "../Protocol.js" as Protocol

ShellRoot {
    id: test
    property var fixture: ({}) // HARDWARE_FIXTURE
    property int cursor: -1
    property var current: null
    function check(condition: bool, message: string): void {
        if (!condition)
            throw new Error("ASSERTION_FAILED " + message);
    }
    function find(item: var, name: string): var {
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const result = find(child, name);
            if (result)
                return result;
        }
        return null;
    }
    function advance(): void {
        ++cursor;
        if (cursor === fixture.cases.length) {
            console.log("HARDWARE_OK");
            Qt.quit();
            return;
        }
        current = fixture.cases[cursor];
        controller.step = "welcome";
        controller.session = Protocol.initial();
        controller.receive({
            type: "hello",
            proto: 1,
            emaki_version: "fixture"
        });
        const inventory = JSON.parse(JSON.stringify(fixture.inventory));
        if (Object.prototype.hasOwnProperty.call(current, "hardware"))
            inventory.hardware = current.hardware;
        if (current.empty)
            inventory.disks = [];
        const request = Protocol.request(controller.session, "probe");
        inventory.for_id = request.id;
        controller.receive(inventory);
        controller.diskId = current.empty ? "" : inventory.disks[0].id;
        // These cases isolate the hardware gate after an explicit installation choice.
        controller.mode = "erase";
        controller.step = "disk";
        verify.restart();
    }
    function inspect(): void {
        try {
            const notice = find(view, "hardwareNotice");
            const next = find(view, "continueButton");
            check(!!notice && !!next, current.name + ": disk controls exist");
            check(notice.visible === !!current.notice, current.name + ": notice visibility");
            if (current.notice) {
                check(notice.text === current.notice, current.name + ": complete plain message");
                check(notice.color.toString() === (current.neutral ? view.ink : view.danger).toString(), current.name + ": notice color");
            }
            check(next.enabled === current.continue, current.name + ": Continue gate");
            check(view.secureBootRefused === !!current.refused, current.name + ": Secure Boot gate");
            if (current.progress) {
                for (const message of current.progress)
                    controller.receive(message);
                const banner = find(view, "installerNotice");
                check(controller.session.notice === current.progress[0].notice, "hardware notice retained after state");
                check(banner.visible && banner.text === controller.session.notice, "hardware notice visible during progress");
            }
            if (current.error) {
                const presentation = Protocol.errorPresentation(current.error);
                check(presentation.sentence === current.error.message && presentation.action === "", "direct Secure Boot refusal");
            }
            console.log("PASS hardware " + current.name);
            Qt.callLater(advance);
        } catch (error) {
            console.error(error.toString());
            Qt.quit();
        }
    }
    UI.InstallerController {
        id: controller
        mockTransport: true
        helpersEnabled: false
    }
    FloatingWindow {
        visible: true
        implicitWidth: 960
        implicitHeight: 640
        UI.InstallerView {
            id: view
            anchors.fill: parent
            controller: controller
        }
    }
    Timer {
        id: verify
        interval: 80
        onTriggered: test.inspect()
    }
    Component.onCompleted: Qt.callLater(advance)
}
