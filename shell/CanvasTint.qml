// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

// The Settings canvas tint, the one place every surface takes it from. Over the Regular glass
// it makes the Settings body material. A surface lays it under its content, below its top part
// (the launcher's header, a panel's island row), so the top part keeps the plain glass and the
// contrast between the two. Where the glass shader composites the content (launcher, panels)
// it sits at the bottom of that content layer: the shader blends it exactly as a rectangle
// drawn over the glass. Shown with LiquidPalette.sharedCanvas; the surfaces that already are
// the Settings canvas set `shown: true`.
Rectangle {
    property bool shown: LiquidPalette.sharedCanvas
    visible: shown
    color: LiquidPalette.canvas
}
