// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

// A private backend substitute: it carries layer policy without a compositor.
Window {
    property real implicitWidth: 1440
    property real implicitHeight: 1000
    width: implicitWidth
    height: implicitHeight
    property real exclusiveZone: 0
    property int exclusionMode: 0
    property var mask
    property alias anchors: edges
    QtObject {
        id: edges
        property bool top: false
        property bool bottom: false
        property bool left: false
        property bool right: false
    }
    property string testNamespace
    property int testLayer: 0
    property int testKeyboardFocus: 0
    property var testBlurRegion
}
