// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import InputMaskProbe 1.0

// test-bar.py: the bar's input mask after the bar slid out under a fullscreen window and
// back. Every time the mask region announces a change, the logo's position in the bar's
// window is recorded; after the bar has come back to rest the last recorded position must
// be the logo's position at rest, or the compositor keeps an input region from mid-slide.
Timer {
    id: driver
    required property ShellScene scene
    property var surfaces: null
    interval: 20
    repeat: true
    running: true
    property int stage: 0
    property var mask: null
    property var seen: []
    property real restedAt: 0
    property int pointIndex: 0
    readonly property var bandPoints: [[0, 25], [400, 25], [900, 25], [400, 2], [900, 5], [400, 8], [1279, 43]]
    property InputMaskProbe probe: InputMaskProbe {}
    function next(value: int): void {
        stage = value;
        restedAt = Date.now();
    }
    function checkBand(expected: bool): void {
        for (const point of bandPoints)
            check(probe.contains(scene.bar, point[0], point[1]) === expected, "band mask at " + point + " expected " + expected);
    }
    function logoY(): real {
        return Math.round(scene.bar.logo.mapToItem(null, 0, 0).y);
    }
    function publish(full: bool): void {
        const name = scene.outputName || "Fixture";
        const outputs = {};
        outputs[name] = {
            logical: {
                width: scene.viewportWidth,
                height: scene.viewportHeight
            }
        };
        scene.niri.receive(JSON.stringify({
            schema_version: 1,
            ipc_release: "26.04",
            generation: 1,
            connection: {
                status: "connected",
                reason: "synchronized"
            },
            model: {
                focused_output: name,
                overview_open: false,
                links_pending: false,
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                },
                outputs: outputs,
                workspaces: {
                    "1": {
                        id: 1,
                        idx: 1,
                        is_active: true,
                        is_focused: true,
                        output: name
                    }
                },
                windows: full ? {
                    "8": {
                        id: 8,
                        workspace_id: 1,
                        layout: {
                            tile_size: [scene.viewportWidth, scene.viewportHeight]
                        }
                    }
                } : {},
                casts: {}
            }
        }));
    }
    function check(ok: bool, label: string): void {
        if (!ok)
            throw new Error(label + " " + JSON.stringify({
                y: scene.bar.y,
                logo: logoY(),
                seen: seen,
                presentation: scene.barPolicy.presentation
            }));
    }
    onTriggered: {
        try {
            if (stage === 0) {
                if (!surfaces)
                    return;
                probe.move(scene.bar, 800, 400);
                publish(false);
                stage = 1;
            } else if (stage === 1) {
                if (scene.barPolicy.presentation !== "clear" || scene.bar.y !== 0)
                    return;
                mask = scene.bar.QsWindow.window?.mask ?? null;
                check(mask !== null, "the bar's window has no input mask");
                mask.changed.connect(() => driver.seen = driver.seen.concat([driver.logoY()]));
                publish(true);
                stage = 2;
            } else if (stage === 2) {
                if (scene.bar.y !== -60)
                    return;
                check(!scene.barPolicy.barVisible, "a covering window did not hide the bar");
                publish(false);
                stage = 3;
            } else if (stage === 3) {
                if (scene.bar.y !== 0)
                    return;
                restedAt = Date.now();
                stage = 4;
            } else if (stage === 4) {
                if (Date.now() - restedAt < 200)
                    return;
                check(seen.length > 0, "the mask never changed while the bar slid");
                check(logoY() === Metrics.top, "the logo did not come back to the top margin");
                check(seen[seen.length - 1] === logoY(), "the input mask was last rebuilt with the logo away from its place");
                console.log("BAR_MASK_OK " + JSON.stringify({
                    changes: seen.length,
                    last: seen[seen.length - 1]
                }));
                checkBand(false);
                scene.barPolicy.autoHide = true;
                next(5);
            } else if (stage === 5) {
                if (scene.bar.y !== -60 || Date.now() - restedAt < 350)
                    return;
                checkBand(false);
                check(probe.contains(scene.bar, 100, 1), "hidden reveal strip missing");
                probe.move(scene.bar, 100, 1);
                next(6);
            } else if (stage === 6) {
                if (!scene.barPolicy.revealed || scene.bar.y !== 0)
                    return;
                checkBand(true);
                probe.move(scene.bar, 28, 25);
                next(7);
            } else if (stage === 7) {
                if (Date.now() - restedAt < scene.barPolicy.hideDelayMs + 150)
                    return;
                check(scene.barPolicy.barVisible && scene.barPolicy.pointerInside, "pointer did not hold the panel");
                checkBand(true);
                if (pointIndex < bandPoints.length) {
                    const point = bandPoints[pointIndex++];
                    probe.move(scene.bar, point[0], point[1]);
                    next(7);
                } else {
                    probe.move(scene.bar, 800, 400);
                    next(8);
                }
            } else if (stage === 8) {
                if (scene.barPolicy.barVisible || scene.bar.y !== -60)
                    return;
                checkBand(false);
                scene.barPolicy.openUI = true;
                next(9);
            } else if (stage === 9) {
                if (scene.bar.y !== 0 || Date.now() - restedAt < 350)
                    return;
                check(scene.barPolicy.barVisible, "UI reveal did not show the panel");
                checkBand(false);
                scene.barPolicy.openUI = false;
                next(10);
            } else if (stage === 10) {
                // The closing grace must not turn a UI reveal into a pointer reveal.
                checkBand(false);
                if (scene.barPolicy.barVisible || scene.bar.y !== -60)
                    return;
                scene.barKeyboardActive = true;
                next(11);
            } else if (stage === 11) {
                if (scene.bar.y !== 0 || Date.now() - restedAt < 350)
                    return;
                check(scene.barPolicy.keyboardActive, "keyboard reveal did not reach policy");
                checkBand(false);
                // Entering a visible island deliberately transfers the hold to the pointer.
                probe.move(scene.bar, 28, 25);
                next(12);
            } else if (stage === 12) {
                if (Date.now() - restedAt < 100)
                    return;
                checkBand(true);
                scene.barKeyboardActive = false;
                probe.move(scene.bar, 400, 25);
                next(13);
            } else if (stage === 13) {
                if (Date.now() - restedAt < scene.barPolicy.hideDelayMs + 150)
                    return;
                check(scene.barPolicy.barVisible && scene.barPolicy.pointerInside, "pointer takeover did not hold the panel across a gap");
                checkBand(true);
                probe.move(scene.bar, 800, 400);
                next(14);
            } else if (stage === 14) {
                if (scene.barPolicy.barVisible || scene.bar.y !== -60)
                    return;
                checkBand(false);
                scene.barPolicy.openUI = true;
                next(15);
            } else if (stage === 15) {
                if (scene.bar.y !== 0 || Date.now() - restedAt < 350)
                    return;
                checkBand(false);
                probe.move(scene.bar, 100, 1);
                next(16);
            } else if (stage === 16) {
                if (Date.now() - restedAt < scene.barPolicy.revealDelayMs + 150)
                    return;
                checkBand(true);
                scene.barPolicy.openUI = false;
                probe.move(scene.bar, 400, 25);
                next(17);
            } else if (stage === 17) {
                if (Date.now() - restedAt < scene.barPolicy.hideDelayMs + 150)
                    return;
                check(scene.barPolicy.barVisible && scene.barPolicy.pointerInside, "edge takeover did not hold the panel across a gap");
                checkBand(true);
                probe.move(scene.bar, 800, 400);
                next(18);
            } else if (stage === 18) {
                if (scene.barPolicy.barVisible || scene.bar.y !== -60)
                    return;
                checkBand(false);
                console.log("BAR_BAND_OK native mask, pointer and edge takeover; hidden, pinned, UI and keyboard pass-through");
                stop();
                Qt.exit(0);
            }
        } catch (error) {
            console.error("BAR_MASK_FAILED " + error.message);
            stop();
            Qt.exit(2);
        }
    }
}
