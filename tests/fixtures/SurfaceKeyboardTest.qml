// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import Quickshell.Wayland

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
                    verify(!scene.openKeyboard("bar"), "covered bar cannot request latent keyboard ownership");
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
        }
    }
}
