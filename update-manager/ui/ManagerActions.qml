// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Layouts

// A dialog's buttons at its right edge, in one row; when the window is too narrow for the
// row, they wrap under each other from the left.
Flow {
    id: actions
    readonly property real rowWidth: {
        let total = 0, count = 0;
        for (const child of children) if (child.visible) { total += child.implicitWidth; count++; }
        return total + Math.max(0, count - 1) * spacing;
    }
    Layout.fillWidth: true
    spacing: 10
    leftPadding: rowWidth <= width ? width - rowWidth : 0
    // A button wider than the whole row shortens its label.
    function fit(): void {
        for (const child of children) {
            child.width = Qt.binding(() => Math.min(child.implicitWidth, actions.width));
            if (child.squeezed !== undefined) child.squeezed = Qt.binding(() => actions.width > 0 && child.implicitWidth > actions.width);
        }
    }
    Component.onCompleted: fit()
    onChildrenChanged: fit()
}
