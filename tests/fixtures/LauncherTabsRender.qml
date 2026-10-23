// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
import Emaki.I18n

Rectangle {
    id: root
    width: bodyWidth + 40
    height: 880
    color: dark ? "#29262c" : "#fffaf8"
    property bool dark: false
    property string mode: "Commands"
    property int bodyWidth: 720
    property bool glass: false
    property string scenario: "reveal"
    property real wheelBefore: 0
    property real wheelDelta: 0
    TextMetrics {
        id: inkMetrics
    }
    TestCase {
        id: input
        when: false
    }
    property var audit: ({})
    property bool ready: false
    Rectangle {
        x: 0
        y: 770
        width: root.width
        height: 110
        color: "black"
        Rectangle {
            id: underProbe
            x: 20
            y: 10
            color: "white"
            layer.enabled: true
        }
        Rectangle {
            id: overProbe
            x: 20
            y: 60
            color: "white"
            layer.enabled: true
        }
    }
    property int stage: 0
    readonly property var renderStats: audit
    NiriService {
        id: niri
        binary: ""
    }
    QtObject {
        id: system
        property QtObject night: QtObject {
            property bool on: false
        }
    }
    DockBackdrop {
        id: backdrop
        mode: "wallpaper"
        wallpaperTexture: Quickshell.env("EMAKI_FIXTURE_WALLPAPER")
        screenWidth: root.width
        screenHeight: root.height
        dpr: Screen.devicePixelRatio || 1
    }
    LauncherBody {
        id: launcher
        x: 20
        y: 20
        width: root.bodyWidth
        height: 720
        origin: Qt.point(x, y)
        backdrop: root.glass ? backdrop : null
        expansion: 1
        opened: true
        niri: niri
        commandSystem: system
    }
    function box(item) {
        const p = item.mapToItem(launcher, 0, 0);
        return {
            x: p.x,
            y: p.y,
            width: item.width,
            height: item.height
        };
    }
    function intersection(a, b) {
        const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
        return {
            x: x,
            y: y,
            width: Math.max(0, Math.min(a.x + a.width, b.x + b.width) - x),
            height: Math.max(0, Math.min(a.y + a.height, b.y + b.height) - y)
        };
    }
    function inspect(item, clip, result, hiddenSources, plate) {
        if (!item.visible || item.opacity <= .001)
            return;
        const bounds = box(item);
        // A hidden texture source is painted only through the glass plate's mask.
        // Other branches, including the on-glass ink and focus, keep their own clips.
        if (hiddenSources.includes(item))
            clip = intersection(clip, plate);
        if (item.clip)
            clip = intersection(clip, bounds);
        if (typeof item.text === "string" && item.text.length) {
            const ink = {
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height
            };
            if (!item.clip && item.elide === Text.ElideNone) {
                if (item.contentWidth > bounds.width) {
                    ink.width = item.contentWidth;
                    if (item.horizontalAlignment === Text.AlignHCenter)
                        ink.x -= (ink.width - bounds.width) / 2;
                    else if (item.horizontalAlignment === Text.AlignRight)
                        ink.x -= ink.width - bounds.width;
                }
                if (item.contentHeight > bounds.height) {
                    ink.height = item.contentHeight;
                    if (item.verticalAlignment === Text.AlignVCenter)
                        ink.y -= (ink.height - bounds.height) / 2;
                }
            }
            result.push({
                text: item.text,
                objectName: item.objectName,
                bounds: bounds,
                clip: clip,
                painted: intersection(ink, clip),
                contentWidth: item.contentWidth,
                truncated: item.truncated === true
            });
        }
        for (const child of item.children || [])
            inspect(child, clip, result, hiddenSources, plate);
    }
    function find(item, name) {
        if (item.objectName === name)
            return item;
        for (const child of item.children || []) {
            const match = find(child, name);
            if (match)
                return match;
        }
        return null;
    }
    function snapshot() {
        const tabs = [];
        for (const name of launcher.modes) {
            const item = find(launcher, "launcher-tab-" + name.toLowerCase());
            if (!item)
                continue;
            const labels = [];
            // Read hidden original text too: the selected label is drawn again on the glass.
            for (const child of item.children || [])
                if (typeof child.text === "string" && child.text.length) {
                    inkMetrics.font = child.font;
                    inkMetrics.text = child.text;
                    const bounds = box(child);
                    const tight = inkMetrics.tightBoundingRect;
                    labels.push({
                        ink: {
                            x: bounds.x + tight.x,
                            y: bounds.y + child.baselineOffset + tight.y,
                            width: tight.width,
                            height: tight.height
                        },
                        text: child.text,
                        bounds: box(child),
                        contentWidth: child.contentWidth,
                        truncated: child.truncated === true
                    });
                }
            tabs.push({
                name: name,
                bounds: box(item),
                labels: labels
            });
        }
        const strip = find(launcher, "launcher-mode-strip");
        const search = find(launcher, "launcher-search");
        const texts = [];
        const hiddenSources = [];
        for (const child of launcher.children || [])
            if (child.sourceItem && child.hideSource === true)
                hiddenSources.push(child.sourceItem);
        inspect(launcher, {
            x: -10000,
            y: -10000,
            width: 20000,
            height: 20000
        }, texts, hiddenSources, launcher.glassStatus().plate);
        const inkLayer = find(launcher, "launcher-mode-ink").parent;
        root.audit = {
            underEffect: strip.layer.effect !== null,
            overEffect: inkLayer.layer.effect !== null,
            underLayer: strip.layer.enabled,
            overLayer: inkLayer.layer.enabled,
            maskLayer: find(launcher, "launcher-mode-fade-mask").layer.enabled,
            scenario: root.scenario,
            wheelBefore: root.wheelBefore,
            wheelDelta: root.wheelDelta,
            drops: launcher.glassStatus().drops,
            dark: ShellPalette.dark,
            glassReady: launcher.glassReady,
            mode: launcher.modes[launcher.modeIndex],
            labels: launcher.modeLabels,
            body: {
                x: 0,
                y: 0,
                width: launcher.width,
                height: launcher.height
            },
            strip: strip ? box(strip) : null,
            contentX: strip ? strip.contentX : 0,
            contentWidth: strip ? strip.contentWidth : 0,
            fadeWidth: strip ? strip.fadeWidth : 0,
            fadeLeft: strip ? strip.fadeLeft : false,
            fadeRight: strip ? strip.fadeRight : false,
            tabs: tabs,
            search: box(search),
            modeRect: {
                x: launcher.modeRect.x,
                y: launcher.modeRect.y,
                width: launcher.modeRect.width,
                height: launcher.modeRect.height
            },
            bubble: launcher.glassStatus().mode,
            texts: texts
        };
    }
    Component.onCompleted: {
        SettingsBridge.values = [
            {
                key: "appearance.theme",
                value: dark ? "dark" : "light"
            }
        ];
        launcher.setMode("Apps");
    }
    // Chain transitions so slow first shader compilation cannot reorder independent timers.
    Timer {
        interval: 650
        running: true
        repeat: true
        onTriggered: {
            if (root.stage === 0)
                launcher.setMode("Emoji");
            else if (root.stage === 1)
                launcher.setMode(root.mode);
            else {
                if (root.stage === 4 && root.scenario !== "reveal") {
                    const strip = find(launcher, "launcher-mode-strip");
                    const bubble = launcher.glassStatus().mode;
                    const bounds = box(strip);
                    const target = root.scenario === "wheel-left" ? strip.contentX + bubble.x - bounds.x - strip.fadeWidth / 8 : strip.contentX + bubble.x + bubble.width - bounds.x - bounds.width + strip.fadeWidth / 8;
                    root.wheelBefore = strip.contentX;
                    root.wheelDelta = Math.round(strip.contentX - Math.max(0, Math.min(strip.contentWidth - strip.width, target)));
                    input.mouseWheel(strip, strip.width / 2, strip.height / 2, 0, root.wheelDelta);
                }
                root.snapshot();
                if (root.stage >= 4 && launcher.modes[launcher.modeIndex] === root.mode && (!root.glass || launcher.glassReady)) {
                    const strip = find(launcher, "launcher-mode-strip");
                    underProbe.width = overProbe.width = strip.width;
                    underProbe.height = overProbe.height = strip.height;
                    underProbe.layer.enabled = strip.layer.enabled;
                    overProbe.layer.enabled = find(launcher, "launcher-mode-ink").parent.layer.enabled;
                    underProbe.layer.effect = strip.layer.effect;
                    overProbe.layer.effect = find(launcher, "launcher-mode-ink").parent.layer.effect;
                    if (root.stage >= 5)
                        root.ready = true;
                }
            }
            root.stage++;
        }
    }
}
