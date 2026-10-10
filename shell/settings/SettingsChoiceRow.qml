// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

SettingsRow {
    id: root
    property var options: []
    formatValue: value => root.options.find(option => option.value === value)?.label || String(value)
    controlWidth: 180
    control: Component {
        SettingsComboBox {
            model: root.options
            currentIndex: root.options.findIndex(option => option.value === root.value)
            Accessible.name: root.title
            onActivated: root.requestValue(currentValue)
        }
    }
}
