// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"
import "SudoText.js" as Copy
import "PolkitDetails.js" as Details

Column {
    id: indicator
    property var backend: SudoKeySupport.backend
    property bool installed: SudoKeySupport.installed
    readonly property var keys: installed && backend && backend.connected ? backend.keys : []
    visible: keys.length > 0
    spacing: 4
    Repeater {
        model: indicator.keys
        SettingsButton {
            required property var modelData
            readonly property string requester: Details.clean(modelData.label || Copy.requesterUnavailable)
            objectName: "sudo-key-revoke-" + modelData.id
            width: indicator.width
            text: "⚿ " + requester + " · " + (modelData.kind === "turns" ? Copy.keyTurnsRemaining(modelData.remainingTurns) : modelData.session ? Copy.keySessionRemaining : Math.max(0, Math.ceil(modelData.remainingSeconds / 60)) + " min")
            Accessible.name: Copy.keyRevoke + ": " + requester
            onClicked: indicator.backend.revoke(modelData.id)
        }
    }
}
