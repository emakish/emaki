// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "ManagerLogic.js" as Logic
import "file:@EMAKI_DATADIR@/shell" as Shell

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
    // The widest the row may be (a narrow card or list row sets it): the count goes first, then the average.
    property real maximumWidth: Number.POSITIVE_INFINITY
    // "4.7", "4.6 · 1.2k" on cards, "4.7 · 1,804 ratings" on a program page.
    readonly property string fullText: [showAverage ? Logic.decimal(value, 1) : "",
        showCount ? Shell.Translation.englishPlural(qsTranslate("Stars", "%1 rating(s)", "", count), count).arg(Logic.groupedCount(count))
            : showShortCount && count > 0 ? compact(count) : ""].filter(part => part !== "").join(" · ")
    readonly property string averageText: showAverage ? Logic.decimal(value, 1) : ""
    readonly property real room: maximumWidth - glyphs.implicitWidth - spacing
    visible: value > 0
    spacing: 5
    function compact(number: int): string {
        const formatted = number >= 10000 ? String(Math.round(number / 1000)) : (number / 1000).toFixed(1).replace(/\.0$/, "");
        return number >= 1000 ? qsTranslate("Stars", "%1k").arg(formatted.replace(".", Logic.numericLocale().decimalPoint)) : String(number);
    }
    TextMetrics { id: fullMetrics; font: label.font; text: stars.fullText }
    TextMetrics { id: averageMetrics; font: label.font; text: stars.averageText }
    Text {
        id: glyphs
        objectName: "stars-glyphs"
        // Whole stars only: 4.3 draws four, 4.6 draws five, like the mockup.
        text: "★".repeat(Math.round(stars.value)) + "☆".repeat(5 - Math.round(stars.value))
        color: Shell.ShellPalette.managerWarning
        font.family: "Noto Sans Symbols 2, Noto Sans"
        font.pixelSize: stars.size
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        id: label
        objectName: "stars-text"
        visible: text !== ""
        text: stars.room >= Math.ceil(fullMetrics.advanceWidth) ? stars.fullText
            : stars.room >= Math.ceil(averageMetrics.advanceWidth) ? stars.averageText : ""
        color: Shell.ShellPalette.managerDim
        font.family: "Noto Sans"
        font.pixelSize: stars.size - 1
        anchors.verticalCenter: parent.verticalCenter
    }
}
