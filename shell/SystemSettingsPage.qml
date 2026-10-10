// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

// Existing session controls share their service with the quick panel. This page
// never owns discovery, scanning or microphone monitoring.
Column {
    id: root
    required property SystemService service
    required property NiriService niri
    property string page: ""
    readonly property var availablePages: ["wifi", "bluetooth", "sound", "displays", "battery", "keyboard"]
    readonly property var backend: service.backend
    readonly property bool pending: service.actionState === "pending" || service.actionState === "busy"
    readonly property bool audioReady: !!(backend?.audioReady && service.sinkAudio)
    readonly property var connectedNetworks: Array.from(backend?.networks ?? []).filter(network => network.connected)
    property string actionKind: ""
    property string actionResult: "idle"
    property bool awaitingAction: false
    property int actionSerial: -1
    function request(kind: string, value: var): void {
        if (pending)
            return;
        actionKind = kind;
        awaitingAction = false;
        service.act(kind, value);
        actionSerial = service.actionSerial;
        actionResult = service.actionState;
        awaitingAction = ["pending", "busy"].includes(actionResult);
    }
    onPageChanged: {
        awaitingAction = false;
        actionKind = "";
        actionResult = "idle";
    }
    Connections {
        target: root.service
        function onActionStateChanged(): void {
            if (!root.awaitingAction)
                return;
            if (root.service.actionSerial !== root.actionSerial || (root.service.pendingKind && root.service.pendingKind !== root.actionKind)) {
                root.awaitingAction = false;
                root.actionResult = "idle";
                return;
            }
            root.actionResult = root.service.actionState;
            root.awaitingAction = ["pending", "busy"].includes(root.actionResult);
        }
    }
    spacing: 16

    Text {
        width: parent.width
        visible: !["idle", "confirmed", "applied", "ready"].includes(root.actionResult) && root.page !== "keyboard"
        text: root.awaitingAction ? "Working…" : root.actionResult === "requested" ? "Change requested." : "Could not confirm the change. The current value is shown below."
        textFormat: Text.PlainText
        color: root.pending ? SettingsTheme.dim : "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }

    SettingsCard {
        width: parent.width
        visible: root.page === "wifi"
        title: "Wireless connection"
        SettingsToggleRow {
            objectName: "settings-wifi-power"
            width: parent.width
            title: "Wi-Fi"
            explanation: "Turn the wireless radio on or off."
            value: root.backend?.wifiEnabled ?? false
            defaultValue: value
            unavailable: !root.backend?.networkReady || !root.backend?.wifiHardwareEnabled || !root.backend?.wifiDevices.length
            busy: root.pending
            onValueRequested: root.request("wifi-power", null)
        }
        SettingsRow {
            width: parent.width
            title: "Connection"
            explanation: !root.backend?.networkReady ? "Network status is unavailable." : !root.backend.wifiHardwareEnabled ? "The wireless radio is blocked by the system." : root.connectedNetworks.length ? "Connected to " + root.connectedNetworks.map(network => network.name || network.ssid || "Wireless network").join(", ") + "." : "No wireless connection is active."
            managed: true
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "bluetooth"
        title: "Bluetooth devices"
        SettingsToggleRow {
            objectName: "settings-bluetooth-power"
            width: parent.width
            title: "Bluetooth"
            explanation: "Turn the Bluetooth adapter on or off."
            value: root.backend?.adapter?.enabled ?? false
            defaultValue: value
            unavailable: !root.backend?.adapter
            busy: root.pending
            onValueRequested: root.request("bt-power", null)
        }
        SettingsRow {
            width: parent.width
            title: "Connected devices"
            explanation: {
                const devices = Array.from(root.backend?.devices ?? []).filter(device => device.connected);
                return devices.length ? devices.map(device => device.name || "Bluetooth device").join(", ") : "No Bluetooth devices are connected.";
            }
            managed: true
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "sound"
        title: "Output"
        SettingsToggleRow {
            objectName: "settings-sound-mute"
            width: parent.width
            title: "Mute"
            explanation: "Silence the current sound output."
            value: root.service.sinkMuted
            defaultValue: value
            unavailable: !root.audioReady
            busy: root.pending
            onValueRequested: root.request("mute", null)
        }
        SettingsSliderRow {
            objectName: "settings-sound-volume"
            width: parent.width
            title: "Volume"
            explanation: root.audioReady ? Math.round(root.service.sinkVolume * 100) + "% of the current output volume." : "No sound output is available."
            value: Math.max(0, Math.round(root.service.sinkVolume * 100))
            defaultValue: value
            unavailable: !root.audioReady
            busy: root.pending
            onValueRequested: value => root.request("volume", Math.round(value))
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "displays"
        title: "Brightness and colour"
        SettingsSliderRow {
            objectName: "settings-display-brightness"
            width: parent.width
            title: "Brightness"
            explanation: root.service.brightness.state === "ready" ? root.service.brightness.percent + "% of the built-in backlight." : "No adjustable backlight is available."
            from: 5
            value: root.service.brightness.percent ?? 5
            defaultValue: value
            unavailable: root.service.brightness.state !== "ready"
            busy: root.pending
            onValueRequested: value => root.request("brightness", Math.round(value))
        }
        SettingsToggleRow {
            objectName: "settings-night-light"
            width: parent.width
            title: "Night Light"
            explanation: "Use warmer display colours for evening light."
            value: root.service.night.on
            defaultValue: value
            unavailable: !["on", "off", "starting"].includes(root.service.night.state)
            onValueRequested: value => root.service.night.setOn(value)
        }
        SettingsSliderRow {
            objectName: "settings-night-warmth"
            width: parent.width
            title: "Warmth"
            explanation: "Choose how warm Night Light makes the display."
            value: root.service.night.warmth
            defaultValue: value
            unavailable: !["on", "off", "starting"].includes(root.service.night.state)
            onValueRequested: value => root.service.night.setWarmth(Math.round(value))
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "battery"
        title: "Power"
        SettingsRow {
            width: parent.width
            title: "Battery"
            explanation: root.service.batteryPercent < 0 ? "No battery is reported by the system." : root.service.batteryPercent + "% remaining" + (root.backend?.charging ? ", charging." : ".")
            managed: true
        }
        SettingsChoiceRow {
            objectName: "settings-power-profile"
            width: parent.width
            title: "Power mode"
            explanation: "Balance energy use and performance."
            options: Array.from(root.service.profiles.profiles ?? []).map(profile => ({
                        value: profile,
                        label: ({
                                "power-saver": "Power saver",
                                balanced: "Balanced",
                                performance: "Performance"
                            })[profile] || profile
                    }))
            value: root.service.profiles.current
            defaultValue: value
            unavailable: root.service.profiles.state !== "ready" || !options.length
            busy: root.pending
            onValueRequested: value => root.request("profile", value)
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "keyboard"
        title: "Keyboard layouts"
        SettingsChoiceRow {
            objectName: "settings-keyboard-layout"
            width: parent.width
            title: "Current layout"
            explanation: "Switch between the layouts already configured on this system."
            options: Array.from(root.niri.layouts?.names ?? []).map((name, index) => ({
                        value: index,
                        label: name
                    }))
            value: root.niri.layouts?.current_idx ?? -1
            defaultValue: value
            unavailable: !root.niri.connected || !options.length
            onValueRequested: value => root.niri.switchLayout(value)
        }
        SettingsRow {
            width: parent.width
            title: "Configured layouts"
            explanation: "The system manages the layout list and its switching shortcut."
            managed: true
        }
    }
}
