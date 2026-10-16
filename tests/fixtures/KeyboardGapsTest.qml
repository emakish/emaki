// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import "settings"

// Keyboard gaps left by the 0.8.1 live check: the snapshot prompt's two actions, the sudo
// card's and warning's buttons, and the focus ring against light and dark backdrops.
// Copied into a temporary shell/ as test.qml by tests/test-keyboard-gaps.py.
ShellRoot {
    id: root
    property int serial: 0
    QtObject {
        id: client
        property bool connected: true
        property var request: ({})
        property var keys: []
        property bool installed: false
        property bool warningAccepted: false
        property bool btrfs: false
        property bool snapguardAvailable: false
        property int submissions: 0
        property string submittedPassword: ""
        property int cancellations: 0
        signal authenticationFailed
        signal rejected
        function submit(id: string, password: string, mode: string, seconds: int): void {
            submittedPassword = password;
            submissions++;
        }
        function cancel(id: string): void {
            cancellations++;
            request = {};
        }
        function revoke(id: string): void {
            keys = [];
        }
        function acknowledgeWarning(): void {
            warningAccepted = true;
        }
    }
    SudoPromptAdapter {
        id: adapter
        client: client
    }
    PolkitPromptController {
        id: controller
        backend: adapter
        locked: false
    }
    // The auth surface's composition (PolkitPromptSurface.qml): the card and the sudo key
    // warning alone in one window, so Tab never has another surface's controls to reach.
    Window {
        id: sudoWindow
        width: 1280
        height: 900
        visible: true
        color: "transparent"
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            hoverEnabled: true
        }
        PolkitPromptCard {
            id: card
            controller: controller
            sudoKeyInstalled: client.installed
            reducedMotion: true
        }
        SudoKeyWarning {
            id: warning
            anchors.centerIn: parent
            width: 820
            height: Math.min(implicitHeight, 860)
            backend: client
            visible: false
            onDismissed: window.warningDismissals++
            onAccepted: window.warningAccepts++
        }
    }
    Window {
        id: window
        width: 1400
        height: 1000
        visible: true
        color: "#808080"
        property int keeps: 0
        property int dismissals: 0
        property int warningDismissals: 0
        property int warningAccepts: 0
        // Takes focus away from every control under test without being one of them.
        TextInput {
            id: sink
            x: 1300
            y: 960
            width: 80
            height: 20
        }
        SnapshotPrompt {
            id: snap
            x: 0
            y: 0
            width: 460
            height: implicitHeight
            snapshot: "7"
            onKeepRequested: window.keeps++
            onDismissed: window.dismissals++
        }
        // Item 24: the ring's pixels over the backdrop the translucent plates let through.
        Rectangle {
            id: stage
            x: 480
            y: 0
            width: 500
            height: 120 + paletteSnap.height
            color: "#f2e6d8"
            Rectangle {
                x: 20
                y: 20
                width: 300
                height: 72
                radius: 16
                color: LiquidPalette.flatPlate
                Item {
                    x: 20
                    y: 20
                    width: 120
                    height: 32
                    KeyboardTarget {
                        id: plateTarget
                        label: "Plate target"
                    }
                }
                WelcomeButton {
                    id: plateAccent
                    x: 160
                    y: 15
                    width: 120
                    primary: true
                    text: "Next"
                }
            }
            SettingsGlass {
                x: 330
                y: 20
                width: 165
                height: 72
            }
            SettingsButton {
                id: settingsProbe
                x: 340
                y: 35
                width: 140
                text: "Settings action"
            }
            SnapshotPrompt {
                id: paletteSnap
                x: 20
                y: 110
                width: 460
                height: implicitHeight
                snapshot: "9"
            }
        }
        TestCase {
            id: test
            name: "KeyboardGaps"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("KEYBOARD_GAPS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function check(ok, message) {
                if (!ok)
                    console.log("KEYBOARD_GAPS_FAIL " + message + " focus=" + window.activeFocusItem);
                verify(ok, message);
            }
            function equal(actual, expected, message) {
                if (actual !== expected)
                    console.log("KEYBOARD_GAPS_FAIL " + message + " actual=" + actual + " expected=" + expected + " focus=" + window.activeFocusItem);
                compare(actual, expected, message);
            }
            function ringOf(item) {
                for (const child of item.children)
                    if (child.strong !== undefined && child.keyboardMode !== undefined && child.border !== undefined)
                        return child;
                return null;
            }
            // The hover look: the plain drop's fill and its 1 px light rim. Strong: 2 px focusOnAccent.
            function checkRing(item, strong, message) {
                const ring = ringOf(item);
                check(ring !== null, message + ": has a FocusRing");
                check(item.activeFocus, message + ": has keyboard focus");
                check(ring.visible, message + ": ring visible");
                check(ring.strong === strong, message + (strong ? ": strong on orange" : ": not strong on pale"));
                check(ring.border.width === (strong ? 2 : 1), message + ": rim width " + ring.border.width);
                check(Qt.colorEqual(ring.border.color, strong ? LiquidPalette.focusOnAccent : LiquidPalette.flatDropRim), message + ": rim colour");
                check(Qt.colorEqual(ring.color, LiquidPalette.flatDrop), message + ": hover fill");
            }
            function noRing(item, message) {
                const ring = ringOf(item);
                check(ring !== null && !ring.visible, message + ": no ring without focus");
            }
            function named(item, name) {
                return findChild(item, name);
            }
            function luminance(c) {
                const linear = [c.r, c.g, c.b].map(v => v <= .04045 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4));
                return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
            }
            function ratio(a, b) {
                const x = luminance(a), y = luminance(b);
                return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
            }
            // Focused against unfocused at the same stage pixels along the control's straight top
            // edge (both rows of a 2 px rim), away from the rounded corners and the label.
            function rimContrast(item) {
                sink.forceActiveFocus();
                wait(30);
                const before = grabImage(stage);
                item.forceActiveFocus(Qt.TabFocusReason);
                wait(30);
                check(item.activeFocus, "contrast control focused");
                const after = grabImage(stage);
                const r = item.mapToItem(stage, 0, 0, item.width, item.height);
                const rows = [];
                for (const y of [Math.round(r.y), Math.round(r.y) + 1]) {
                    const row = [];
                    for (let x = Math.round(r.x) + 15; x < Math.round(r.x + r.width) - 15; ++x)
                        row.push(ratio(before.pixel(x, y), after.pixel(x, y)));
                    rows.push(row);
                }
                return {
                    high: Math.max(...rows[0]),
                    low: Math.min(...rows[0], ...rows[1])
                };
            }
            function init() {
                snap.busy = false;
                snap.succeeded = false;
                snap.error = "";
                window.keeps = 0;
                window.dismissals = 0;
                window.requestActivate();
            }

            // Item 9: the snapshot prompt.
            function test_snapshot_arrows_and_tab() {
                snap.takeFocus();
                wait(30);
                const later = named(snap, "snapshotLater");
                const keep = named(snap, "snapshotKeep");
                checkRing(later, false, "Not now on open");
                noRing(keep, "Keep this state while Not now is focused");
                keyClick(Qt.Key_Right);
                checkRing(keep, true, "Right reaches Keep this state");
                noRing(later, "Not now after Right");
                keyClick(Qt.Key_Left);
                checkRing(later, false, "Left returns to Not now");
                keyClick(Qt.Key_Down);
                checkRing(keep, true, "Down reaches Keep this state");
                keyClick(Qt.Key_Up);
                checkRing(later, false, "Up returns to Not now");
                keyClick(Qt.Key_Tab);
                checkRing(keep, true, "Tab reaches Keep this state");
                keyClick(Qt.Key_Tab);
                checkRing(later, false, "Tab wraps to Not now");
                keyClick(Qt.Key_Backtab);
                checkRing(keep, true, "Shift+Tab reaches Keep this state");
                keyClick(Qt.Key_Backtab);
                checkRing(later, false, "Shift+Tab returns to Not now");
                equal(window.keeps + window.dismissals, 0, "moving focus never activates");
            }
            function test_snapshot_enter() {
                snap.takeFocus();
                wait(30);
                keyClick(Qt.Key_Return);
                equal(window.dismissals, 1, "Return on Not now dismisses");
                equal(window.keeps, 0, "Return on Not now does not keep");
                keyClick(Qt.Key_Enter);
                equal(window.dismissals, 2, "keypad Enter on Not now dismisses");
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Enter);
                equal(window.keeps, 1, "keypad Enter on Keep this state keeps");
                keyClick(Qt.Key_Return);
                equal(window.keeps, 2, "Return on Keep this state keeps");
                equal(window.dismissals, 2, "keeping never dismisses");
            }
            function test_snapshot_escape_and_states() {
                snap.takeFocus();
                wait(30);
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Escape);
                equal(window.dismissals, 1, "Escape from Keep this state dismisses");
                equal(window.keeps, 0, "Escape never keeps");
                snap.busy = true;
                wait(30);
                keyClick(Qt.Key_Escape);
                equal(window.dismissals, 1, "Escape is ignored while keeping");
                snap.busy = false;
                wait(50);
                check(named(snap, "snapshotLater").activeFocus, "focus returns to Not now after keeping ends");
                snap.succeeded = true;
                wait(30);
                const later = named(snap, "snapshotLater");
                for (const key of [Qt.Key_Right, Qt.Key_Down, Qt.Key_Tab]) {
                    keyClick(key);
                    checkRing(later, false, "after success every move stays on Done");
                }
                keyClick(Qt.Key_Enter);
                equal(window.dismissals, 2, "Enter on Done dismisses");
                equal(window.keeps, 0, "success state never keeps");
            }

            // Item 24: the ring's colours exist and are seen over light and dark backdrops, in
            // both themes (nothing saved is light).
            function test_palette_ring_on_light_and_dark() {
                const saved = SettingsBridge.values;
                const cases = [[settingsProbe, false, "Settings button"], [plateTarget, false, "bar-like plate target"], [plateAccent, true, "orange button on the plate"], [named(paletteSnap, "snapshotLater"), false, "prompt Not now"], [named(paletteSnap, "snapshotKeep"), true, "prompt Keep this state"]];
                // Exercise both explicit choices independently of the default.
                for (const theme of ["light", "dark"]) {
                    for (const accent of ShellPalette.accents) {
                        SettingsBridge.values = [
                            {
                                key: "appearance.theme",
                                value: theme
                            },
                            {
                                key: "appearance.accent",
                                value: accent.id
                            }
                        ];
                        wait(30);
                        for (const ink of [LiquidPalette.flatDrop, LiquidPalette.flatDropRim, LiquidPalette.focusOnAccent, LiquidPalette.focusOnSelected])
                            check(ink.a > 0, theme + ": ring ink has alpha");
                        check(LiquidPalette.focusOnAccent.a > LiquidPalette.flatDropRim.a && LiquidPalette.focusOnSelected.a > LiquidPalette.flatDropRim.a, theme + ": strong rim is denser than the hover rim");
                        for (const backdrop of [["light", "#f2e6d8"], ["dark", "#392f29"], ["white", "#ffffff"], ["black", "#000000"]]) {
                            stage.color = backdrop[1];
                            for (const entry of cases) {
                                const c = rimContrast(entry[0]);
                                const label = accent.id + " " + entry[2] + " on " + backdrop[0] + " in " + theme;
                                console.log("KEYBOARD_GAPS_CONTRAST " + accent.id + " " + theme + " " + backdrop[0] + " " + entry[2].replace(/ /g, "-") + " high=" + c.high.toFixed(2) + " low=" + c.low.toFixed(2));
                                if (entry[0] === settingsProbe) {
                                    check(Qt.colorEqual(settingsProbe.background.border.color, SettingsTheme.accent), label + ": existing accent focus rim");
                                    check(settingsProbe.background.border.width === 2, label + ": accent rim width");
                                } else if (entry[1])
                                    check(c.low >= 3, label + ": strong rim below 3:1 (" + c.low.toFixed(2) + ")");
                                else
                                    check(c.high >= 1.2 && c.high <= 2.2, label + ": hover-look rim not visible or not light (" + c.high.toFixed(2) + ")");
                            }
                        }
                    }
                }
                SettingsBridge.values = saved;
                stage.color = "#f2e6d8";
            }

            // Item 18: sudo card controls and the sudo key warning.
            function begin() {
                client.request = {
                    id: String(++root.serial),
                    accountName: "fixture",
                    requester: "Fixture Product",
                    command: "fixture-program",
                    arguments: [],
                    keyEligible: true,
                    turnEligible: true
                };
                sudoWindow.requestActivate();
                tryVerify(() => sudoWindow.active, 3000, "auth window active");
                tryVerify(() => controller.opened && controller.accepting, 3000, "sudo request accepting");
                card.focusPassword();
                wait(20);
            }
            function test_sudo_tab_order_keeps_text() {
                begin();
                const password = named(card, "polkit-password");
                const reveal = named(card, "polkit-reveal");
                const details = named(card, "polkit-details");
                check(password.activeFocus, "password focused");
                keyClick(Qt.Key_A);
                keyClick(Qt.Key_B);
                const order = [];
                for (let i = 0; i < 6; ++i) {
                    keyClick(Qt.Key_Tab);
                    order.push(sudoWindow.activeFocusItem === reveal ? "reveal" : sudoWindow.activeFocusItem === details ? "details" : sudoWindow.activeFocusItem === password ? "password" : String(sudoWindow.activeFocusItem.text));
                }
                equal(order.join(","), "reveal,details,Cancel,Allow,password,reveal", "Tab order through the card");
                check(reveal.activeFocus && ringOf(reveal).visible, "eye shows its ring under keyboard focus");
                keyClick(Qt.Key_Backtab);
                check(password.activeFocus, "Shift+Tab from the eye returns to the password");
                keyClick(Qt.Key_Backtab);
                equal(String(sudoWindow.activeFocusItem.text), "Allow", "Shift+Tab from the password wraps to Allow");
                card.focusPassword();
                keyClick(Qt.Key_C);
                equal(password.text, "abc", "Tab round trip keeps the typed password");
                for (let i = 0; i < 4; ++i)
                    keyClick(Qt.Key_Tab);
                equal(String(sudoWindow.activeFocusItem.text), "Allow", "Tab reaches Allow");
                keyClick(Qt.Key_Return);
                equal(client.submissions, 1, "Return on Allow submits");
                equal(client.submittedPassword, "abc", "Allow submits the typed password");
            }
            function test_sudo_cancel_and_escape() {
                begin();
                for (let i = 0; i < 3; ++i)
                    keyClick(Qt.Key_Tab);
                equal(String(sudoWindow.activeFocusItem.text), "Cancel", "Tab reaches Cancel");
                const before = client.cancellations;
                keyClick(Qt.Key_Enter);
                equal(client.cancellations, before + 1, "keypad Enter on Cancel cancels");
                check(!controller.opened, "Cancel closes the card");
                equal(client.submissions, 0, "Cancel never submits");
                begin();
                const cancelled = client.cancellations;
                keyClick(Qt.Key_Escape);
                equal(client.cancellations, cancelled + 1, "Escape with nothing expanded cancels");
            }
            function test_sudo_warning_keyboard() {
                client.installed = true;
                client.warningAccepted = false;
                window.warningDismissals = 0;
                window.warningAccepts = 0;
                warning.visible = true;
                sudoWindow.requestActivate();
                tryVerify(() => sudoWindow.active, 3000, "auth window active");
                warning.forceActiveFocus();
                wait(30);
                const dismiss = named(warning, "sudo-key-warning-dismiss");
                const accept = named(warning, "sudo-key-warning-accept");
                keyClick(Qt.Key_Tab);
                check(dismiss.activeFocus, "Tab reaches the warning's Not now");
                keyClick(Qt.Key_Right);
                check(accept.activeFocus, "Right reaches the warning's acknowledge button");
                keyClick(Qt.Key_Left);
                check(dismiss.activeFocus, "Left returns to Not now");
                keyClick(Qt.Key_Return);
                equal(window.warningDismissals, 1, "Return on Not now dismisses the warning");
                equal(window.warningAccepts, 0, "Not now does not acknowledge");
                keyClick(Qt.Key_Tab);
                check(accept.activeFocus, "Tab moves on to acknowledge");
                keyClick(Qt.Key_Return);
                equal(window.warningAccepts, 1, "Return on acknowledge accepts");
                check(client.warningAccepted, "acknowledge reaches the backend");
                warning.forceActiveFocus();
                keyClick(Qt.Key_Escape);
                equal(window.warningDismissals, 2, "Escape dismisses the warning");
                warning.visible = false;
                client.installed = false;
            }
        }
    }
}
