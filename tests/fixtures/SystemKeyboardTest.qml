// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: test
    property bool failed: false
    function check(ok: bool, message: string): void {
        if (!ok) {
            failed = true;
            console.error("KEYBOARD_FAIL " + message);
        }
    }
    function find(item: Item, key: string): Item {
        if (item.key === key && item.activeFocusOnTab)
            return item;
        for (const child of item.children) {
            const found = find(child, key);
            if (found)
                return found;
        }
        return null;
    }
    function focusRing(item: Item): Item {
        return panel.focusRing;
    }
    function checkRing(item: Item, message: string): void {
        const ring = focusRing(item);
        check(ring !== null && ring.visible, message + " visible");
        if (!ring)
            return;
        check(ring.focused === item, message + " follows focused control");
        let captured = false;
        ring.grabToImage(result => {
            result.saveToFile(Quickshell.env("EMAKI_TEST_EVIDENCE") + "/" + item.key + ".png");
            captured = true;
        });
        for (let tries = 0; !captured && tries < 100; ++tries)
            input.wait(10);
        check(captured, "ring captured");
        check(ring.border.color.toString() === ShellPalette.text.toString(), message + " light outer edge");
        check(ring.children[0].border.color.toString() === ShellPalette.background.toString(), message + " dark inner edge");
    }
    function tabTo(key: string): Item {
        const item = find(panel, key);
        check(item !== null, "control exists: " + key);
        if (!item)
            return null;
        let count = 0;
        do {
            input.keyClick(Qt.Key_Tab);
        } while (!item.activeFocus && ++count < 80)
        check(item.activeFocus, "Tab reaches " + key);
        input.wait(30);
        return item;
    }
    SystemFixture {
        id: backend
    }
    SystemService {
        id: service
        backend: backend
        brightness: ({
                state: "ready",
                percent: 50
            })
        profiles: ({
                state: "ready",
                current: "balanced",
                profiles: ["power-saver", "balanced", "performance"]
            })
        vpn: ({
                state: "ready",
                connections: [
                    {
                        uuid: "fixture",
                        name: "Fixture VPN",
                        kind: "wireguard",
                        active: false
                    }
                ]
            })
        property string lastAction: ""
        property var lastValue: null
        function act(kind: string, value: var): bool {
            lastAction = kind;
            lastValue = value;
            if (kind === "brightness") {
                brightness = {
                    state: "ready",
                    percent: value
                };
                return true;
            }
            if (kind === "profile") {
                profiles = {
                    state: "ready",
                    current: value,
                    profiles: profiles.profiles
                };
                return true;
            }
            if (kind === "vpn") {
                vpn = {
                    state: "ready",
                    connections: [
                        {
                            uuid: value.uuid,
                            name: "Fixture VPN",
                            kind: "wireguard",
                            active: value.up
                        }
                    ]
                };
                return true;
            }
            const result = backend.act(kind, value);
            return typeof result === "function" && result() === true;
        }
    }
    NiriService {
        id: niri
        binary: ""
        connection: "connected"
        model: ({
                overview_open: false,
                workspaces: {},
                windows: {},
                keyboard_layouts: {
                    names: ["English (US)", "German"],
                    current_idx: 0
                }
            })
        function switchLayout(index: int): bool {
            model = {
                overview_open: false,
                workspaces: {},
                windows: {},
                keyboard_layouts: {
                    names: model.keyboard_layouts.names,
                    current_idx: index
                }
            };
            return true;
        }
    }
    FloatingWindow {
        id: window
        implicitWidth: 800
        implicitHeight: 480
        SystemPanel {
            id: panel
            viewportWidth: 800
            viewportHeight: 480
            expansion: 1
            opened: true
            page: "sound"
            service: service
            niri: niri
            onPageRequested: page => panel.page = page
            Keys.onEscapePressed: {
                opened = false;
                outside.forceActiveFocus();
            }
        }
        TextInput {
            id: outside
            y: 450
            width: 100
            height: 25
        }
        TestCase {
            id: input
            when: false
            name: "SystemKeyboard"
        }
    }
    Timer {
        interval: 400
        running: true
        onTriggered: {
            niri.connection = "connected";
            niri.model = {
                overview_open: false,
                workspaces: {},
                windows: {},
                keyboard_layouts: {
                    names: ["English (US)", "German"],
                    current_idx: 0
                }
            };
            panel.expansion = 0;
            outside.forceActiveFocus();
            panel.takeFocus();
            input.wait(30);
            test.check(outside.activeFocus, "opening waits for page content");
            panel.expansion = .55;
            input.wait(30);
            test.check(outside.activeFocus, "opening does not focus tray tab before page appears");
            panel.expansion = .8;
            input.wait(30);
            const initial = test.find(panel, "volume");
            test.check(initial.activeFocus, "animated opening starts at first page control");
            panel.expansion = 0;
            outside.forceActiveFocus();
            panel.takeFocus();
            panel.opened = false;
            panel.opened = true;
            panel.expansion = 1;
            input.wait(30);
            test.check(outside.activeFocus, "closing cancels pending page focus");
            panel.page = "empty";
            panel.takeFocus();
            input.wait(30);
            test.check(test.find(panel, "cell-tray").activeFocus, "empty page falls back to header");
            panel.page = "sound";
            panel.takeFocus(false);
            input.wait(30);
            test.check(initial.activeFocus, "opening starts at first page control");
            test.check(!test.focusRing(initial).visible, "pointer opening has no focus ring");
            input.keyClick(Qt.Key_Tab);
            test.check(panel.keyboardMode, "first Tab from pointer-opened slider enables keyboard mode");
            test.check(panel.Window.window.activeFocusItem !== initial, "first Tab advances from pointer-opened slider");
            test.checkRing(panel.Window.window.activeFocusItem, "first Tab from pointer-opened slider ring");
            panel.takeFocus(false);
            input.wait(30);
            input.keyClick(Qt.Key_Backtab);
            test.check(panel.keyboardMode, "first Backtab from pointer-opened slider enables keyboard mode");
            test.checkRing(panel.Window.window.activeFocusItem, "first Backtab from pointer-opened slider ring");
            panel.takeFocus();
            input.wait(30);
            test.checkRing(initial, "system slider focus ring");
            const volume = test.tabTo("volume");
            const before = volume.shown;
            input.keyClick(Qt.Key_Left);
            test.check(volume.shown === before - 1, "volume left step");
            input.keyClick(Qt.Key_PageUp);
            test.check(volume.shown === Math.min(100, before + 9), "volume page step");
            input.keyClick(Qt.Key_Home);
            test.check(volume.shown === 0, "volume minimum");
            input.keyClick(Qt.Key_End);
            test.check(volume.shown === 100, "volume maximum");
            test.tabTo("mic");
            const stream = test.tabTo("stream-0");
            const top = stream.mapToItem(panel.body.scroller, 0, 0).y;
            test.check(top >= -1 && top + stream.height <= panel.body.scroller.height + 1, "focused app slider scrolls into view");
            const lightTab = test.tabTo("cell-light");
            input.keyClick(Qt.Key_Return);
            test.checkRing(lightTab, "selected system tab focus ring");
            const brightness = test.tabTo("brightness");
            input.keyClick(Qt.Key_Home);
            test.check(service.brightness.percent === 5, "brightness respects minimum");
            input.keyClick(Qt.Key_PageUp);
            test.check(service.brightness.percent === 15, "brightness page increment reaches service");
            const night = test.tabTo("night");
            input.keyClick(Qt.Key_Space);
            test.check(service.night.on, "night light keyboard toggle");
            input.wait(30);
            test.checkRing(night, "selected orange switch focus ring");
            input.mouseClick(night, night.width / 2, night.height / 2);
            test.check(!test.focusRing(night).visible, "pointer press hides keyboard ring");
            input.keyClick(Qt.Key_Tab);
            test.check(panel.keyboardMode, "Tab restores keyboard focus indication");
            test.tabTo("warmth");
            input.keyClick(Qt.Key_Right);
            test.check(service.night.warmth === 51, "warmth keyboard adjustment");
            test.tabTo("cell-bt");
            input.keyClick(Qt.Key_Return);
            test.tabTo("bt-on");
            input.keyClick(Qt.Key_Space);
            test.check(!backend.adapter.enabled, "Bluetooth radio off");
            input.keyClick(Qt.Key_Space);
            test.check(backend.adapter.enabled, "Bluetooth radio on");
            test.tabTo("dev-fixture-device");
            input.keyClick(Qt.Key_Return);
            test.check(backend.devices[0].connected, "paired Bluetooth device connects");
            input.wait(40);
            test.check(test.find(panel, "dev-fixture-device").activeFocus, "device focus survives backend refresh");
            input.keyClick(Qt.Key_Return);
            test.check(!backend.devices[0].connected, "focused Bluetooth device disconnects after backend refresh");
            test.tabTo("search");
            input.keyClick(Qt.Key_Space);
            test.check(backend.adapter.discovering, "Bluetooth scan starts");
            input.keyClick(Qt.Key_Space);
            test.check(!backend.adapter.discovering, "Bluetooth scan stops");
            backend.pairOutcome = "hang";
            test.tabTo("near-fixture-nearby");
            input.keyClick(Qt.Key_Return);
            test.check(backend.devices[1].pairing, "nearby Bluetooth device pairs");
            test.tabTo("near-fixture-nearby");
            input.keyClick(Qt.Key_Return);
            test.check(!backend.devices[1].pairing, "Bluetooth pairing cancels");
            test.tabTo("cell-kb");
            input.keyClick(Qt.Key_Return);
            test.tabTo("layout-1");
            input.keyClick(Qt.Key_Return);
            test.check(niri.layouts.current_idx === 1, "keyboard layout choice");
            test.tabTo("cell-tray");
            input.keyClick(Qt.Key_Return);
            test.tabTo("tray-0");
            input.keyClick(Qt.Key_Return);
            input.keyClick(Qt.Key_Tab);
            test.check(test.find(panel, "tray-0:m2").activeFocus, "tray skips disabled item and separator");
            input.keyClick(Qt.Key_Space);
            test.check(service.tray.checked && service.tray.triggered === 1, "tray checkable action");
            test.tabTo("tray-0:m3");
            input.keyClick(Qt.Key_Return);
            test.tabTo("tray-0:s0");
            input.keyClick(Qt.Key_Return);
            test.check(service.tray.triggered === 2, "tray submenu action");
            test.tabTo("cell-wifi");
            input.keyClick(Qt.Key_Return);
            test.check(panel.page === "wifi", "header switches Wi-Fi page");
            test.tabTo("vpn-fixture");
            input.keyClick(Qt.Key_Space);
            test.check(service.vpn.connections[0].active, "VPN toggles on");
            input.wait(40);
            test.check(test.find(panel, "vpn-fixture").activeFocus, "VPN focus survives backend refresh");
            input.keyClick(Qt.Key_Space);
            test.check(!service.vpn.connections[0].active, "VPN toggles off");
            test.tabTo("hidden");
            input.keyClick(Qt.Key_Space);
            test.check(panel.body.hiddenOpen, "hidden Wi-Fi controls expand");
            input.keyClick(Qt.Key_Tab);
            input.keyClick(Qt.Key_A);
            input.keyClick(Qt.Key_B);
            input.keyClick(Qt.Key_Left);
            input.keyClick(Qt.Key_C);
            test.check(panel.body.hiddenName === "acb", "network field keeps text editing arrows");
            test.tabTo("hidden-secured");
            input.keyClick(Qt.Key_Tab);
            const password = panel.Window.window.activeFocusItem;
            test.check(password.echoMode === TextInput.Password, "Tab reaches hidden network password");
            input.mouseClick(password, password.width / 2, password.height / 2);
            test.check(!panel.keyboardMode, "password pointer press disables keyboard mode");
            test.check(!panel.focusRing.visible, "password pointer press hides ring");
            input.keyClick(Qt.Key_Tab);
            test.check(panel.Window.window.activeFocusItem !== password, "first Tab advances from password");
            test.checkRing(panel.Window.window.activeFocusItem, "first Tab from pointer-focused password ring");
            input.mouseClick(password, password.width / 2, password.height / 2);
            input.keyClick(Qt.Key_Backtab);
            test.check(panel.Window.window.activeFocusItem !== password, "first Backtab advances from password");
            test.checkRing(panel.Window.window.activeFocusItem, "first Backtab from pointer-focused password ring");
            test.tabTo("hidden-open");
            input.keyClick(Qt.Key_Return);
            test.check(!panel.body.hiddenSecured, "hidden Wi-Fi security choice");
            test.tabTo("hidden-cancel");
            input.keyClick(Qt.Key_Return);
            test.check(!panel.body.hiddenOpen, "hidden Wi-Fi cancel");
            test.tabTo("cell-power");
            input.keyClick(Qt.Key_Return);
            test.tabTo("profile-performance");
            input.keyClick(Qt.Key_Return);
            test.check(service.profiles.current === "performance", "power profile choice");
            input.wait(40);
            test.check(test.find(panel, "profile-performance").activeFocus, "profile focus survives backend refresh");
            test.tabTo("act-reboot");
            input.keyClick(Qt.Key_Return);
            test.check(panel.body.confirmation === "reboot", "power activation requests confirmation only");
            test.tabTo("confirm-no");
            input.keyClick(Qt.Key_Space);
            test.check(panel.body.confirmation === "", "power cancellation");
            input.keyClick(Qt.Key_Escape);
            test.check(!panel.opened && outside.activeFocus, "Escape closes and focus can return");
            test.check(outside.text === "", "keys did not enter underlying input");
            console.log(test.failed ? "KEYBOARD_FAILED" : "SYSTEM_KEYBOARD_PASS");
            Qt.quit();
        }
    }
}
