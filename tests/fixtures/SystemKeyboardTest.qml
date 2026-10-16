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
    // Nothing saved is light, the default; tests/test-system-keyboard.py also runs dark.
    Component.onCompleted: if (Quickshell.env("EMAKI_TEST_THEME"))
        SettingsBridge.values = [
            {
                key: "appearance.theme",
                value: Quickshell.env("EMAKI_TEST_THEME")
            }
        ]
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
    function named(item: Item, name: string): Item {
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const found = named(child, name);
            if (found)
                return found;
        }
        return null;
    }
    // Pale controls: the hover look (1 px light rim); orange ones: the strong rim.
    function checkRing(item: Item, message: string, strong: bool): void {
        const ring = focusRing(item);
        check(ring !== null && ring.visible, message + " visible");
        if (!ring)
            return;
        check(ring.focused === item, message + " follows focused control");
        check(ring.strong === strong && ring.border.width === (strong ? 2 : 1), message + (strong ? " strong rim on orange" : " light hover rim"));
        let captured = false;
        ring.grabToImage(result => {
            result.saveToFile(Quickshell.env("EMAKI_TEST_EVIDENCE") + "/" + item.key + ".png");
            captured = true;
        });
        for (let tries = 0; !captured && tries < 100; ++tries)
            input.wait(10);
        check(captured, "ring captured");
        // The hover rim's ink, also for the strong rim over a selected drop (a tab): dark shades
        // that drop, so there the ink is dark's light one. A switch's flat track is the accent
        // itself in both themes: focusOnAccent, the warm dark ink.
        const ink = strong && item.drop === false ? LiquidPalette.focusOnAccent : LiquidPalette.flatDropRim;
        check(Math.abs(ring.border.color.r - ink.r) < .01 && Math.abs(ring.border.color.g - ink.g) < .01 && Math.abs(ring.border.color.b - ink.b) < .01 && ring.border.color.a < 1, message + " existing hover rim");
        check(ring.color.toString() === LiquidPalette.flatDrop.toString(), message + " existing hover fill");
        // Nothing drawn over the rim: the ring's only child, the two-colour rim's outer row
        // (FocusRing.outerInk), is set by no control here and must stay hidden.
        check(ring.children.every(child => !child.visible), message + " no dark inner frame");
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
        Rectangle {
            id: pixelProbe
            width: 480
            height: 80 + snapshotProbe.height
            color: "#fff8f3"
            z: 10000
            Row {
                x: 20
                y: 19
                spacing: 10
                WelcomeButton {
                    id: primaryProbe
                    width: 100
                    primary: true
                    text: "Next"
                }
                WelcomeButton {
                    id: selectedProbe
                    width: 100
                    selected: true
                    text: "Selected"
                }
                WelcomeButton {
                    id: paleProbe
                    width: 100
                    text: "Back"
                }
            }
            // The recovery prompt's opaque-accent "Keep this state" beside its pale "Not now".
            SnapshotPrompt {
                id: snapshotProbe
                y: 80
                width: 460
                snapshot: "42"
            }
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
            const keepProbe = test.named(snapshotProbe, "snapshotKeep");
            const laterProbe = test.named(snapshotProbe, "snapshotLater");
            test.check(keepProbe !== null && laterProbe !== null, "snapshot prompt actions exist");
            for (const entry of [[keepProbe, "keep"], [laterProbe, "later"]]) {
                const r = entry[0].mapToItem(pixelProbe, 0, 0, entry[0].width, entry[0].height);
                console.log("CONTRAST_RECT " + entry[1] + " " + r.x + " " + r.y + " " + r.width + " " + r.height);
            }
            for (const entry of [[primaryProbe, "primary"], [selectedProbe, "selected"], [paleProbe, "pale"], [keepProbe, "keep"], [laterProbe, "later"]]) {
                for (const focused of [false, true]) {
                    if (focused)
                        entry[0].forceActiveFocus();
                    else
                        outside.forceActiveFocus();
                    input.wait(30);
                    let captured = false;
                    pixelProbe.grabToImage(result => {
                        result.saveToFile(Quickshell.env("EMAKI_TEST_EVIDENCE") + "/contrast-" + entry[1] + "-" + focused + ".png");
                        captured = true;
                    });
                    for (let tries = 0; !captured && tries < 100; ++tries)
                        input.wait(10);
                    test.check(captured, "composited focus captured");
                }
            }
            pixelProbe.visible = false;
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
            test.checkRing(panel.Window.window.activeFocusItem, "first Tab from pointer-opened slider ring", false);
            panel.takeFocus(false);
            input.wait(30);
            input.keyClick(Qt.Key_Backtab);
            test.check(panel.keyboardMode, "first Backtab from pointer-opened slider enables keyboard mode");
            test.checkRing(panel.Window.window.activeFocusItem, "first Backtab from pointer-opened slider ring", false);
            panel.takeFocus();
            input.wait(30);
            test.checkRing(initial, "system slider focus ring", false);
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
            test.checkRing(lightTab, "selected system tab focus ring", true);
            const brightness = test.tabTo("brightness");
            input.keyClick(Qt.Key_Home);
            test.check(service.brightness.percent === 5, "brightness respects minimum");
            input.keyClick(Qt.Key_PageUp);
            test.check(service.brightness.percent === 15, "brightness page increment reaches service");
            const night = test.tabTo("night");
            input.keyClick(Qt.Key_Space);
            test.check(service.night.on, "night light keyboard toggle");
            input.wait(30);
            test.checkRing(night, "selected orange switch focus ring", true);
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
            const forget = test.find(panel, "dev-fixture-device:forget");
            test.check(forget.visible, "paired device focus reveals Forget");
            input.keyClick(Qt.Key_Tab);
            test.check(forget.activeFocus && forget.visible, "Forget stays visible as Tab enters it");
            input.keyClick(Qt.Key_Backtab);
            test.check(test.find(panel, "dev-fixture-device").activeFocus && forget.visible, "Backtab returns to the paired device");
            test.tabTo("search");
            test.check(!forget.visible, "Forget hides after focus leaves the device");
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
            test.checkRing(panel.Window.window.activeFocusItem, "first Tab from pointer-focused password ring", false);
            input.mouseClick(password, password.width / 2, password.height / 2);
            input.keyClick(Qt.Key_Backtab);
            test.check(panel.Window.window.activeFocusItem !== password, "first Backtab advances from password");
            test.checkRing(panel.Window.window.activeFocusItem, "first Backtab from pointer-focused password ring", false);
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
