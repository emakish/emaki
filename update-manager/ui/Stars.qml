// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

// A row of five stars for a 0–5 rating, with the average and count beside it.
// Cards pass the engine's {average, count}; a missing rating draws nothing.
Row {
    id: stars
    property var rating: null
    property real value: rating ? Number(rating.average) || 0 : 0
    property int count: rating ? Number(rating.count) || 0 : 0
    property bool showAverage: true
    property bool showCount: false
    property bool showShortCount: false
    property int size: 13
    visible: value > 0
    spacing: 5
    function compact(number: int): string {
        return number >= 10000 ? Math.round(number / 1000) + "k" : number >= 1000 ? (number / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(number);
    }
    Text {
        objectName: "stars-glyphs"
        // Whole stars only: 4.3 draws four, 4.6 draws five, like the mockup.
        text: "★".repeat(Math.round(stars.value)) + "☆".repeat(5 - Math.round(stars.value))
        color: "#d9962c"
        font.family: "Noto Sans Symbols 2, Noto Sans"
        font.pixelSize: stars.size
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        objectName: "stars-text"
        visible: text !== ""
        // "4.7", "4.6 · 1.2k" on cards, "4.7 · 1,804 ratings" on a program page.
        text: [stars.showAverage ? stars.value.toFixed(1) : "",
               stars.showCount ? stars.count.toLocaleString(Qt.locale("en_US"), "f", 0) + (stars.count === 1 ? " rating" : " ratings")
                   : stars.showShortCount && stars.count > 0 ? stars.compact(stars.count) : ""].filter(part => part !== "").join(" · ")
        color: "#866c70"
        font.family: "Noto Sans"
        font.pixelSize: stars.size - 1
        anchors.verticalCenter: parent.verticalCenter
    }
}
