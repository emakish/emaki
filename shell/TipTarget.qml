pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: target
    anchors.fill: parent
    property HoverTip tip
    property string label: ""
    property string shortcut: ""
    // Screen edge the owner sits at; dock items add their surface offset in screen coordinates.
    property string side: "top"
    property point screenOffset: Qt.point(0, 0)
    HoverHandler {
        onHoveredChanged: {
            if (!target.tip)
                return;
            if (hovered && target.enabled && target.label)
                target.tip.offerFrom(target, target.label, target.shortcut, target.side, target.screenOffset);
            else
                target.tip.leave(target);
        }
    }
    // Passive tap observation: existing MouseAreas retain their click actions.
    TapHandler {
        onPressedChanged: {
            if (pressed && target.tip)
                target.tip.hide();
        }
    }
    onEnabledChanged: {
        if (!enabled && tip)
            tip.leave(target);
    }
    onVisibleChanged: {
        if (!visible && tip)
            tip.leave(target);
    }
}
