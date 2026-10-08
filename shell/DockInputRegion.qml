// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Region {
    id: input
    required property Item edge
    required property bool edgeEnabled
    required property rect plateRect
    required property bool plateEnabled
    required property rect popupRect
    required property bool popupEnabled
    // Disabled rectangles must be empty, not subtracted: the hidden plate's
    // padding overlaps the reveal strip at the bottom of the surface.
    Region {
        item: input.edgeEnabled ? input.edge : null
    }
    Region {
        readonly property rect r: input.plateRect
        x: Math.floor(r.x)
        y: Math.floor(r.y)
        width: input.plateEnabled ? Math.ceil(r.x + r.width) - x : 0
        height: Math.ceil(r.y + r.height) - y
    }
    Region {
        readonly property rect r: input.popupRect
        x: Math.floor(r.x)
        y: Math.floor(r.y)
        width: input.popupEnabled ? Math.ceil(r.x + r.width) - x : 0
        height: Math.ceil(r.y + r.height) - y
    }
}
