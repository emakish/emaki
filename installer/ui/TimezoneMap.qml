pragma ComponentBehavior: Bound
import QtQuick
import "Timezones.js" as Timezones
import "assets/ZoneData.js" as Zones

Item {
    id: map
    required property string timezone
    required property var availableZones
    signal picked(string zone)
    readonly property var point: Zones.data.points[timezone] || null
    function selectAt(x: real, y: real): void {
        const zone = Timezones.at(x / width, y / height);
        if (zone && availableZones.indexOf(zone) >= 0)
            picked(zone);
    }
    Image {
        anchors.fill: parent
        source: "assets/timezone-map.png"
        smooth: true
    }
    Canvas {
        id: highlight
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d");
            ctx.reset();
            const data = Zones.data;
            const selected = data.names.indexOf(map.timezone);
            ctx.fillStyle = "#e2733f";
            if (selected > 0) {
                const sx = width / data.width;
                const sy = height / data.height;
                for (let y = 0; y < data.height; ++y) {
                    const row = data.rows[y];
                    let start = 0;
                    for (let i = 0; i < row.length; i += 2) {
                        if (row[i + 1] === selected)
                            ctx.fillRect(start * sx, y * sy, (row[i] - start) * sx, sy + .4);
                        start = row[i];
                    }
                }
            } else if (map.timezone === "UTC") {
                ctx.fillRect(width / 2 - 1, 0, 2, height);
            }
        }
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
    }
    Connections {
        target: map
        function onTimezoneChanged(): void {
            highlight.requestPaint();
        }
    }
    Rectangle {
        visible: !!map.point
        x: map.point ? (map.point[0] + 180) / 360 * map.width - width / 2 : 0
        y: map.point ? (90 - map.point[1]) / 180 * map.height - height / 2 : 0
        width: 12
        height: 12
        radius: 6
        color: "#703e2e"
        border.color: "#fff8f3"
        border.width: 3
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: mouse => map.selectAt(mouse.x, mouse.y)
    }
}
