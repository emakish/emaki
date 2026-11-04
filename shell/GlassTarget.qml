// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "Keyboard.js" as Keyboard

// Something pressable on the clock panel's glass (docs/mockups/liquid-glass/clock.js): the
// panel's one hover drop flows to it. Its own drawing moves fully above the glass while
// its drop exists, so the growing lens cannot bend its text. Departing targets follow
// coverage while the drop flows away (dual() in clock.js). Children are the drawing.
// `glass` is the ClockPanel, the SystemPanel or the SystemIsland (var: the glass contains its
// targets); without one nothing fades.
Item {
    id: targetObject
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
    // Rests under an orange drop (today, the chosen player, the open page's tab): keyboard
    // focus draws the strong rim there.
    property bool accent: false
    signal clicked
    activeFocusOnTab: activeFocus || (pressable && enabled && visible)
    Keys.onPressed: event => {
        const scope = Keyboard.boundary(targetObject);
        if (scope && scope.keyboardMode !== undefined)
            scope.keyboardMode = true;
        if ([Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space].includes(event.key) && pressable) {
            if (!event.isAutoRepeat)
                clicked();
            event.accepted = true;
        } else {
            Keyboard.handle(Keyboard.boundary(targetObject), event);
        }
    }
    onActiveFocusChanged: if (activeFocus)
        Keyboard.reveal(targetObject)
    FocusRing {
        shown: targetObject.activeFocus && !(targetObject.glass && (targetObject.glass.glassReady || targetObject.glass.drawsKeyboardFocus === true))
        strong: targetObject.accent
        // Under its own drop (the selected one) or on a flat accent track (drop: false).
        strongInk: targetObject.drop ? LiquidPalette.focusOnSelected : LiquidPalette.focusOnAccent
        radius: targetObject.bubbleRadius
    }
    readonly property bool pointerInside: hoverHandler.hovered
    // The glass lifts its current target fully while the drop exists. Departing targets
    // follow Liquid.cover; the remainder stays here, under the glass.
    opacity: glass && glass.onGlassOf ? 1 - glass.onGlassOf(key) : 1
    // Passive: several handlers may watch one point (the owner and the panel see it too).
    HoverHandler {
        id: hoverHandler
        enabled: targetObject.pressable && targetObject.enabled
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: {
            if (!targetObject.glass)
                return;
            if (hovered)
                targetObject.glass.hover(targetObject);
            else if (targetObject.glass.hoverKey === targetObject.key && targetObject.owner && targetObject.owner.pointerInside)
                targetObject.glass.hover(targetObject.owner);
        }
    }
    MouseArea {
        anchors.fill: parent
        enabled: targetObject.pressable && targetObject.enabled
        onPressed: {
            const scope = Keyboard.boundary(targetObject);
            if (scope && scope.keyboardMode !== undefined)
                scope.keyboardMode = false;
        }
        onClicked: targetObject.clicked()
    }
    // A list rebuilt under a resting pointer (a notification arrived): the new delegate with
    // the same key takes the drop over, so it does not melt and grow back.
    Component.onCompleted: {
        if (glass && glass.hoverKey === key)
            glass.hover(targetObject);
    }
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: {
        if (pressable && enabled)
            clicked();
    }
}
