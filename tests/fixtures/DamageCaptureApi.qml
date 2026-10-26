// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell.Wayland

ScreencopyView {
    Component.onCompleted: console.info("FORK_DAMAGE " + typeof damageIntersects + " " + typeof frameCaptured + (typeof damageIntersects === "function" ? " " + damageIntersects(Qt.rect(0, 0, 10, 10)) : ""))
}
