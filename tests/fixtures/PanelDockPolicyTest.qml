// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell
import Quickshell.Wayland

ShellRoot {
    id: root
    property int barLayer: 0
    property int dockLayer: 0
    property bool insetMarkerVisible: false
    property int insetMarkerHeight: 0
    property var panel: null
    property var dock: null
    Component {
        id: panelFactory
        BarPolicy {
            presentation: "clear"
        }
    }
    Component {
        id: dockFactory
        DockPolicy {
            presentation: "clear"
        }
    }
    NiriService {
        id: service
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 1280
        height: 800
        Rectangle {
            anchors.fill: parent
            color: "#c9c0b0"
        }
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: 1280
            testHeight: 800
            reservedSpace: 52
            skipIntro: false
        }
    }
    TestCase {
        name: "PanelDockPolicy"
        when: true
        onCompletedChanged: if (completed) {
            console.log("PANEL_DOCK_RESULT " + qtest_results.passCount + " " + qtest_results.failCount);
            Qt.quit();
        }
        function eventually(object, key, expected, timeout) {
            try {
                tryCompare(object, key, expected, timeout || 5000);
            } catch (error) {
                console.error("POLICY_FAILURE wait " + key + ": " + object[key] + " != " + expected + " " + new Error().stack);
                throw error;
            }
        }
        function equal(actual, expected, label) {
            if (actual !== expected)
                console.error("POLICY_FAILURE " + label + ": " + actual + " != " + expected + " " + new Error().stack);
            compare(actual, expected, label || "");
        }
        function check(value) {
            if (!value)
                console.error("POLICY_FAILURE " + new Error().stack);
            verify(value);
        }
        function init() {
            root.panel = panelFactory.createObject(scene);
            root.dock = dockFactory.createObject(scene);
        }
        function cleanup() {
            root.panel.destroy();
            root.dock.destroy();
        }
        function mode(panel, value) {
            panel.autoHide = value === "always" || value === "desktop";
            panel.autoHideFullscreen = value === "always" || value === "fullscreen";
        }
        function reserve(choice) {
            return choice === "always" || choice === "desktop" ? 10 : 52;
        }
        function inset(choice, fullscreen) {
            return fullscreen && (choice === "desktop" || choice === "never") ? 52 : 0;
        }
        function state(label) {
            console.log("POLICY_TRACE " + JSON.stringify({
                label: label,
                time: Date.now(),
                panelVisible: root.panel.barVisible,
                panelRevealed: root.panel.revealed,
                dockVisible: root.dock.dockVisible,
                dockRevealed: root.dock.revealed
            }));
        }
        function test_defaults_and_modes() {
            equal(root.panel.revealDelayMs, 150);
            equal(root.panel.hideDelayMs, 500);
            equal(root.panel.slideDurationMs, 280);
            equal(root.dock.revealDelayMs, 100);
            equal(root.dock.hideDelayMs, 100);
            equal(root.dock.slideDurationMs, 320);
            equal(root.panel.hideMode, "fullscreen");
            equal(root.panel.barVisible, true);
            equal(root.panel.effectiveReserve, 52);
            equal(root.dock.dockVisible, false);
            equal(root.dock.reserve, 0);
            for (const choice of ["always", "desktop", "fullscreen", "never"]) {
                mode(root.panel, choice);
                for (const presentation of ["clear", "covered"]) {
                    root.panel.presentation = presentation;
                    const hidden = choice === "always" || choice === (presentation === "covered" ? "fullscreen" : "desktop");
                    equal(root.panel.barVisible, !hidden, choice + "/" + presentation);
                    equal(root.panel.edgeEnabled, hidden, choice + " edge/" + presentation);
                    equal(root.panel.effectiveReserve, reserve(choice), choice + " reserve/" + presentation);
                    equal(root.panel.fullscreenInset, inset(choice, presentation === "covered"), choice + " fullscreen inset/" + presentation);
                }
            }
            root.dock.autoHide = false;
            equal(root.dock.dockVisible, true);
            equal(root.dock.reserve, 77);
            root.dock.presentation = "covered";
            equal(root.dock.dockVisible, false);
            equal(root.dock.edgeEnabled, true);
            equal(root.dock.reserve, 77);
        }
        function test_mode_matrix_data() {
            const rows = [];
            for (const choice of ["always", "desktop", "fullscreen", "never"]) {
                for (const environment of ["EMPTY", "NORMAL", "FULLSCREEN"]) {
                    rows.push({
                        tag: choice + "/" + environment,
                        choice: choice,
                        environment: environment
                    });
                }
            }
            return rows;
        }
        function test_mode_matrix(data) {
            mouseMove(scene, 400, 400);
            mode(scene.barPolicy, data.choice);
            service.receive(Quickshell.env("PANEL_DOCK_" + data.environment));
            const fullscreen = data.environment === "FULLSCREEN";
            const hidden = data.choice === "always" || data.choice === (fullscreen ? "fullscreen" : "desktop");
            equal(scene.presentationState, fullscreen ? "covered" : "clear", data.tag + " presentation");
            const status = JSON.parse(scene.status()).bar_policy;
            equal(status.hide_mode, data.choice, data.tag + " status mode");
            equal(status.auto_hide_fullscreen, data.choice === "always" || data.choice === "fullscreen", data.tag + " status fullscreen flag");
            equal(status.visible, !hidden, data.tag + " status visible");
            equal(status.edge_enabled, hidden, data.tag + " status edge");
            equal(scene.barPolicy.hidesHere, hidden, data.tag + " policy hides");
            equal(root.barLayer, fullscreen ? WlrLayer.Overlay : WlrLayer.Top, data.tag + " layer");
            equal(scene.barPolicy.effectiveReserve, reserve(data.choice), data.tag + " reserve");
            equal(status.fullscreen_inset, inset(data.choice, fullscreen), data.tag + " status fullscreen inset");
            equal(root.insetMarkerVisible, inset(data.choice, fullscreen) > 0, data.tag + " inset marker mapped");
            equal(root.insetMarkerHeight, Math.max(1, inset(data.choice, fullscreen)), data.tag + " inset marker height");
        }
        function test_reserve_transitions() {
            for (const choice of ["always", "desktop", "fullscreen", "never"]) {
                mode(root.panel, choice);
                for (const presentation of ["clear", "covered"]) {
                    root.panel.presentation = presentation;
                    const expected = inset(choice, presentation === "covered");
                    equal(root.panel.effectiveReserve, reserve(choice), choice + " presentation reserve");
                    equal(root.panel.fullscreenInset, expected, choice + " presentation inset");
                    root.panel.presentation = "unknown";
                    equal(root.panel.effectiveReserve, reserve(choice), choice + " unknown reserve");
                    equal(root.panel.fullscreenInset, expected, choice + " unknown inset");
                    for (const field of ["revealed", "openUI", "keyboardActive", "dragging", "pointerInside", "overview"]) {
                        root.panel[field] = true;
                        equal(root.panel.effectiveReserve, reserve(choice), choice + " " + field + " reserve");
                        equal(root.panel.fullscreenInset, expected, choice + " " + field + " inset");
                        root.panel[field] = false;
                    }
                }
            }
        }
        function geometrySnapshot(size, position, scrolling, floating) {
            const snapshot = JSON.parse(Quickshell.env("PANEL_DOCK_FULLSCREEN"));
            const window = snapshot.model.windows["8"];
            window.is_floating = floating;
            window.layout.tile_size = size;
            window.layout.tile_pos_in_workspace_view = position;
            window.layout.pos_in_scrolling_layout = scrolling;
            return JSON.stringify(snapshot);
        }
        function test_inset_fullscreen_feedback() {
            for (const choice of ["always", "desktop", "fullscreen", "never"]) {
                mode(scene.barPolicy, choice);
                service.receive(Quickshell.env("PANEL_DOCK_FULLSCREEN"));
                for (let update = 0; update < 4; update++) {
                    const top = scene.barPolicy.fullscreenInset;
                    service.receive(geometrySnapshot([1280, 800 - top], top > 0 ? [73, top] : null, [1, 1], false));
                    equal(scene.presentationState, "covered", choice + " inset fullscreen feedback");
                    equal(scene.barPolicy.fullscreenInset, inset(choice, true), choice + " stable fullscreen inset");
                    equal(scene.barPolicy.effectiveReserve, reserve(choice), choice + " stable reserve");
                }
            }
        }
        function test_inset_fullscreen_geometry() {
            for (const position of [[0, 52], [73, 52], [-80, 52], [0, 51.5]]) {
                service.receive(geometrySnapshot([1280, 748], position, [1, 1], false));
                equal(scene.presentationState, "covered", "inset fullscreen geometry");
            }
            service.receive(geometrySnapshot([1280, 780], [73, 52], [1, 1], false));
            equal(scene.presentationState, "covered", "inset fullscreen pending resize");
            for (const row of [[[1280, 748], null, [1, 1], false], [[1280, 748], [0, 52], null, true], [[1280, 748], [0, 52], [1, 1], true], [[1280, 748], [0, 52], null, false], [[1200, 748], [0, 52], [1, 1], false], [[1280, 700], [0, 52], [1, 1], false], [[1280, 748], [0, 0], [1, 1], false], [[1280, 748], [0, -52], [1, 1], false], [[1280, 748], [0, "52"], [1, 1], false], [[1280, 748], [0, null], [1, 1], false]]) {
                service.receive(geometrySnapshot(row[0], row[1], row[2], row[3]));
                equal(scene.presentationState, "clear", "normal, maximized or floating geometry");
            }
        }
        function test_unknown_after_fullscreen_data() {
            return ["always", "desktop", "fullscreen", "never"].map(choice => ({
                        tag: choice,
                        choice: choice
                    }));
        }
        function test_unknown_after_fullscreen(data) {
            mode(root.panel, data.choice);
            root.panel.presentation = "covered";
            root.panel.presentation = "unknown";
            wait(600);
            const hidden = data.choice === "always" || data.choice === "fullscreen";
            equal(root.panel.barVisible, !hidden, data.choice + " unknown keeps fullscreen preference");
            equal(root.panel.edgeEnabled, hidden, data.choice + " unknown keeps reveal reachable");
            root.panel.edgeHovered = true;
            eventually(root.panel, "barVisible", true, 400);
            root.panel.edgeHovered = false;
            eventually(root.panel, "barVisible", !hidden, 700);
        }
        function test_fullscreen_panel_edge_dwell() {
            root.panel.presentation = "covered";
            equal(root.panel.edgeEnabled, true);
            root.panel.edgeHovered = true;
            wait(60);
            equal(root.panel.barVisible, false, "not before edge dwell");
            state("panel-before-reveal");
            eventually(root.panel, "barVisible", true, 400);
            state("panel-after-reveal");
            root.panel.edgeHovered = false;
            wait(200);
            equal(root.panel.barVisible, true, "hide grace after edge leave");
            state("panel-before-hide");
            eventually(root.panel, "barVisible", false, 700);
            state("panel-after-hide");
        }
        function test_fullscreen_all_modes_reveal_and_leave() {
            for (const choice of ["always", "desktop", "fullscreen", "never"]) {
                mode(root.panel, choice);
                root.panel.presentation = "covered";
                const hidden = choice === "always" || choice === "fullscreen";
                equal(root.panel.barVisible, !hidden, choice + " fullscreen preference");
                root.panel.edgeHovered = true;
                eventually(root.panel, "barVisible", true, 400);
                root.panel.pointerInside = true;
                root.panel.edgeHovered = false;
                wait(550);
                equal(root.panel.barVisible, true, choice + " pointer holds fullscreen reveal");
                root.panel.pointerInside = false;
                eventually(root.panel, "barVisible", !hidden, 700);
                root.panel.presentation = "clear";
                equal(root.panel.barVisible, choice === "fullscreen" || choice === "never", choice + " restores desktop preference");
            }
        }
        function test_pointer_hold_through_pending_window_links() {
            mode(root.panel, "always");
            for (const presentation of ["clear", "covered"]) {
                root.panel.presentation = presentation;
                root.dock.presentation = presentation;
                root.panel.edgeHovered = true;
                root.dock.edgeHovered = true;
                eventually(root.panel, "barVisible", true);
                eventually(root.dock, "dockVisible", true);
                root.panel.pointerInside = true;
                root.dock.pointerInside = true;
                root.panel.edgeHovered = false;
                root.dock.edgeHovered = false;
                root.panel.presentation = "unknown";
                root.dock.presentation = "unknown";
                equal(root.panel.barVisible, true, presentation + " panel survives pending links");
                equal(root.dock.dockVisible, true, presentation + " dock survives pending links");
                wait(550);
                root.panel.presentation = presentation;
                root.dock.presentation = presentation;
                wait(550);
                equal(root.panel.barVisible, true, presentation + " panel stays under pointer");
                equal(root.dock.dockVisible, true, presentation + " dock stays under pointer");
                root.panel.pointerInside = false;
                root.dock.pointerInside = false;
                eventually(root.panel, "barVisible", false, 700);
                eventually(root.dock, "dockVisible", false, 350);
            }
        }
        function test_real_fullscreen_change_resets_reveal() {
            mode(root.panel, "always");
            for (const presentation of ["covered", "clear"]) {
                root.panel.edgeHovered = true;
                root.dock.edgeHovered = true;
                eventually(root.panel, "barVisible", true);
                eventually(root.dock, "dockVisible", true);
                root.panel.pointerInside = true;
                root.dock.pointerInside = true;
                root.panel.edgeHovered = false;
                root.dock.edgeHovered = false;
                root.panel.presentation = "unknown";
                root.dock.presentation = "unknown";
                root.panel.presentation = presentation;
                root.dock.presentation = presentation;
                equal(root.panel.revealed, false, "real fullscreen transition resets panel");
                equal(root.dock.revealed, false, "real fullscreen transition resets dock");
                root.panel.pointerInside = false;
                root.dock.pointerInside = false;
            }
        }
        function test_dock_edge_dwell_and_cancellation() {
            root.dock.edgeHovered = true;
            wait(35);
            equal(root.dock.dockVisible, false);
            root.dock.edgeHovered = false;
            wait(160);
            equal(root.dock.dockVisible, false, "cancelled dwell stays hidden");
            root.dock.edgeHovered = true;
            eventually(root.dock, "dockVisible", true, 350);
            state("dock-after-reveal");
            root.dock.edgeHovered = false;
            wait(35);
            equal(root.dock.dockVisible, true, "dock hide grace");
            eventually(root.dock, "dockVisible", false, 350);
            state("dock-after-hide");
        }
        function test_hold_release_grace() {
            root.panel.presentation = "covered";
            for (const field of ["openUI", "keyboardActive", "dragging"]) {
                root.panel[field] = true;
                equal(root.panel.barVisible, true, field + " reveals panel");
                equal(root.panel.effectiveReserve, 52, "fullscreen interaction retains desktop reserve");
                equal(root.panel.fullscreenInset, 0, "fullscreen interaction sets no inset");
                wait(550);
                equal(root.panel.barVisible, true, field + " holds panel");
                root.panel[field] = false;
                wait(180);
                equal(root.panel.barVisible, true, field + " panel grace");
                eventually(root.panel, "barVisible", false, 650);
            }
            for (const field of ["popupOpen", "keyboardActive", "dragging"]) {
                root.dock[field] = true;
                equal(root.dock.dockVisible, true, field + " reveals dock");
                wait(160);
                equal(root.dock.dockVisible, true, field + " holds dock");
                root.dock[field] = false;
                wait(35);
                equal(root.dock.dockVisible, true, field + " dock grace");
                eventually(root.dock, "dockVisible", false, 350);
            }
        }
        function test_pointer_and_reentry() {
            mode(root.panel, "always");
            root.panel.edgeHovered = true;
            root.dock.edgeHovered = true;
            eventually(root.panel, "barVisible", true);
            eventually(root.dock, "dockVisible", true);
            root.panel.pointerInside = true;
            root.dock.pointerInside = true;
            root.panel.edgeHovered = false;
            root.dock.edgeHovered = false;
            wait(600);
            equal(root.panel.barVisible, true);
            equal(root.dock.dockVisible, true);
            root.panel.pointerInside = false;
            root.dock.pointerInside = false;
            wait(35);
            root.panel.pointerInside = true;
            root.dock.pointerInside = true;
            wait(600);
            equal(root.panel.barVisible, true, "pointer reentry cancels panel hide");
            equal(root.dock.dockVisible, true, "pointer reentry cancels dock hide");
        }
        function test_drag_edge_dwell() {
            root.dock.edgeDragging = true;
            wait(35);
            equal(root.dock.dockVisible, false);
            eventually(root.dock, "dockVisible", true, 350);
            wait(150);
            equal(root.dock.dockVisible, true);
            root.dock.dragging = true;
            root.dock.edgeDragging = false;
            wait(150);
            equal(root.dock.dockVisible, true);
            root.dock.dragging = false;
            wait(35);
            equal(root.dock.dockVisible, true);
            eventually(root.dock, "dockVisible", false, 350);
        }
        function test_overview_unknown_and_settings() {
            mode(root.panel, "always");
            root.panel.edgeHovered = true;
            root.dock.edgeHovered = true;
            wait(35);
            root.panel.presentation = "unknown";
            root.dock.presentation = "unknown";
            wait(200);
            equal(root.panel.edgeEnabled, true);
            equal(root.dock.edgeEnabled, true);
            equal(root.panel.revealed, true);
            equal(root.dock.revealed, true);
            root.panel.presentation = "clear";
            root.dock.presentation = "clear";
            eventually(root.panel, "barVisible", true);
            eventually(root.dock, "dockVisible", true);
            root.panel.overview = true;
            root.dock.overview = true;
            equal(root.panel.normalIslands, false);
            equal(root.panel.workspaceIsland, true);
            equal(root.panel.edgeEnabled, false);
            equal(root.dock.dockVisible, false);
            root.panel.overviewWorkspaces = false;
            equal(root.panel.barVisible, false);
            root.dock.keyboardActive = true;
            equal(root.dock.dockVisible, true);
            root.dock.keyboardActive = false;
            equal(root.dock.dockVisible, false);
            root.panel.overview = false;
            root.dock.overview = false;
            root.panel.edgeHovered = false;
            root.dock.edgeHovered = false;
            mode(root.panel, "never");
            root.dock.autoHide = false;
            wait(200);
            equal(root.panel.barVisible, true);
            equal(root.dock.dockVisible, true);
            mode(root.panel, "always");
            root.dock.autoHide = true;
            equal(root.panel.barVisible, false);
            equal(root.dock.dockVisible, false);
            root.dock.edgeHovered = true;
            wait(35);
            root.dock.on = false;
            wait(150);
            equal(root.dock.dockVisible, false);
            equal(root.dock.revealed, false);
        }
        function shot(name) {
            let saved = false;
            check(window.contentItem.grabToImage(result => {
                saved = result.saveToFile(Quickshell.env("PANEL_DOCK_SHOTS") + "/" + name + ".png");
            }));
            tryVerify(() => saved);
        }
        function frame(label, start) {
            console.log("SLIDE_TRACE " + JSON.stringify({
                label: label,
                elapsedMs: Date.now() - start,
                panelY: scene.bar.y,
                dockAmount: scene.dock.visibleAmount
            }));
        }
        function test_rendered_slide_and_desktop() {
            scene.wallpaper.enabled = false;
            mouseMove(scene, 400, 400);
            service.receive(Quickshell.env("PANEL_DOCK_EMPTY"));
            equal(scene.presentationState, "clear");
            equal(root.barLayer, WlrLayer.Top);
            equal(root.dockLayer, WlrLayer.Top);
            scene.dockStore.pinned = ["fixture-editor", "fixture-files"];
            mode(scene.barPolicy, "never");
            scene.dockStore.autoHide = false;
            wait(600);
            equal(scene.bar.y, 0);
            equal(scene.dock.visibleAmount, 1);
            shot("empty-desktop-visible");
            service.receive(Quickshell.env("PANEL_DOCK_NORMAL"));
            equal(scene.presentationState, "clear");
            equal(scene.barPolicy.barVisible, true);
            equal(scene.dockPolicy.dockVisible, true);
            equal(scene.dockPolicy.reserve, 77);
            mode(scene.barPolicy, "always");
            scene.dockStore.autoHide = true;
            let start = Date.now();
            wait(60);
            frame("hide-middle", start);
            check(scene.bar.y < 0 && scene.bar.y > -60);
            check(scene.dock.visibleAmount > 0 && scene.dock.visibleAmount < 1);
            shot("hide-middle");
            eventually(scene.bar, "y", -60);
            eventually(scene.dock, "visibleAmount", 0);
            frame("hidden", start);
            shot("hidden");
            service.receive(Quickshell.env("PANEL_DOCK_FULLSCREEN"));
            equal(scene.presentationState, "covered");
            equal(root.barLayer, WlrLayer.Overlay);
            equal(root.dockLayer, WlrLayer.Overlay);
            scene.barPolicy.edgeHovered = true;
            scene.dockPolicy.edgeHovered = true;
            start = Date.now();
            wait(190);
            frame("fullscreen-reveal-middle", start);
            check(scene.bar.y < 0 && scene.bar.y > -60);
            check(scene.dock.visibleAmount > 0 && scene.dock.visibleAmount < 1);
            shot("fullscreen-reveal-middle");
            eventually(scene.bar, "y", 0);
            eventually(scene.dock, "visibleAmount", 1);
            frame("fullscreen-revealed", start);
            shot("fullscreen-revealed");
            scene.barPolicy.edgeHovered = false;
            scene.dockPolicy.edgeHovered = false;
            wait(900);
            service._layerFocusSupported = true;
            check(scene.openKeyboard("bar"));
            equal(scene.barPolicy.barVisible, true);
            equal(scene.barPolicy.keyboardActive, true);
            scene.closeAll();
            wait(180);
            equal(scene.barPolicy.barVisible, true, "real keyboard close keeps panel hide grace");
            eventually(scene.barPolicy, "barVisible", false, 650);
            check(scene.openKeyboard("dock"));
            equal(scene.dockPolicy.dockVisible, true);
            equal(scene.dockPolicy.keyboardActive, true);
            scene.closeAll();
            wait(35);
            equal(scene.dockPolicy.dockVisible, true, "real keyboard close keeps dock hide grace");
            eventually(scene.dockPolicy, "dockVisible", false, 350);
        }
    }
}
