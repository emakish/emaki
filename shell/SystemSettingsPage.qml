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
        text: root.awaitingAction ? qsTranslate("SystemSettingsPage", "Working…") : root.actionResult === "requested" ? qsTranslate("SystemSettingsPage", "Change requested.") : qsTranslate("SystemSettingsPage", "Could not confirm the change. The current value is shown below.")
        textFormat: Text.PlainText
        color: root.pending ? SettingsTheme.dim : SettingsTheme.error
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }

    SettingsCard {
        width: parent.width
        visible: root.page === "wifi"
        title: qsTranslate("SystemSettingsPage", "Wireless connection")
        SettingsToggleRow {
            objectName: "settings-wifi-power"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Wi-Fi")
            explanation: qsTranslate("SystemSettingsPage", "Turn the wireless radio on or off.")
            value: root.backend?.wifiEnabled ?? false
            defaultValue: value
            unavailable: !root.backend?.networkReady || !root.backend?.wifiHardwareEnabled || !root.backend?.wifiDevices.length
            busy: root.pending
            onValueRequested: root.request("wifi-power", null)
        }
        SettingsRow {
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Connection")
            explanation: !root.backend?.networkReady ? qsTranslate("SystemSettingsPage", "Network status is unavailable.") : !root.backend.wifiHardwareEnabled ? qsTranslate("SystemSettingsPage", "The wireless radio is blocked by the system.") : root.connectedNetworks.length ? qsTranslate("SystemSettingsPage", "Connected to %1.").arg(root.connectedNetworks.map(network => network.name || network.ssid || qsTranslate("SystemSettingsPage", "Wireless network")).join(", ")) : qsTranslate("SystemSettingsPage", "No wireless connection is active.")
            managed: true
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "bluetooth"
        title: qsTranslate("SystemSettingsPage", "Bluetooth devices")
        SettingsToggleRow {
            objectName: "settings-bluetooth-power"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Bluetooth")
            explanation: qsTranslate("SystemSettingsPage", "Turn the Bluetooth adapter on or off.")
            value: root.backend?.adapter?.enabled ?? false
            defaultValue: value
            unavailable: !root.backend?.adapter
            busy: root.pending
            onValueRequested: root.request("bt-power", null)
        }
        SettingsRow {
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Connected devices")
            explanation: {
                const devices = Array.from(root.backend?.devices ?? []).filter(device => device.connected);
                return devices.length ? devices.map(device => device.name || qsTranslate("SystemSettingsPage", "Bluetooth device")).join(", ") : qsTranslate("SystemSettingsPage", "No Bluetooth devices are connected.");
            }
            managed: true
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "sound"
        title: qsTranslate("SystemSettingsPage", "Output")
        SettingsToggleRow {
            objectName: "settings-sound-mute"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Mute")
            explanation: qsTranslate("SystemSettingsPage", "Silence the current sound output.")
            value: root.service.sinkMuted
            defaultValue: value
            unavailable: !root.audioReady
            busy: root.pending
            onValueRequested: root.request("mute", null)
        }
        SettingsSliderRow {
            objectName: "settings-sound-volume"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Volume")
            explanation: root.audioReady ? qsTranslate("SystemSettingsPage", "%1% of the current output volume.").arg(Math.round(root.service.sinkVolume * 100).toFixed(0)) : qsTranslate("SystemSettingsPage", "No sound output is available.")
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
        title: qsTranslate("SystemSettingsPage", "Brightness and colour")
        SettingsSliderRow {
            objectName: "settings-display-brightness"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Brightness")
            explanation: root.service.brightness.state === "ready" ? qsTranslate("SystemSettingsPage", "%1% of the built-in backlight.").arg(root.service.brightness.percent.toFixed(0)) : qsTranslate("SystemSettingsPage", "No adjustable backlight is available.")
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
            title: qsTranslate("SystemSettingsPage", "Night Light")
            explanation: qsTranslate("SystemSettingsPage", "Use warmer display colours for evening light.")
            value: root.service.night.on
            defaultValue: value
            unavailable: !["on", "off", "starting"].includes(root.service.night.state)
            onValueRequested: value => root.service.night.setOn(value)
        }
        SettingsSliderRow {
            objectName: "settings-night-warmth"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Warmth")
            explanation: qsTranslate("SystemSettingsPage", "Choose how warm Night Light makes the display.")
            value: root.service.night.warmth
            defaultValue: value
            unavailable: !["on", "off", "starting"].includes(root.service.night.state)
            onValueRequested: value => root.service.night.setWarmth(Math.round(value))
        }
    }
    SettingsCard {
        width: parent.width
        visible: root.page === "battery"
        title: qsTranslate("SystemSettingsPage", "Power")
        SettingsRow {
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Battery")
            explanation: root.service.batteryPercent < 0 ? qsTranslate("SystemSettingsPage", "No battery is reported by the system.") : (root.backend?.charging ? qsTranslate("SystemSettingsPage", "%1% remaining, charging.") : qsTranslate("SystemSettingsPage", "%1% remaining.")).arg(root.service.batteryPercent.toFixed(0))
            managed: true
        }
        SettingsChoiceRow {
            objectName: "settings-power-profile"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Power mode")
            explanation: qsTranslate("SystemSettingsPage", "Balance energy use and performance.")
            options: Array.from(root.service.profiles.profiles ?? []).map(profile => ({
                        value: profile,
                        label: ({
                                "power-saver": qsTranslate("SystemSettingsPage", "Power saver"),
                                balanced: qsTranslate("SystemSettingsPage", "Balanced"),
                                performance: qsTranslate("SystemSettingsPage", "Performance")
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
        title: qsTranslate("SystemSettingsPage", "Keyboard layouts")
        SettingsChoiceRow {
            objectName: "settings-keyboard-layout"
            width: parent.width
            title: qsTranslate("SystemSettingsPage", "Current layout")
            explanation: qsTranslate("SystemSettingsPage", "Switch between the layouts already configured on this system.")
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
            title: qsTranslate("SystemSettingsPage", "Configured layouts")
            explanation: qsTranslate("SystemSettingsPage", "The system manages the layout list and its switching shortcut.")
            managed: true
        }
    }
}
