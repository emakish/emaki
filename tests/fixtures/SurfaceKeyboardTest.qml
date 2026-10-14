// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import Quickshell.Wayland
import "Keyboard.js" as Keyboard

// Offscreen substitutes only the unavailable layer-window backend. Production
// bindings and focus-loss handlers run unchanged; native focus is not proven.
ShellRoot {
    NiriService {
        id: service
        binary: ""
    }
    ShellScene {
        id: scene
        niri: service
        headless: true
        testWidth: 1440
        testHeight: 1000
        borderMode: "soft"
        reservedSpace: 52
        skipIntro: true
    }
    Surfaces {
        id: surfaces
        controller: scene
    }
    Window {
        id: window
        visible: true
        TestCase {
            name: "SurfaceKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("SURFACE_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function none() {
                compare(surfaces.barKeyboardFocus, WlrKeyboardFocus.None, "closed bar releases layer policy");
                compare(surfaces.dockKeyboardFocus, WlrKeyboardFocus.None, "closed dock releases layer policy");
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.None, "closed panel releases layer policy");
            }
            function fullscreen(covered) {
                service.connection = "connected";
                service.model = {
                    outputs: {
                        "": {
                            logical: {
                                width: 1440,
                                height: 1000
                            }
                        }
                    },
                    workspaces: {
                        "1": {
                            id: 1,
                            idx: 1,
                            output: "",
                            is_active: true
                        }
                    },
                    windows: {
                        "1": {
                            id: 1,
                            workspace_id: 1,
                            layout: {
                                tile_size: covered ? [1440, 1000] : [700, 600]
                            }
                        }
                    },
                    keyboard_layouts: {
                        names: ["English (US)"],
                        current_idx: 0
                    },
                    overview_open: false
                };
                compare(scene.presentationState, covered ? "covered" : "clear");
            }
            function policy(layer) {
                return layer === "bar" ? surfaces.barKeyboardFocus : layer === "dock" ? surfaces.dockKeyboardFocus : surfaces.panelKeyboardFocus;
            }
            function test_policy() {
                none();
                fullscreen(false);
                tryVerify(() => service.layerFocusSupported);
                for (const entry of ["bar", "dock", "sound", "privacy", "shortcuts"]) {
                    verify(scene.openKeyboard(entry));
                    wait(30);
                    const layer = entry === "bar" || entry === "dock" ? entry : "panel";
                    compare(policy(layer), WlrKeyboardFocus.Exclusive, entry + " explicitly requests focus");
                    compare(surfaces.armedLayer, "", "request cannot impersonate actual activation");
                    surfaces.keyboardActiveChanged(layer, true);
                    compare(surfaces.armedLayer, layer);
                    compare(policy(layer), WlrKeyboardFocus.OnDemand, entry + " permits native outside focus");
                    surfaces.keyboardActiveChanged(layer, false);
                    wait(30);
                    none();
                    verify(!scene.modalOpen && !scene.barKeyboardActive && !scene.dock.keyboardActive);
                }
                scene.openSystem("sound");
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.Exclusive, "pointer panel retains modal policy");
                scene.closeAll();
                none();
                for (const acknowledged of [false, true]) {
                    verify(scene.openKeyboard("bar"));
                    if (acknowledged)
                        surfaces.keyboardActiveChanged("bar", true);
                    scene.focusedOutput = false;
                    none();
                    compare(surfaces.pendingLayer, "", "output change cancels unacknowledged requests");
                    compare(surfaces.armedLayer, "", "output change releases acknowledged ownership");
                    verify(!scene.openKeyboard("bar"), "inactive output cannot acquire focus");
                    scene.focusedOutput = true;
                    none();
                }
                fullscreen(false);
                for (const entry of ["bar", "dock", "sound"]) {
                    verify(scene.openKeyboard(entry));
                    fullscreen(true);
                    none();
                    verify(!scene.modalOpen && !scene.barKeyboardActive && !scene.dock.keyboardActive);
                    verify(scene.openKeyboard("bar"), "fullscreen panel is keyboard reachable");
                    verify(scene.barPolicy.barVisible, "keyboard visit reveals the fullscreen panel");
                    compare(surfaces.barKeyboardFocus, WlrKeyboardFocus.Exclusive);
                    surfaces.keyboardActiveChanged("bar", true);
                    compare(surfaces.barKeyboardFocus, WlrKeyboardFocus.OnDemand);
                    scene.closeAll();
                    none();
                    fullscreen(false);
                    wait(30);
                    none();
                    verify(!scene.barKeyboardActive, "fullscreen exit does not reacquire keyboard");
                }
                verify(scene.openKeyboard("dock"));
                scene.dockStore.on = false;
                wait(30);
                none();
                scene.dockStore.on = true;
                wait(30);
                none();
                verify(scene.openKeyboard("bar"));
                scene.enabled = false;
                none();
                scene.enabled = true;
                none();
                verify(scene.openKeyboard("dock"));
                // Unknown and older compositors retain the original panel policy,
                // and never acquire keyboard ownership for the bar or dock.
                service._layerFocusSupported = false;
                none();
                compare(surfaces.pendingLayer, "", "lost capability clears the pending request");
                verify(!scene.openKeyboard("bar"));
                verify(!scene.openKeyboard("dock"));
                none();
                verify(scene.openKeyboard("sound"));
                compare(surfaces.pendingLayer, "");
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.Exclusive);
                surfaces.keyboardActiveChanged("panel", true);
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.Exclusive);
                service._layerFocusSupported = true;
                compare(surfaces.pendingLayer, "panel", "late capability reply arms the already-open keyboard panel");
                surfaces.keyboardActiveChanged("panel", true);
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.OnDemand);
                service._layerFocusSupported = false;
                none();
                scene.openSystem("bt");
                scene.services.pendingKind = "bt-pair";
                compare(surfaces.pendingLayer, "");
                compare(surfaces.panelKeyboardFocus, WlrKeyboardFocus.OnDemand);
                scene.services.pendingKind = "";
                scene.closeAll();
                none();
            }
            // Escape from a panel opened from a bar cell returns to that cell. The order is the
            // compositor's: the panel closes while the bar's surface is still inactive, and the
            // bar is activated afterwards (Surfaces then asks the bar to take focus again).
            function where() {
                const item = scene.bar.keyboardItem;
                return " (bar focus: " + (item ? item.label || item.key : "none") + ", bar active: " + scene.bar.Window.window?.active + ", bar visible: " + scene.bar.Window.window?.visible + ", barKeyboard: " + scene.barKeyboardActive + ", targets: " + Keyboard.targets(scene.bar).map(t => t.label || t.key).join("|") + ", afi: " + scene.bar.Window.window?.activeFocusItem + ")";
            }
            function ok(condition, message) {
                if (!condition)
                    console.log("SURFACE_KEYBOARD_FAIL " + message + where());
                verify(condition, message);
            }
            function waitFor(condition, timeout, message) {
                try {
                    tryVerify(condition, timeout, message);
                } catch (error) {
                    console.log("SURFACE_KEYBOARD_FAIL " + message + where());
                    throw error;
                }
            }
            function test_return() {
                // test_policy ends on a compositor without layer focus.
                service._layerFocusSupported = true;
                fullscreen(false);
                waitFor(() => service.layerFocusSupported);
                scene.focusedOutput = true;
                // Map the layer windows (the headless scene has no output of its own).
                scene.output = Quickshell.screens[0];
                waitFor(() => [scene.bar, scene.dock, scene.clockPanel].every(item => item.Window.window?.visible === true), 1000, "the layer surfaces are mapped");
                // Offscreen activates each window it shows; let that settle before playing the compositor.
                wait(200);
                const barWindow = scene.bar.Window.window;
                const panelWindow = scene.clockPanel.Window.window;
                ok(barWindow && panelWindow && barWindow !== panelWindow, "the bar and the panels are separate surfaces");
                for (const label of ["Calendar and notifications", "Open launcher", "cell-sound"]) {
                    ok(scene.openKeyboard("bar"));
                    barWindow.requestActivate();
                    waitFor(() => barWindow.active && scene.bar.keyboardItem !== null, 1000, "the compositor activates the bar");
                    const origin = Keyboard.targets(scene.bar).find(item => item.label === label || item.key === label);
                    ok(origin !== undefined, label + " is a bar cell");
                    origin.forceActiveFocus(Qt.TabFocusReason);
                    keyClick(Qt.Key_Return);
                    waitFor(() => scene.modalOpen, 1000, label + " opens its panel");
                    panelWindow.requestActivate();
                    waitFor(() => panelWindow.active, 1000, "the compositor activates the panel");
                    wait(100);
                    if (label === "Open launcher") {
                        keyClick(Qt.Key_A);
                        keyClick(Qt.Key_B);
                        compare(scene.input.queryText, "ab", "the launcher takes the typed query");
                        keyClick(Qt.Key_Escape);
                        ok(scene.launcherOpen, "the first Escape only clears the query");
                        compare(scene.input.queryText, "");
                    }
                    keyClick(Qt.Key_Escape);
                    wait(30);
                    ok(!scene.modalOpen, label + ": Escape closes the panel");
                    ok(scene.barKeyboardActive, label + ": Escape steps back to the bar");
                    barWindow.requestActivate();
                    waitFor(() => barWindow.active, 1000);
                    waitFor(() => scene.bar.keyboardItem === origin, 2000, label + ": Escape returns to the cell that opened the panel, not " + (scene.bar.keyboardItem?.label ?? scene.bar.keyboardItem?.key));
                    wait(400);
                    ok(scene.bar.keyboardItem === origin, label + ": the cell keeps the keyboard, not " + (scene.bar.keyboardItem?.label ?? scene.bar.keyboardItem?.key));
                    ok(scene.bar.returnItem === null, label + ": the return has settled");
                    keyClick(Qt.Key_Right);
                    if (scene.bar.keyboardItem === origin)
                        keyClick(Qt.Key_Left);
                    ok(scene.bar.keyboardItem !== origin, label + ": arrows walk on from the cell");
                    scene.closeAll();
                    wait(30);
                    none();
                }
            }
        }
    }
}
