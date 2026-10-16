// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//@ pragma AppId emaki-install-map-test
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../Timezones.js" as Timezones
import ".." as UI

// The time zone map on its own: pixels of land, water, the chosen and the hovered zone in
// the light and the dark palette, pointer and keyboard picking, and paint and hit-test times.
ShellRoot {
    id: test
    readonly property bool dark: Quickshell.env("EMAKI_MAP_THEME") === "dark"
    readonly property string screenshot: Quickshell.env("EMAKI_MAP_SHOT")
    readonly property real budget: Number(Quickshell.env("EMAKI_MAP_PAINT_BUDGET_MS") || 1500)
    property var picks: []
    function check(ok: bool, message: string): void {
        if (!ok) {
            console.error("ASSERTION_FAILED " + message);
            Qt.quit();
            throw new Error(message);
        }
    }
    function find(item: Item, name: string): Item {
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const found = find(child, name);
            if (found)
                return found;
        }
        return null;
    }
    // Item coordinates of a longitude and latitude on the map.
    function at(lon: real, lat: real): point {
        return map.pointAt(lon, lat);
    }
    function near(a: color, b: color, tolerance: real): bool {
        return Math.abs(a.r - b.r) <= tolerance && Math.abs(a.g - b.g) <= tolerance && Math.abs(a.b - b.b) <= tolerance;
    }
    // The map's rendered colour at an item position. QtTest grabs are whole only at
    // scale 1; at every scale map.py checks the SAMPLE points on the saved picture.
    function pixel(where: point): color {
        return input.grabImage(map).pixel(Math.round(where.x), Math.round(where.y));
    }
    function sample(name: string, where: point, expected: color): void {
        const p = map.mapToItem(shot, where.x, where.y);
        console.log("SAMPLE " + name + " " + p.x + " " + p.y + " " + expected);
    }
    // Milliseconds until the named canvases have painted after `change` runs.
    function paintTime(names: var, change: var): real {
        const canvases = names.map(name => find(map, name) as Canvas);
        const done = canvases.map(() => false);
        const handlers = canvases.map((canvas, index) => () => done[index] = true);
        canvases.forEach((canvas, index) => canvas.painted.connect(handlers[index]));
        const start = Date.now();
        change();
        while (done.indexOf(false) >= 0 && Date.now() - start < 5000)
            input.wait(2);
        const elapsed = Date.now() - start;
        canvases.forEach((canvas, index) => canvas.painted.disconnect(handlers[index]));
        check(done.indexOf(false) < 0, names + " painted");
        return elapsed;
    }
    function run(): void {
        const scale = Quickshell.env("QT_SCALE_FACTOR") || "1";
        // A full repaint (resize, theme change) and a hover repaint.
        const layers = ["timezoneZones", "timezoneWater", "timezoneHighlight"];
        const full = paintTime(layers, () => layers.forEach(name => (find(map, name) as Canvas).requestPaint()));
        console.log("PERF full repaint " + full + " ms at scale " + scale);
        check(full < budget, "the whole map repaints within " + budget + " ms (" + full + ")");
        const hover = paintTime(["timezoneHighlight"], () => map.hovered = "Asia/Kolkata");
        console.log("PERF hover repaint " + hover + " ms at scale " + scale);
        check(hover < budget / 5, "a hover repaints within " + budget / 5 + " ms (" + hover + ")");
        map.hovered = "";
        // Hit testing per pointer move.
        const t0 = Date.now();
        let hits = 0;
        for (let i = 0; i < 400; ++i) {
            const x = (i * 37 % 400) / 400 * map.geometry.width, y = (i * 53 % 400) / 400 * map.geometry.height;
            if (Timezones.zoneAt(x, y, null))
                ++hits;
        }
        const perMove = (Date.now() - t0) / 400;
        console.log("PERF hit test " + perMove.toFixed(2) + " ms per pointer move (" + hits + "/400 found)");
        check(hits === 400 && perMove < 8, "hit testing keeps up with the pointer (" + perMove + " ms)");
        input.wait(100);

        const whole = scale === "1";
        if (whole) {
            const water = pixel(at(-140, -40));
            const land = pixel(at(-55, -12));
            check(near(water, map.colors.water, .02), "open water has the water colour (" + water + ")");
            check(near(land, map.zoneColor(-240), .02), "inland Brazil (UTC−4) has its offset colour (" + land + ")");
            check(!near(water, land, .05), "land and water are told apart");
            // The chosen zone, set from outside as the automatic guess does, is in the accent.
            const chosen = pixel(at(122, -25));
            check(near(chosen, map.accent, .02), "the chosen zone is filled with the accent (" + chosen + ")");
        }
        const marker = find(map, "timezoneMarker");
        const perth = at(115.86, -31.95);
        check(marker.visible && Math.abs(marker.x + marker.width / 2 + find(map, "timezoneMapFrame").x - perth.x) < 2, "the marker sits on the chosen city");
        // Hovering India names it and tints it with a lighter accent.
        const india = at(78, 22);
        input.mouseMove(map, india.x, india.y);
        check(map.hovered === "Asia/Kolkata", "hover finds India (" + map.hovered + ")");
        check(map.caption === "Kolkata, India · UTC+5:30", "hover caption: " + map.caption);
        check(find(map, "timezoneCaption").visible, "the caption is shown");
        input.wait(60);
        if (whole) {
            const tinted = pixel(india);
            check(!near(tinted, map.zoneColor(330), .03) && !near(tinted, map.accent, .03), "the hovered zone is tinted, not filled (" + tinted + ")");
        }
        // A click picks the zone under the pointer; over water or a zone the system lacks,
        // the nearest offered zone.
        input.mouseClick(map, india.x, india.y);
        const ocean = at(-35, 41);
        input.mouseClick(map, ocean.x, ocean.y);
        const tokyo = at(139.69, 35.69);
        input.mouseClick(map, tokyo.x, tokyo.y);
        check(test.picks.length === 3 && test.picks[0] === "Asia/Kolkata" && test.picks[1] === "Atlantic/Azores" && map.availableZones.indexOf(test.picks[2]) >= 0, "pointer picks: " + JSON.stringify(test.picks));
        input.mouseMove(map, -5, -5);
        // Keyboard: focus puts the cursor on the chosen zone; arrows move it, Enter picks.
        map.forceActiveFocus();
        check(map.cursor !== null && find(map, "timezoneCursor").visible && map.hovered === "Australia/Perth", "keyboard focus starts on the chosen zone (" + map.hovered + ")");
        for (let i = 0; i < 4; ++i)
            input.keyClick(Qt.Key_Left, Qt.ShiftModifier);
        for (let i = 0; i < 5; ++i)
            input.keyClick(Qt.Key_Up, Qt.ShiftModifier);
        input.keyClick(Qt.Key_Up);
        input.keyClick(Qt.Key_Up);
        check(map.hovered === "Asia/Kolkata", "arrows move the cursor to India (" + map.hovered + ")");
        input.keyClick(Qt.Key_Return);
        check(test.picks[3] === "Asia/Kolkata", "Enter picks the zone under the cursor");
        input.keyClick(Qt.Key_Home);
        check(map.hovered === "Australia/Perth", "Home returns the cursor to the chosen zone");
        // The picture: India chosen, the pointer over Nepal.
        map.timezone = "Asia/Kolkata";
        const kathmandu = at(85.32, 27.72);
        input.mouseMove(map, kathmandu.x, kathmandu.y);
        check(map.hovered === "Asia/Kathmandu" && map.caption === "Kathmandu, Nepal · UTC+5:45", "Nepal hover: " + map.caption);
        input.wait(100);
        sample("water", at(-140, -40), map.colors.water);
        sample("land", at(-55, -12), map.zoneColor(-240));
        sample("chosen", india, map.accent);
        shot.grabToImage(result => {
            check(!test.screenshot || result.saveToFile(test.screenshot), "screenshot saved");
            console.log("MAP_OK " + (test.dark ? "dark" : "light") + " scale " + scale);
            Qt.quit();
        });
    }

    Window {
        id: window
        visible: true
        width: 900
        height: 520
        color: test.dark ? "#1a1311" : "#fff8f3"
        Rectangle {
            id: shot
            color: window.color
            anchors.fill: parent
            UI.TimezoneMap {
                id: map
                anchors.fill: parent
                anchors.margins: 10
                dark: test.dark
                timezone: "Australia/Perth"
                availableZones: ["UTC", "Australia/Perth", "Asia/Kolkata", "Asia/Kathmandu", "Europe/Berlin", "America/New_York", "America/Sao_Paulo", "Pacific/Chatham", "Atlantic/Azores"]
                accent: "#e2733f"
                accentInk: test.dark ? "#f4a07a" : "#a8481f"
                fontFamily: "sans-serif"
                onPicked: zone => test.picks = test.picks.concat([zone])
            }
        }
        TestCase {
            id: input
            name: "TimezoneMap"
            when: false
        }
    }
    Timer {
        interval: 400
        running: true
        onTriggered: test.run()
    }
}
