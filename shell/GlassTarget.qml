pragma ComponentBehavior: Bound
import QtQuick

// Something pressable on the clock panel's glass (docs/mockups/liquid-glass/clock.js): the
// panel's one hover drop flows to it, and while a drop covers it the target's own drawing fades
// under the glass and the panel draws it once more on the glass (dual() in clock.js; since
// 27.09 also what a drop leaves or flows over, as much as it covers it), so the drop's edge
// never bends the text it lies on or is flowing off. Children are the drawing.
// `glass` is the ClockPanel, the SystemPanel or the SystemIsland (var: the glass contains its
// targets); without one nothing fades.
Item {
    id: target
    property var glass: null
    // Unique within the panel; the resting drops (today, the active player) find it by key.
    required property string key
    // The drop around it: the target's rect grown by bubblePad, with this corner radius.
    property real bubblePad: 2
    property real bubbleRadius: 11
    // A target the pointer may rest on without a drop of its own (the DND toggle: its knob
    // already is one).
    property bool drop: true
    property bool pressable: true
    // A target inside another (a row's close button): it wins the hover while the pointer is
    // on it and hands it back to its owner when the pointer leaves it but not the owner.
    property var owner: null
    property string label: ""
    signal clicked
    readonly property bool pointerInside: hoverHandler.hovered
    // The glass says how much of the target lies on it (onGlassOf: the alpha of the drop that
    // goes to or rests on it, or as much as a drop covers it, Liquid.cover); the rest of it is
    // here, under the glass.
    opacity: glass && glass.onGlassOf ? 1 - glass.onGlassOf(key) : 1
    // Passive: several handlers may watch one point (the owner and the panel see it too).
    HoverHandler {
        id: hoverHandler
        enabled: target.pressable && target.enabled
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: {
            if (!target.glass)
                return;
            if (hovered)
                target.glass.hover(target);
            else if (target.glass.hoverKey === target.key && target.owner && target.owner.pointerInside)
                target.glass.hover(target.owner);
        }
    }
    MouseArea {
        anchors.fill: parent
        enabled: target.pressable && target.enabled
        onClicked: target.clicked()
    }
    // A list rebuilt under a resting pointer (a notification arrived): the new delegate with
    // the same key takes the drop over, so it does not melt and grow back.
    Component.onCompleted: {
        if (glass && glass.hoverKey === key)
            glass.hover(target);
    }
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: {
        if (pressable && enabled)
            clicked();
    }
}
