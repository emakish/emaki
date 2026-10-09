// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "Keyboard.js" as Keyboard

// Service refreshes can replace a focused delegate. Keep its logical key, then
// resolve the new control after the replacement has finished in this event turn.
Item {
    id: keeper
    required property Item scope
    property string rememberedKey: ""
    visible: false
    onEnabledChanged: if (!enabled)
        rememberedKey = ""
    function track(item: var): void {
        if (!enabled)
            return;
        if (item && item.activeFocusOnTab) {
            rememberedKey = Keyboard.boundary(item) === scope && typeof item.key === "string" ? item.key : "";
        } else if (rememberedKey) {
            Qt.callLater(restore);
        }
    }
    function restore(): void {
        if (!enabled || !scope.Window.window?.active || !rememberedKey)
            return;
        const current = scope.Window.window.activeFocusItem;
        // Never move focus away from another control or a newly opened surface.
        if (current && (current.activeFocusOnTab || (Keyboard.boundary(current) && Keyboard.boundary(current) !== scope)))
            return;
        const replacement = Keyboard.targets(scope).find(item => item.key === rememberedKey);
        if (replacement) {
            replacement.forceActiveFocus(Qt.TabFocusReason);
            Keyboard.reveal(replacement);
        } else {
            Keyboard.focusFirst(scope);
        }
    }
    Connections {
        target: keeper.scope.Window.window
        function onActiveFocusItemChanged(): void {
            keeper.track(keeper.scope.Window.window.activeFocusItem);
        }
    }
}
