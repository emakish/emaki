// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"
import "SudoText.js" as Copy

Column {
    id: page
    property bool sudoKeyInstalled: SudoKeySupport.installed
    spacing: 20
    SettingsCard {
        width: parent.width
        title: qsTranslate("SudoKeySettingsPage", "System rules")
        SettingsRow {
            objectName: "settings-agent-rules"
            width: parent.width
            title: qsTranslate("SudoKeySettingsPage", "Rules template")
            explanation: qsTranslate("SudoKeySettingsPage", "Read the system rules and choose whether to add them to your assistant. Opening instructions changes no configuration.")
            controlWidth: 270
            control: SettingsButton {
                text: qsTranslate("SudoKeySettingsPage", "Setup instructions")
                onClicked: Qt.openUrlExternally("file://" + Platform.dataDir + "/agent-rules-setup.md")
            }
        }
        SettingsRow {
            objectName: "settings-system-map"
            width: parent.width
            title: qsTranslate("SudoKeySettingsPage", "System map")
            explanation: qsTranslate("SudoKeySettingsPage", "See components, file zones and commands for asking the system state.")
            controlWidth: 270
            control: SettingsButton {
                text: qsTranslate("SudoKeySettingsPage", "Open map")
                onClicked: Qt.openUrlExternally("file://" + Platform.dataDir + "/system-map.md")
            }
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.sudoKeyInstalled
        title: qsTranslate("SudoKeySettingsPage", "Sudo key")
        SettingsRow {
            objectName: "settings-sudo-key"
            width: parent.width
            title: qsTranslate("SudoKeySettingsPage", "Sudo key")
            explanation: Copy.keySettings()
            unavailable: !SudoKeySupport.backend || !SudoKeySupport.backend.connected
            controlWidth: 190
            control: SettingsButton {
                text: Copy.keyReview()
                onClicked: SudoKeySupport.warningRequested = true
            }
        }
        SettingsRow {
            objectName: "settings-sudo-turn-reporting"
            width: parent.width
            title: qsTranslate("SudoKeySettingsPage", "Turn reporting")
            explanation: qsTranslate("SudoKeySettingsPage", "Let the agent tell Emaki when each turn ends, so a key can count turns.")
            controlWidth: 270
            control: SettingsButton {
                text: qsTranslate("SudoKeySettingsPage", "Setup instructions")
                onClicked: Qt.openUrlExternally("file://" + Platform.dataDir + "/sudo/turn-reporting.md")
            }
        }
    }
}
