// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "Timezones.js" as Timezones

// A Mercator world map of the time zones, drawn from the generated vector asset
// (assets/ZoneData.js). Zones are coloured by their standard UTC offset; the chosen zone
// takes the accent, the zone under the pointer or keyboard cursor a lighter accent.
// The pointer picks the zone under it (the nearest zone over water); with keyboard
// focus the arrow keys move a cursor (Shift: farther) and Enter or Space picks.
Item {
    id: map
    required property string timezone
    required property var availableZones
    // The chosen zone's fill: the installer's accent; outlines and marks take its ink.
    required property color accent
    property color accentInk: accent
    property bool dark: false
    property string fontFamily: ""
    signal picked(string zone)

    readonly property var geometry: Timezones.geometry()
    readonly property var available: {
        const result = {};
        for (const zone of availableZones)
            result[zone] = true;
        return result;
    }
    // Pixels per map unit; the map keeps its proportions and is centred.
    readonly property real unit: Math.min(width / geometry.width, height / geometry.height)
    readonly property real radius: 12
    // The zone a click or Enter would choose now.
    property string hovered: ""
    // The keyboard cursor in map units, while the map has keyboard focus.
    property var cursor: null
    readonly property var anchor: Timezones.anchor(timezone)
    readonly property string caption: hovered ? Timezones.place(hovered) + " · " + Timezones.offsetLabel(hovered) : ""
    readonly property var colors: dark ? ({
            water: "#1c2227",
            land: ["#57443a", "#6b5446", "#4a3a31", "#7a604d"],
            fraction: "#5c5638",
            border: "#6b5446",
            offsetBorder: "#a3826a",
            coast: "#56646d",
            ink: "#f5e8de",
            plate: "#e6201a17",
            marker: "#fff1e6",
            markerRim: "#201a17"
        }) : ({
            water: "#cad8de",
            land: ["#f2ddc8", "#e7c6a8", "#f6e9d6", "#dcb999"],
            fraction: "#ebe0b4",
            border: "#cdae96",
            offsetBorder: "#9b7357",
            coast: "#8ea2aa",
            ink: "#2a1d17",
            plate: "#e6fbf5ef",
            marker: "#703e2e",
            markerRim: "#fff8f3"
        })

    Accessible.role: Accessible.Graphic
    Accessible.name: qsTranslate("TimezoneMap", "Time zone map")
    Accessible.description: caption
    activeFocusOnTab: true

    function accepts(name: string): bool {
        return available[name] === true;
    }
    function zoneAt(x: real, y: real): string {
        return Timezones.zoneAt((x - frame.x) / unit, (y - frame.y) / unit, name => map.accepts(name));
    }
    // Where a longitude and latitude are, in this item's coordinates.
    function pointAt(lon: real, lat: real): point {
        const p = Timezones.project(lon, lat);
        return Qt.point(frame.x + p[0] * unit, frame.y + p[1] * unit);
    }
    function selectAt(x: real, y: real): void {
        const zone = zoneAt(x, y);
        if (zone)
            picked(zone);
    }
    function zoneColor(offset: int): color {
        return offset % 60 ? colors.fraction : colors.land[((Math.floor(offset / 60) % 4) + 4) % 4];
    }
    function moveCursor(dx: real, dy: real): void {
        const step = geometry.width / 360;
        const start = cursor || anchor || [geometry.width / 2, geometry.height / 2];
        cursor = [Math.max(0, Math.min(geometry.width, start[0] + dx * step)), Math.max(0, Math.min(geometry.height, start[1] + dy * step))];
        hovered = Timezones.zoneAt(cursor[0], cursor[1], name => map.accepts(name));
    }
    function trace(ctx: var, flat: var, closed: bool): void {
        ctx.moveTo(flat[0], flat[1]);
        for (let i = 2; i < flat.length; i += 2)
            ctx.lineTo(flat[i], flat[i + 1]);
        if (closed)
            ctx.closePath();
    }
    function begin(ctx: var): void {
        ctx.reset();
        ctx.beginPath();
        ctx.roundedRect(0, 0, frame.width, frame.height, radius, radius);
        ctx.clip();
        ctx.scale(unit, unit);
        ctx.fillRule = Qt.OddEvenFill;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
    }
    function fillZone(ctx: var, zone: var, fill: color): void {
        ctx.fillStyle = fill;
        ctx.beginPath();
        for (const flat of zone.rings)
            trace(ctx, flat, true);
        ctx.fill();
    }

    Keys.onPressed: event => {
        const step = event.modifiers & Qt.ShiftModifier ? 10 : 2.5;
        if (event.key === Qt.Key_Left)
            map.moveCursor(-step, 0);
        else if (event.key === Qt.Key_Right)
            map.moveCursor(step, 0);
        else if (event.key === Qt.Key_Up)
            map.moveCursor(0, -step);
        else if (event.key === Qt.Key_Down)
            map.moveCursor(0, step);
        else if (event.key === Qt.Key_Home) {
            map.cursor = null;
            map.moveCursor(0, 0);
        } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) && map.hovered)
            map.picked(map.hovered);
        else
            return;
        event.accepted = true;
    }
    onActiveFocusChanged: {
        if (activeFocus && !cursor)
            moveCursor(0, 0);
        else if (!activeFocus) {
            cursor = null;
            hovered = "";
        }
    }
    onHoveredChanged: highlight.requestPaint()
    onTimezoneChanged: highlight.requestPaint()
    onAccentChanged: highlight.requestPaint()
    onAvailableChanged: highlight.requestPaint()
    onColorsChanged: {
        zones.requestPaint();
        water.requestPaint();
        highlight.requestPaint();
    }

    Item {
        id: frame
        objectName: "timezoneMapFrame"
        x: Math.round((map.width - width) / 2)
        y: Math.round((map.height - height) / 2)
        width: Math.round(map.geometry.width * map.unit)
        height: Math.round(map.geometry.height * map.unit)

        // Zones by standard offset, borders inside one offset faint, between offsets stronger.
        Canvas {
            id: zones
            objectName: "timezoneZones"
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d");
                map.begin(ctx);
                ctx.fillStyle = map.colors.water;
                ctx.fillRect(0, 0, map.geometry.width, map.geometry.height);
                for (const zone of map.geometry.paintOrder)
                    map.fillZone(ctx, zone, map.zoneColor(zone.offset));
                for (const kind of [1, 2]) {
                    ctx.beginPath();
                    for (let i = 0; i < map.geometry.arcs.length; ++i)
                        if (map.geometry.kinds[i] === kind)
                            map.trace(ctx, map.geometry.arcs[i], false);
                    ctx.strokeStyle = kind === 2 ? map.colors.offsetBorder : map.colors.border;
                    ctx.lineWidth = (kind === 2 ? .8 : .5) / map.unit;
                    ctx.stroke();
                }
            }
        }
        // The offset band, the hovered zone and the chosen zone, in the accent.
        Canvas {
            id: highlight
            objectName: "timezoneHighlight"
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d");
                map.begin(ctx);
                const geometry = map.geometry;
                const chosen = geometry.byName[map.timezone];
                const hovered = geometry.byName[map.hovered];
                const band = hovered || chosen;
                if (band)
                    for (const zone of geometry.paintOrder)
                        if (zone.offset === band.offset && zone !== chosen && zone !== hovered)
                            map.fillZone(ctx, zone, Qt.alpha(map.accent, .2));
                if (hovered && hovered !== chosen)
                    map.fillZone(ctx, hovered, Qt.alpha(map.accent, .55));
                if (chosen && chosen.rings.length) {
                    map.fillZone(ctx, chosen, map.accent);
                    ctx.strokeStyle = map.accentInk;
                    ctx.lineWidth = 1.2 / map.unit;
                    ctx.stroke();
                }
            }
        }
        // Water over the zones' territorial seas, so coasts are real coasts.
        Canvas {
            id: water
            objectName: "timezoneWater"
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d");
                map.begin(ctx);
                ctx.beginPath();
                ctx.rect(0, 0, map.geometry.width, map.geometry.height);
                for (const flat of map.geometry.land)
                    map.trace(ctx, flat, true);
                ctx.fillStyle = map.colors.water;
                ctx.fill();
                ctx.beginPath();
                for (const flat of map.geometry.land)
                    map.trace(ctx, flat, true);
                ctx.strokeStyle = map.colors.coast;
                ctx.lineWidth = .5 / map.unit;
                ctx.stroke();
            }
        }
        // Islands too small for the land layer.
        Repeater {
            model: map.geometry.dots
            delegate: Rectangle {
                id: dot
                required property var modelData
                readonly property string zone: map.geometry.zones[modelData[0]].name
                x: modelData[1] * map.unit - width / 2
                y: modelData[2] * map.unit - height / 2
                width: 5
                height: 5
                radius: 2.5
                color: zone === map.timezone ? map.accent : zone === map.hovered ? Qt.alpha(map.accent, .7) : map.zoneColor(map.geometry.zones[modelData[0]].offset)
                border.width: 1
                border.color: zone === map.timezone ? map.accentInk : map.colors.coast
            }
        }
        Rectangle {
            objectName: "timezoneMarker"
            visible: !!map.anchor
            x: map.anchor ? map.anchor[0] * map.unit - width / 2 : 0
            y: map.anchor ? map.anchor[1] * map.unit - height / 2 : 0
            width: 12
            height: 12
            radius: 6
            color: map.colors.marker
            border.color: map.colors.markerRim
            border.width: 3
        }
        Rectangle {
            objectName: "timezoneCursor"
            visible: map.activeFocus && !!map.cursor
            x: map.cursor ? map.cursor[0] * map.unit - width / 2 : 0
            y: map.cursor ? map.cursor[1] * map.unit - height / 2 : 0
            width: 18
            height: 18
            radius: 9
            color: "transparent"
            border.color: map.accentInk
            border.width: 2
        }
        Rectangle {
            objectName: "timezoneCaption"
            visible: !!map.caption
            x: 8
            y: parent.height - height - 8
            width: Math.min(parent.width - 16, captionText.implicitWidth + 20)
            height: captionText.implicitHeight + 8
            radius: height / 2
            color: map.colors.plate
            Text {
                id: captionText
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                verticalAlignment: Text.AlignVCenter
                text: map.caption
                color: map.colors.ink
                elide: Text.ElideRight
                font.family: map.fontFamily
                font.pixelSize: 14
                textFormat: Text.PlainText
            }
        }
        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            visible: map.activeFocus
            radius: map.radius + 3
            color: "transparent"
            border.color: map.accentInk
            border.width: 2
        }
    }
    MouseArea {
        id: pointer
        // Where the pointer was in the window: when the page scrolls the map under a resting
        // pointer, Qt reports a move, which must not take over from the keyboard cursor.
        property point last: Qt.point(-1, -1)
        anchors.fill: frame
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onPositionChanged: mouse => {
            const scene = mapToItem(null, mouse.x, mouse.y);
            if (map.cursor && scene.x === last.x && scene.y === last.y)
                return;
            last = scene;
            map.cursor = null;
            map.hovered = map.zoneAt(frame.x + mouse.x, frame.y + mouse.y);
        }
        onExited: if (!map.cursor)
            map.hovered = ""
        onClicked: mouse => map.selectAt(frame.x + mouse.x, frame.y + mouse.y)
    }
}
