// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"

Column {
    id: root
    required property var service
    required property var displays
    property int actionSerial: -1
    readonly property string actionResult: actionSerial === service.actionSerial ? service.actionState : "idle"
    function requestBrightness(value): void {
        service.act("brightness", Math.round(value));
        actionSerial = service.actionSerial;
    }
    property string selectedName: ""
    readonly property var activeOutputs: displays.outputs.filter(output => output.logical && output.enabled)
    readonly property string effectiveMain: activeOutputs.length === 1 ? activeOutputs[0].name : displays.main
    readonly property var selected: displays.outputs.find(output => output.name === selectedName) || displays.outputs[0] || null
    readonly property var modes: selected?.modes || []
    readonly property var mode: selected && selected.current_mode !== null ? modes[selected.current_mode] : null
    readonly property string resolution: mode ? mode.width + "x" + mode.height : ""
    readonly property bool blocked: displays.busy || displays.pending || !displays.ready
    readonly property var resolutions: [...new Set(modes.map(mode => mode.width + "x" + mode.height))].map(value => ({
                value,
                label: value.replace("x", " × ")
            }))
    readonly property var rates: modes.filter(mode => mode.width + "x" + mode.height === resolution).map(mode => ({
                value: wireMode(mode),
                label: (mode.refresh_rate / 1000).toFixed(2) + " Hz"
            }))
    spacing: 16
    signal confirmationNeeded
    Connections {
        target: root.displays
        function onPendingChanged(): void {
            if (root.displays.pending)
                root.confirmationNeeded();
        }
    }
    function displayName(output): string {
        if (!output)
            return "Display";
        if (/^(eDP|LVDS|DSI)-/i.test(output.name))
            return "Built-in display";
        return output.description || output.name;
    }
    function wireMode(mode): string {
        return mode.width + "x" + mode.height + "@" + (mode.refresh_rate / 1000).toFixed(3);
    }
    function resolutionLabel(output): string {
        const current = output.current_mode !== null ? output.modes?.[output.current_mode] : null;
        return current ? current.width + " × " + current.height : "";
    }
    function set(key, value): void {
        if (selected)
            displays.set(selected.name, key, value);
    }
    function reset(key): void {
        if (selected)
            displays.reset(selected.name, key);
    }
    function overrideValue(key, fallback): var {
        return selected?.overrides?.[key] ?? fallback;
    }
    function setResolution(value): void {
        const choices = modes.filter(mode => mode.width + "x" + mode.height === value);
        const next = choices.find(item => item.refresh_rate === mode?.refresh_rate) || choices.find(item => item.is_preferred) || choices[0];
        if (next)
            set("mode", wireMode(next));
    }
    function snappedPosition(output, x, y, factor): var {
        const width = output.logical.width;
        const height = output.logical.height;
        const threshold = 12 / factor;
        let bestX = threshold, bestY = threshold, snappedX = x, snappedY = y;
        for (const other of displays.outputs) {
            if (other.name === output.name || !other.enabled || !other.logical)
                continue;
            const rect = other.logical;
            for (const edge of [rect.x, rect.x + rect.width, rect.x - width, rect.x + rect.width - width]) {
                const distance = Math.abs(x - edge);
                if (distance < bestX) {
                    bestX = distance;
                    snappedX = edge;
                }
            }
            for (const edge of [rect.y, rect.y + rect.height, rect.y - height, rect.y + rect.height - height]) {
                const distance = Math.abs(y - edge);
                if (distance < bestY) {
                    bestY = distance;
                    snappedY = edge;
                }
            }
        }
        return {
            x: Math.round(snappedX),
            y: Math.round(snappedY)
        };
    }
    function moveDisplay(dx, dy): void {
        if (selected?.logical)
            set("position", {
                x: selected.logical.x + dx,
                y: selected.logical.y + dy
            });
    }
    Text {
        width: parent.width
        visible: root.displays.message !== ""
        text: root.displays.message
        textFormat: Text.PlainText
        color: "#a01b45"
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        objectName: "settings-display-confirmation"
        width: parent.width
        visible: root.displays.pending
        title: "Keep these display settings?"
        Text {
            width: parent.width
            text: "Reverting in " + root.displays.seconds + " seconds."
            color: SettingsTheme.ink
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            topPadding: 8
            bottomPadding: 12
        }
        Row {
            spacing: 12
            SettingsButton {
                text: "Keep changes"
                enabled: !root.displays.busy
                onClicked: root.displays.keep()
            }
            SettingsButton {
                text: "Revert"
                enabled: !root.displays.busy
                onClicked: root.displays.revert()
            }
        }
    }
    SettingsCard {
        objectName: "settings-display-arrangement"
        width: parent.width
        title: "Arrangement"
        Item {
            id: canvas
            width: parent.width
            height: 260
            readonly property var activeOutputs: root.activeOutputs
            readonly property real minX: Math.min(0, ...activeOutputs.map(output => output.logical.x))
            readonly property real minY: Math.min(0, ...activeOutputs.map(output => output.logical.y))
            readonly property real maxX: Math.max(1, ...activeOutputs.map(output => output.logical.x + output.logical.width))
            readonly property real maxY: Math.max(1, ...activeOutputs.map(output => output.logical.y + output.logical.height))
            readonly property real factor: Math.min((width - 32) / (maxX - minX), 210 / (maxY - minY), .16)
            readonly property real offsetX: (width - (maxX - minX) * factor) / 2 - minX * factor
            readonly property real offsetY: (height - (maxY - minY) * factor) / 2 - minY * factor
            Repeater {
                model: canvas.activeOutputs
                delegate: Rectangle {
                    id: monitor
                    required property var modelData
                    required property int index
                    x: canvas.offsetX + modelData.logical.x * canvas.factor
                    y: canvas.offsetY + modelData.logical.y * canvas.factor
                    width: Math.max(42, modelData.logical.width * canvas.factor)
                    height: Math.max(38, modelData.logical.height * canvas.factor)
                    radius: 8
                    color: root.selected?.name === modelData.name ? "#e7b39a" : "#d7d9de"
                    border.width: activeFocus || root.selected?.name === modelData.name ? 2 : 1
                    border.color: root.selected?.name === modelData.name ? SettingsTheme.accent : SettingsTheme.rim
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: root.displayName(modelData) + ". Use arrow keys to move this screen."
                    Keys.onPressed: event => {
                        root.selectedName = modelData.name;
                        const step = event.modifiers & Qt.ShiftModifier ? 100 : 10;
                        if (!root.blocked && [Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down].includes(event.key)) {
                            root.moveDisplay(event.key === Qt.Key_Left ? -step : event.key === Qt.Key_Right ? step : 0, event.key === Qt.Key_Up ? -step : event.key === Qt.Key_Down ? step : 0);
                            event.accepted = true;
                        }
                    }
                    Rectangle {
                        objectName: "display-main-marker-" + monitor.modelData.name
                        visible: root.effectiveMain === monitor.modelData.name
                        x: 6
                        y: 5
                        width: parent.width - 12
                        height: 4
                        radius: 2
                        color: SettingsTheme.accentInk
                    }
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 12
                        objectName: "display-resolution-label-" + monitor.modelData.name
                        text: root.displayName(monitor.modelData) + "\n" + root.resolutionLabel(monitor.modelData)
                        textFormat: Text.PlainText
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 11
                    }
                    Text {
                        x: 8
                        y: parent.height - height - 5
                        text: monitor.index + 1
                        color: SettingsTheme.dim
                        font.pixelSize: 10
                    }
                    MouseArea {
                        objectName: "display-drag-" + monitor.modelData.name
                        property bool moved: false
                        anchors.fill: parent
                        drag.target: root.blocked ? null : monitor
                        preventStealing: true
                        onPositionChanged: {
                            if (drag.active)
                                moved = true;
                        }
                        onPressed: {
                            moved = false;
                            root.selectedName = monitor.modelData.name;
                            monitor.forceActiveFocus();
                        }
                        onReleased: {
                            if (moved && !root.blocked)
                                root.set("position", root.snappedPosition(monitor.modelData, (monitor.x - canvas.offsetX) / canvas.factor, (monitor.y - canvas.offsetY) / canvas.factor, canvas.factor));
                            monitor.x = Qt.binding(() => canvas.offsetX + monitor.modelData.logical.x * canvas.factor);
                            monitor.y = Qt.binding(() => canvas.offsetY + monitor.modelData.logical.y * canvas.factor);
                        }
                    }
                }
            }
            Text {
                anchors.centerIn: parent
                visible: !canvas.activeOutputs.length
                text: root.displays.ready ? "No active displays are available." : "Reading displays…"
                color: SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
            }
        }
        SettingsRow {
            width: parent.width
            title: "Screen positions"
            explanation: "Drag the screens to match your desk. The top bar marks the main screen. Use arrow keys to move a focused screen."
            value: root.overrideValue("position", "auto")
            defaultValue: "auto"
            busy: root.blocked
            unavailable: !root.selected
            controlWidth: 0
            separator: false
            onResetRequested: root.reset("position")
        }
    }
    SettingsCard {
        width: parent.width
        title: root.displayName(root.selected)
        SettingsChoiceRow {
            width: parent.width
            title: "Display"
            explanation: "Choose the screen whose settings you want to change."
            controlWidth: Math.min(310, root.width * .45)
            options: root.displays.outputs.map(output => ({
                        value: output.name,
                        label: root.displayName(output)
                    }))
            value: root.selected?.name || ""
            defaultValue: value
            unavailable: !options.length
            onValueRequested: value => root.selectedName = value
        }
        SettingsChoiceRow {
            objectName: "settings-display-resolution"
            width: parent.width
            title: "Resolution"
            explanation: "Choose how many pixels the screen shows."
            options: root.resolutions
            value: root.resolution
            defaultValue: root.selected?.overrides?.mode ? "auto" : value
            unavailable: !root.selected?.enabled || !options.length
            busy: root.blocked
            onValueRequested: value => root.setResolution(value)
            onResetRequested: root.reset("mode")
        }
        SettingsChoiceRow {
            objectName: "settings-display-scale"
            width: parent.width
            title: "Scale"
            explanation: "Make text and controls larger or smaller."
            options: [...new Set([1, 1.25, 1.5, 1.75, 2, root.selected?.logical?.scale || 1])].sort((a, b) => a - b).map(value => ({
                        value,
                        label: Math.round(value * 100) + "%"
                    }))
            value: root.selected?.logical?.scale || 1
            defaultValue: root.selected?.overrides?.scale !== undefined ? "auto" : value
            unavailable: !root.selected?.enabled
            busy: root.blocked
            onValueRequested: value => root.set("scale", value)
            onResetRequested: root.reset("scale")
        }
        SettingsChoiceRow {
            objectName: "settings-display-refresh-rate"
            width: parent.width
            title: "Refresh rate"
            explanation: "Choose how often the screen updates each second."
            options: root.rates
            value: root.mode ? root.wireMode(root.mode) : ""
            defaultValue: root.selected?.overrides?.mode ? "auto" : value
            unavailable: !root.selected?.enabled || !options.length
            busy: root.blocked
            onValueRequested: value => root.set("mode", value)
            onResetRequested: root.reset("mode")
        }
        SettingsChoiceRow {
            objectName: "settings-display-rotation"
            width: parent.width
            title: "Rotation"
            explanation: "Match the orientation of your screen."
            options: [
                {
                    value: "normal",
                    label: "Normal"
                },
                {
                    value: "90",
                    label: "90°"
                },
                {
                    value: "180",
                    label: "180°"
                },
                {
                    value: "270",
                    label: "270°"
                }
            ]
            value: String(root.selected?.logical?.transform || "normal").toLowerCase()
            defaultValue: root.selected?.overrides?.rotation !== undefined ? "inherited" : value
            unavailable: !root.selected?.enabled
            busy: root.blocked
            onValueRequested: value => root.set("rotation", value)
            onResetRequested: root.reset("rotation")
        }
        SettingsToggleRow {
            objectName: "settings-display-main"
            width: parent.width
            title: "Main screen"
            explanation: "Choose the display focused now and at sign-in."
            value: root.effectiveMain === root.selected?.name
            defaultValue: root.displays.main === root.selected?.name ? false : value
            unavailable: !root.selected?.enabled
            busy: root.blocked
            onValueRequested: value => {
                if (value)
                    root.set("main", true);
                else if (root.displays.main === root.selected?.name)
                    root.reset("main");
            }
            onResetRequested: root.reset("main")
        }
        SettingsToggleRow {
            objectName: "settings-display-enabled"
            width: parent.width
            title: "Use this display"
            explanation: "Turn this screen on or off for this session. At least one screen must stay on."
            value: root.selected?.enabled ?? false
            defaultValue: value
            unavailable: !root.selected
            busy: root.blocked
            separator: false
            onValueRequested: value => root.set("enabled", value)
            onResetRequested: root.reset("enabled")
        }
    }
    SettingsCard {
        width: parent.width
        title: "Brightness and colour"
        Text {
            objectName: "settings-display-action-result"
            width: parent.width
            visible: !["idle", "confirmed"].includes(root.actionResult)
            text: ["pending", "busy"].includes(root.actionResult) ? "Working…" : root.actionResult === "requested" ? "Change requested." : "Could not confirm the change. The current value is shown below."
            color: SettingsTheme.dim
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
        SettingsSliderRow {
            objectName: "settings-display-brightness"
            width: parent.width
            title: "Brightness"
            explanation: root.service.brightness.state === "ready" ? root.service.brightness.percent + "% of the built-in backlight." : "No adjustable backlight is available."
            from: 5
            value: root.service.brightness.percent ?? 5
            defaultValue: value
            unavailable: root.service.brightness.state !== "ready"
            busy: ["pending", "busy"].includes(root.service.actionState)
            onValueRequested: value => root.requestBrightness(value)
        }
        SettingsToggleRow {
            objectName: "settings-night-light"
            width: parent.width
            title: "Night Light"
            explanation: "Use warmer display colours for evening light."
            value: root.service.night.on
            defaultValue: false
            onValueRequested: value => root.service.night.setOn(value)
            onResetRequested: root.service.night.setOn(false)
        }
        SettingsChoiceRow {
            objectName: "settings-night-schedule"
            width: parent.width
            title: "Schedule"
            explanation: "Keep Night Light on or use your own hours."
            options: [
                {
                    value: "always",
                    label: "Always"
                },
                {
                    value: "manual",
                    label: "Custom hours"
                }
            ]
            value: root.service.night.schedule
            defaultValue: "always"
            onValueRequested: value => root.service.night.setSchedule(value)
            onResetRequested: root.service.night.setSchedule("always")
        }
        Repeater {
            model: [
                {
                    key: "startTime",
                    title: "Start time",
                    fallback: "20:00"
                },
                {
                    key: "endTime",
                    title: "End time",
                    fallback: "07:00"
                }
            ]
            delegate: SettingsRow {
                id: timeRow
                required property var modelData
                width: parent.width
                visible: root.service.night.schedule === "manual"
                title: modelData.title
                explanation: "Use local time in 24-hour format (HH:MM)."
                value: root.service.night[modelData.key]
                defaultValue: modelData.fallback
                onResetRequested: root.service.night[modelData.key === "startTime" ? "setStartTime" : "setEndTime"](modelData.fallback)
                control: Component {
                    TextField {
                        text: timeRow.value
                        color: SettingsTheme.ink
                        font.family: SettingsTheme.fontFamily
                        selectByMouse: true
                        Accessible.name: timeRow.title
                        validator: RegularExpressionValidator {
                            regularExpression: /([01][0-9]|2[0-3]):[0-5][0-9]/
                        }
                        onEditingFinished: {
                            if (acceptableInput)
                                root.service.night[timeRow.modelData.key === "startTime" ? "setStartTime" : "setEndTime"](text);
                            text = Qt.binding(() => timeRow.value);
                        }
                        background: Rectangle {
                            radius: 8
                            color: SettingsTheme.field
                            border.color: SettingsTheme.rim
                        }
                    }
                }
            }
        }
        SettingsSliderRow {
            objectName: "settings-night-warmth"
            width: parent.width
            title: "Warmth"
            explanation: value + " K. Choose how warm Night Light makes the display."
            from: 2500
            to: 6000
            stepSize: 100
            value: 6500 - root.service.night.warmth * 50
            defaultValue: 4000
            separator: false
            onValueRequested: value => root.service.night.setWarmth(Math.round((6500 - value) / 50))
            onResetRequested: root.service.night.setWarmth(50)
        }
        Text {
            width: parent.width
            visible: !!root.service.night.error
            text: root.service.night.error || ""
            color: "#a01b45"
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
    }
}
