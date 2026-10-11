// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "SudoText.js" as Copy

Column {
    id: choices
    property string way: "time"
    property alias minutesText: minutesInput.text
    property alias turnsText: turnsInput.text
    property bool grantAllowed: false
    property bool turnEligible: false
    readonly property bool minutesValid: /^[0-9]+$/.test(minutesText) && Number(minutesText) >= 5 && Number(minutesText) <= 1440
    readonly property bool turnsValid: /^[0-9]+$/.test(turnsText) && Number(turnsText) >= 1 && Number(turnsText) <= 1000
    readonly property bool amountValid: way === "session" || (way === "time" && minutesValid) || (way === "turns" && turnEligible && turnsValid)
    readonly property string mode: way === "turns" ? "turns:" + Number(turnsText) : way
    readonly property int seconds: way === "time" && minutesValid ? Number(minutesText) * 60 : 0
    readonly property string amountError: way === "time" && !minutesValid ? Copy.keyMinutesError : way === "turns" && !turnsValid ? Copy.keyTurnsError : ""
    onTurnEligibleChanged: if (!turnEligible && way === "turns")
        way = "time"
    spacing: 6
    RadioButton {
        text: "For a while"
        checked: choices.way === "time"
        onClicked: choices.way = "time"
    }
    TextField {
        id: minutesInput
        objectName: "sudo-key-minutes"
        width: parent.width
        visible: choices.way === "time"
        placeholderText: Copy.keyMinutesInput
        inputMethodHints: Qt.ImhDigitsOnly
        maximumLength: 16
    }
    RadioButton {
        text: "The whole session"
        checked: choices.way === "session"
        onClicked: choices.way = "session"
    }
    RadioButton {
        objectName: "sudo-key-turns"
        visible: choices.turnEligible
        text: Copy.keyTurnsChoice
        checked: choices.way === "turns"
        onClicked: choices.way = "turns"
    }
    TextField {
        id: turnsInput
        objectName: "sudo-key-turn-count"
        width: parent.width
        visible: choices.turnEligible && choices.way === "turns"
        placeholderText: Copy.keyTurnsInput
        inputMethodHints: Qt.ImhDigitsOnly
        maximumLength: 16
    }
    Text {
        objectName: "sudo-key-amount-error"
        width: parent.width
        visible: text.length > 0
        text: choices.amountError
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: "#9b1730"
        font.pixelSize: 12
    }
    Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        text: "Super+Ctrl+Esc revokes every sudo key"
        color: "#352522"
        font.pixelSize: 12
    }
    Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        visible: !choices.grantAllowed
        text: Copy.keyWarningRequired
        color: "#352522"
        font.pixelSize: 12
    }
}
