// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "Keyboard.js" as Keyboard

Item {
    id: target
    anchors.fill: parent
    property string label: ""
    signal activated
    activeFocusOnTab: activeFocus || (enabled && visible)
    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: if (enabled)
        activated()
    Keys.onPressed: event => {
        const scope = Keyboard.boundary(target);
        if (scope && scope.keyboardMode !== undefined)
            scope.keyboardMode = true;
        if ([Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space].includes(event.key)) {
            if (!event.isAutoRepeat)
                activated();
            event.accepted = true;
        } else {
            Keyboard.handle(Keyboard.boundary(target), event);
        }
    }
    onActiveFocusChanged: if (activeFocus)
        Keyboard.reveal(target)
    FocusRing {}
}
