// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

SettingsRow {
    id: root
    property string unit: ""
    formatValue: value => String(value) + (root.unit ? " " + root.unit : "")
    property real from: 0
    property real to: 100
    property real stepSize: 1
    signal flushRequested
    function flushPending(): void {
        flushRequested();
    }
    controlWidth: 220
    control: Component {
        Slider {
            id: slider
            implicitHeight: 32
            implicitWidth: 220
            rightPadding: 48
            from: root.from
            to: root.to
            stepSize: root.stepSize
            value: Number(root.value)
            focusPolicy: Qt.StrongFocus
            wheelEnabled: false
            Accessible.name: root.title
            property bool pointerMoved: false
            property bool pending: false
            property bool submitted: false
            function followValue() {
                value = Qt.binding(() => Number(root.value));
            }
            function submit(flush = false) {
                keyboardCommit.stop();
                if (!pending)
                    return;
                if (root.busy && !(flush && root.catalog && root.settingKey))
                    return;
                pending = false;
                if (!root.managed && !root.unavailable && value !== Number(root.value)) {
                    submitted = true;
                    // A queued catalog write survives the window that collected it.
                    root.valueRequested(value);
                    if (root.busy)
                        return;
                    submitted = false;
                }
                followValue();
            }
            Connections {
                target: root
                function onFlushRequested() {
                    slider.submit(true);
                }
                function onBusyChanged() {
                    if (!root.busy) {
                        if (slider.pending)
                            slider.submit();
                        else if (slider.submitted) {
                            slider.submitted = false;
                            slider.followValue();
                        }
                    }
                }
            }
            Keys.onPressed: event => {
                if ([Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End].includes(event.key)) {
                    if (event.key === Qt.Key_Home)
                        value = from;
                    else if (event.key === Qt.Key_End)
                        value = to;
                    else if (event.key === Qt.Key_Right || event.key === Qt.Key_Up)
                        increase();
                    else
                        decrease();
                    pending = true;
                    keyboardCommit.restart();
                    event.accepted = true;
                }
            }
            onMoved: {
                pending = true;
                if (pressed)
                    pointerMoved = true;
                else
                    keyboardCommit.restart();
            }
            Timer {
                id: keyboardCommit
                interval: 400
                onTriggered: slider.submit()
            }
            onActiveFocusChanged: {
                if (!activeFocus && keyboardCommit.running)
                    submit();
            }
            onPressedChanged: {
                if (!pressed && pointerMoved) {
                    pointerMoved = false;
                    submit();
                }
            }
            Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: 44
                text: root.formatValue(Number(slider.value.toFixed(2)))
                horizontalAlignment: Text.AlignRight
                color: SettingsTheme.ink
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
            }
            background: Rectangle {
                antialiasing: true
                x: slider.leftPadding
                y: slider.topPadding + slider.availableHeight / 2 - 3
                width: slider.availableWidth
                height: 6
                radius: 3
                color: SettingsTheme.rim
                Rectangle {
                    antialiasing: true
                    width: slider.visualPosition * parent.width
                    height: parent.height
                    radius: 3
                    color: SettingsTheme.accent
                }
            }
            handle: Rectangle {
                antialiasing: true
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 20
                height: 20
                radius: 10
                color: "#fffaf6"
                border.width: slider.activeFocus ? 2 : 1
                border.color: slider.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
            }
        }
    }
}
