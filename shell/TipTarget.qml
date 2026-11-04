// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: targetObject
    anchors.fill: parent
    property HoverTip tip
    property string label: ""
    property string shortcut: ""
    // Screen edge the owner sits at; dock items add their surface offset in screen coordinates.
    property string side: "top"
    property point screenOffset: Qt.point(0, 0)
    HoverHandler {
        onHoveredChanged: {
            if (!targetObject.tip)
                return;
            if (hovered && targetObject.enabled && targetObject.label)
                targetObject.tip.offerFrom(targetObject, targetObject.label, targetObject.shortcut, targetObject.side, targetObject.screenOffset);
            else
                targetObject.tip.leave(targetObject);
        }
    }
    // Passive tap observation: existing MouseAreas retain their click actions.
    TapHandler {
        onPressedChanged: {
            if (pressed && targetObject.tip)
                targetObject.tip.hide();
        }
    }
    onEnabledChanged: {
        if (!enabled && tip)
            tip.leave(targetObject);
    }
    onVisibleChanged: {
        if (!visible && tip)
            tip.leave(targetObject);
    }
}
