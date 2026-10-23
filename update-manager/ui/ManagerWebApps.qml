// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import "file:@EMAKI_DATADIR@/shell" as Shell

ColumnLayout {
    id: page
    required property var controller
    property string removing: ""
    property var removalSource: null
    onVisibleChanged: if (!visible) confirmation.close()
    Connections {
        target: page.controller
        function onWebsiteChanged(action: string): void {
            if (action === "add") { address.clear(); appName.clear(); }
            if (page.visible) address.forceActiveFocus();
        }
    }
    component WebsiteField: C.TextField {
        id: field
        implicitHeight: 40
        leftPadding: 12; rightPadding: 12
        font.family: "Noto Sans"; font.pixelSize: 14
        color: Shell.ShellPalette.managerInk
        placeholderTextColor: Shell.ShellPalette.managerDim
        selectByMouse: true
        background: Rectangle {
            radius: 12
            color: Qt.alpha(Shell.ShellPalette.managerWhite, .62)
            border.color: field.activeFocus ? Shell.ShellPalette.accent : Qt.alpha(Shell.ShellPalette.managerInk, .14)
        }
    }
    spacing: 16
    ManagerLabel { text: qsTranslate("ManagerWebApps", "Websites as apps"); font.pixelSize: 25; font.weight: Font.DemiBold }
    ManagerLabel { text: qsTranslate("ManagerWebApps", "Add a website as an app in Firefox. It has its own profile and launcher entry, which you can pin in the dock.") }
    ManagerLabel { text: qsTranslate("ManagerWebApps", "Links to other sites open in this app too. Point at the top edge or press Ctrl+L to see the address."); color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
    ManagerLabel { text: qsTranslate("ManagerWebApps", "Website address") }
    WebsiteField {
        id: address
        objectName: "website-address"
        Layout.fillWidth: true
        placeholderText: "https://example.com"
        onAccepted: if (addButton.enabled) addButton.clicked()
        Accessible.name: qsTranslate("ManagerWebApps", "Website address")
    }
    ManagerLabel { text: qsTranslate("ManagerWebApps", "Name (optional)") }
    WebsiteField {
        id: appName
        objectName: "website-name"
        Layout.fillWidth: true
        onAccepted: if (addButton.enabled) addButton.clicked()
        Accessible.name: qsTranslate("ManagerWebApps", "Name (optional)")
    }
    ManagerLabel { text: qsTranslate("ManagerWebApps", "When you press Add, Emaki loads the page and its icon to read the site name and save its icon."); color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
    ManagerButton {
        id: addButton
        objectName: "website-add"
        text: page.controller.phase === "websiteChange" ? qsTranslate("ManagerWebApps", "Adding…") : qsTranslate("ManagerWebApps", "Add")
        primary: true
        Layout.maximumWidth: implicitWidth; Layout.fillWidth: true; squeezed: parent.width > 0 && parent.width < implicitWidth
        enabled: address.text.trim() !== "" && !page.controller.busy && !page.controller.externalBusy
        onClicked: page.controller.addWebsite(address.text.trim(), appName.text.trim())
    }
    ManagerLabel { visible: !page.controller.websitesLoaded; text: qsTranslate("ManagerWebApps", "Loading…"); color: Shell.ShellPalette.managerDim }
    ManagerLabel { visible: page.controller.websitesLoaded && !(page.controller.websiteApps || []).length; text: qsTranslate("ManagerWebApps", "No website apps yet."); color: Shell.ShellPalette.managerDim }
    Repeater {
        model: page.controller.websiteApps || []
        Rectangle {
            id: card
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: contents.implicitHeight + 32
            radius: 16
            color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
            border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
            ColumnLayout {
                id: contents
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16
                spacing: 10
                ManagerLabel { text: card.modelData.name; font.pixelSize: 19; font.weight: Font.DemiBold }
                ManagerLabel { text: card.modelData.url; color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
                ManagerButton {
                    id: removeButton
                    text: qsTranslate("ManagerWebApps", "Remove")
                    Layout.maximumWidth: implicitWidth; Layout.fillWidth: true
                    squeezed: parent.width > 0 && parent.width < implicitWidth
                    enabled: !page.controller.busy && !page.controller.externalBusy
                    onClicked: {
                        if (confirmation.opened) return;
                        page.removing = card.modelData.id;
                        page.removalSource = removeButton;
                        confirmation.open();
                    }
                }
            }
        }
    }
    C.Popup {
        id: confirmation
        objectName: "website-removal-confirmation"
        parent: C.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(470, parent.width - 32)
        height: Math.min(removalContents.implicitHeight + 32, parent.height - 32)
        padding: 16
        modal: true; focus: true
        closePolicy: C.Popup.CloseOnEscape
        onOpened: { cancelRemoval.forceActiveFocus(); removalDelay.restart(); }
        // A second click from the original double-click cannot confirm, even if the dialog
        // happens to place its button under the pointer in a small window.
        Timer { id: removalDelay; interval: Qt.styleHints.mouseDoubleClickInterval; repeat: false }
        onClosed: {
            removalDelay.stop();
            page.removing = "";
            if (page.visible) {
                if (page.removalSource && page.removalSource.visible) page.removalSource.forceActiveFocus();
                else address.forceActiveFocus();
            }
            page.removalSource = null;
        }
        background: Rectangle { radius: 18; color: Shell.ShellPalette.managerPanel; border.width: 2; border.color: Shell.ShellPalette.accent }
        C.Overlay.modal: Rectangle { color: Qt.alpha(Shell.ShellPalette.managerInk, .26) }
        ColumnLayout {
            id: removalContents
            anchors.fill: parent
            spacing: 16
            C.ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.preferredHeight: removalWarning.implicitHeight
                Layout.minimumHeight: 0
                contentWidth: availableWidth; clip: true
                ManagerLabel {
                    id: removalWarning
                    width: parent.width
                    text: qsTranslate("ManagerWebApps", "Close the app first. Removing deletes its launcher, profile, sign-ins and saved site data.")
                }
            }
            ManagerActions {
                ManagerButton {
                    id: cancelRemoval
                    text: qsTranslate("ManagerWebApps", "Cancel")
                    onClicked: confirmation.close()
                }
                ManagerButton {
                    id: confirmRemoval
                    text: qsTranslate("ManagerWebApps", "Remove app and its data")
                    danger: true; primary: true
                    enabled: !!page.removing && !removalDelay.running && !page.controller.busy && !page.controller.externalBusy
                    onClicked: {
                        if (!confirmRemoval.enabled) return;
                        const appId = page.removing;
                        page.removalSource = null;
                        confirmation.close();
                        page.controller.removeWebsite(appId);
                    }
                }
            }
        }
    }
}
