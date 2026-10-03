pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell.Io
import "file:///usr/share/emaki/shell" as Shell
import "Protocol.js" as Protocol

Item {
    id: view
    required property InstallerController controller
    signal hideRequested
    signal requestPlan
    readonly property color ink: Shell.LiquidPalette.inkOnLight
    readonly property color dim: Shell.LiquidPalette.dimOnLight
    readonly property color accent: "#e2733f"
    readonly property color danger: Shell.LiquidPalette.dangerOnLight
    readonly property var titles: ({
            welcome: "Welcome",
            keyboard: "Keyboard",
            network: "Network",
            disk: "Disk",
            filesystem: "File system",
            you: "You",
            review: "Review",
            install: "Install",
            done: "Done",
            error: "Error"
        })
    readonly property var phaseKeys: ["prepare_disk", "copy_packages", "bootloader", "account", "settings", "snapshot", "update", "finish"]
    readonly property var phaseNames: ["Prepare the disk", "Copy Emaki from the USB", "Set up the boot menu", "Create your account", "Apply your settings", "Create the first snapshot", "Check for updates", "Finish installation"]
    readonly property var tips: ["Mod+D opens the app launcher.", "Mod+Left / Right moves between columns.", "Mod+Up / Down moves between windows and workspaces.", "Mod+Shift+Left / Right moves a column.", "Mod+Space switches keyboard layouts.", "Mod+O opens the overview.", "Mod+R changes the column width.", "Mod+Shift+Slash shows all keyboard shortcuts."]
    property string wallpaperTexture: ""
    function size(bytes: real): string {
        return (bytes / 1073741824).toFixed(1) + " GiB";
    }
    function duration(seconds: int): string {
        return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    }
    function diskLabel(): string {
        return controller.selectedDisk ? controller.selectedDisk.model + ", " + size(controller.selectedDisk.size_bytes) : "the selected disk";
    }
    function diskReason(disk: var): string {
        return controller.diskReasons[disk.id] || Protocol.diskReason(disk);
    }
    function capture(path: string): void {
        view.grabToImage(result => result.saveToFile(path));
    }

    component Copy: Text {
        objectName: "installerCopy"
        color: view.ink
        font.family: Shell.ShellPalette.uiFont
        font.pixelSize: 17
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
    }
    component Heading: Copy {
        font.pixelSize: 38
        font.weight: Font.DemiBold
    }
    component Lead: Copy {
        font.pixelSize: 19
        color: view.dim
        lineHeight: 1.3
    }
    component Hint: Copy {
        font.pixelSize: 15
        color: view.dim
    }
    component Action: C.Button {
        id: button
        property bool primary: false
        property bool destructive: false
        implicitHeight: 52
        implicitWidth: Math.max(100, label.implicitWidth + 44)
        opacity: enabled ? 1 : .4
        hoverEnabled: true
        contentItem: Copy {
            id: label
            text: button.text
            font.pixelSize: 18
            font.weight: Font.DemiBold
            color: button.destructive ? "#fff8f3" : view.ink
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
        background: Rectangle {
            radius: 17
            color: button.destructive ? view.danger : button.primary ? view.accent : button.hovered ? "#20241018" : Shell.LiquidPalette.flatDrop
            border.width: button.activeFocus ? 2 : 1
            border.color: button.activeFocus ? view.accent : Shell.LiquidPalette.flatDropRim
        }
    }
    component Field: C.TextField {
        id: field
        implicitHeight: 52
        leftPadding: 18
        rightPadding: 18
        color: view.ink
        placeholderTextColor: view.dim
        font.family: Shell.ShellPalette.uiFont
        font.pixelSize: 19
        selectByMouse: true
        background: Rectangle {
            radius: 15
            color: "#bfffffff"
            border.width: field.activeFocus ? 2 : 1
            border.color: field.activeFocus ? view.accent : Shell.LiquidPalette.flatDropRim
        }
    }
    component Check: C.CheckBox {
        id: check
        implicitHeight: Math.max(42, contentItem.implicitHeight + 12)
        spacing: 12
        indicator: Rectangle {
            x: 0
            y: (check.height - height) / 2
            width: 25
            height: 25
            radius: 7
            color: check.checked ? view.accent : "#60ffffff"
            border.width: check.activeFocus ? 2 : 1
            border.color: check.activeFocus ? view.accent : view.dim
            Copy {
                anchors.centerIn: parent
                text: check.checked ? "✓" : ""
            }
        }
        contentItem: Copy {
            text: check.text
            leftPadding: 37
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 17
        }
    }
    component Choice: C.AbstractButton {
        id: choice
        property string detail: ""
        property bool chosen: false
        implicitHeight: Math.max(84, words.implicitHeight + 32)
        opacity: enabled ? 1 : .42
        hoverEnabled: true
        background: Rectangle {
            radius: 18
            color: choice.chosen ? "#35e2733f" : choice.hovered ? "#16241018" : Shell.LiquidPalette.flatDrop
            border.width: choice.chosen || choice.activeFocus ? 2 : 1
            border.color: choice.chosen || choice.activeFocus ? view.accent : Shell.LiquidPalette.flatDropRim
        }
        contentItem: RowLayout {
            spacing: 16
            Rectangle {
                Layout.leftMargin: 20
                implicitWidth: 20
                implicitHeight: 20
                radius: 10
                color: choice.chosen ? view.accent : "transparent"
                border.width: 1
                border.color: choice.chosen ? view.accent : view.dim
            }
            Column {
                id: words
                Layout.fillWidth: true
                Layout.rightMargin: 20
                spacing: 5
                Copy {
                    width: parent.width
                    text: choice.text
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                }
                Hint {
                    width: parent.width
                    text: choice.detail
                    visible: text !== ""
                    font.pixelSize: 16
                }
            }
        }
    }
    component Select: C.ComboBox {
        id: select
        implicitHeight: 44
        font.family: Shell.ShellPalette.uiFont
        font.pixelSize: 16
        contentItem: Copy {
            text: select.displayText
            leftPadding: 12
            rightPadding: 25
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 12
            color: "#aaffffff"
            border.width: select.activeFocus ? 2 : 1
            border.color: select.activeFocus ? view.accent : Shell.LiquidPalette.flatDropRim
        }
    }

    GlassPane {
        anchors.fill: parent
        wallpaperTexture: view.wallpaperTexture
    }
    Process {
        running: GraphicsInfo.api !== GraphicsInfo.Software && !view.controller.mockTransport
        command: ["python3", "-I", "-B", "/usr/share/emaki/shell/helpers/wallpaper.py", String(Math.round(view.width)), String(Math.round(view.height)), "1", "", "sharp"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    view.wallpaperTexture = JSON.parse(text).texture || "";
                } catch (_) {}
            }
        }
    }
    RowLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 30
        ColumnLayout {
            Layout.preferredWidth: 192
            Layout.fillHeight: true
            spacing: 4
            Copy {
                text: "Emaki"
                font.pixelSize: 32
                font.weight: Font.DemiBold
                Layout.bottomMargin: 2
            }
            Hint {
                text: view.controller.session.version ? "Install · " + view.controller.session.version : "Installer"
                Layout.bottomMargin: 22
            }
            Repeater {
                model: view.controller.steps
                delegate: C.AbstractButton {
                    id: stepButton
                    required property string modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: 44
                    enabled: index < view.controller.steps.indexOf(view.controller.step) && index < view.controller.steps.indexOf("review") && !view.controller.locked && !view.controller.session.outcome
                    onClicked: view.controller.edit(modelData)
                    background: Rectangle {
                        radius: 14
                        color: view.controller.step === stepButton.modelData ? Shell.LiquidPalette.flatDrop : "transparent"
                    }
                    contentItem: RowLayout {
                        spacing: 12
                        Rectangle {
                            Layout.leftMargin: 10
                            implicitWidth: 26
                            implicitHeight: 26
                            radius: 13
                            color: view.controller.step === stepButton.modelData ? view.accent : Shell.LiquidPalette.flatDrop
                            Copy {
                                anchors.centerIn: parent
                                text: String(stepButton.index + 1)
                                font.pixelSize: 14
                            }
                        }
                        Copy {
                            Layout.fillWidth: true
                            text: view.titles[stepButton.modelData]
                            color: view.controller.step === stepButton.modelData ? view.ink : view.dim
                            font.pixelSize: 18
                        }
                    }
                }
            }
            Item {
                Layout.fillHeight: true
            }
            Hint {
                Layout.fillWidth: true
                text: view.controller.step === "install" ? "Keep this computer on and the USB connected." : "Running from the live USB."
                lineHeight: 1.35
            }
        }
        Rectangle {
            Layout.fillHeight: true
            implicitWidth: 1
            color: "#20241018"
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18
            RowLayout {
                Layout.fillWidth: true
                Hint {
                    Layout.fillWidth: true
                    text: "EMAKI SETUP"
                    font.letterSpacing: 1.5
                }
                Action {
                    implicitHeight: 34
                    implicitWidth: 78
                    text: "Hide"
                    onClicked: view.hideRequested()
                }
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: notice.implicitHeight + 24
                radius: 12
                color: "#20e2733f"
                visible: view.controller.session.notice !== ""
                Copy {
                    id: notice
                    anchors.fill: parent
                    anchors.margins: 12
                    text: view.controller.session.notice
                    font.pixelSize: 15
                }
            }
            C.ScrollView {
                id: scroll
                objectName: "installerBody"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                Layout.minimumHeight: 0
                contentWidth: availableWidth
                enabled: !view.controller.session.planning && !view.controller.session.confirming
                Loader {
                    id: page
                    width: scroll.availableWidth
                    sourceComponent: ({
                            welcome: welcomePage,
                            keyboard: keyboardPage,
                            network: networkPage,
                            disk: diskPage,
                            filesystem: filesystemPage,
                            you: youPage,
                            review: reviewPage,
                            install: installPage,
                            done: donePage,
                            error: errorPage
                        })[view.controller.step]
                }
            }
            Hint {
                Layout.fillWidth: true
                visible: view.controller.helperMessage !== ""
                text: view.controller.helperMessage
                color: view.controller.helperFailed ? view.danger : view.dim
            }
            RowLayout {
                objectName: "installerFooter"
                Layout.fillWidth: true
                spacing: 12
                Action {
                    text: "Back"
                    visible: ["keyboard", "network", "disk", "filesystem", "you", "review"].indexOf(view.controller.step) >= 0
                    enabled: !view.controller.locked
                    onClicked: view.controller.back()
                }
                Item {
                    Layout.fillWidth: true
                }
                Action {
                    text: "Skip"
                    visible: view.controller.step === "network"
                    enabled: !view.controller.locked
                    onClicked: {
                        view.controller.onlineUpdate = false;
                        view.controller.next();
                    }
                }
                Action {
                    visible: ["keyboard", "network", "disk", "filesystem"].indexOf(view.controller.step) >= 0
                    text: view.controller.partitioning ? "GParted is open…" : "Continue"
                    primary: true
                    enabled: !view.controller.locked && (view.controller.step !== "disk" || (view.controller.session.inventory?.uefi && !!view.controller.selectedDisk && !view.diskReason(view.controller.selectedDisk)))
                    onClicked: view.controller.next()
                }
                Action {
                    visible: view.controller.step === "you"
                    text: view.controller.session.planning ? "Checking…" : "Review installation"
                    primary: true
                    enabled: view.controller.session.ready && !view.controller.locked
                    onClicked: view.requestPlan()
                }
                Action {
                    visible: view.controller.step === "review"
                    text: view.controller.mode === "erase" ? "Erase disk and install" : "Install"
                    destructive: view.controller.mode === "erase"
                    primary: true
                    enabled: view.controller.session.ready && !view.controller.locked && !!view.controller.session.plan?.token && (view.controller.mode !== "erase" || view.controller.agreed)
                    onClicked: view.controller.confirm()
                }
                Action {
                    visible: view.controller.step === "install"
                    text: view.controller.session.cancelPending ? "Cancellation requested…" : "Cancel after this phase"
                    enabled: view.controller.session.ready && view.controller.session.running && !view.controller.session.cancelPending && view.controller.session.phase !== "finish"
                    onClicked: view.controller.send("cancel", {})
                }
                Action {
                    visible: view.controller.step === "error" && !!view.controller.session.error?.retryable
                    text: "Try again"
                    primary: true
                    enabled: view.controller.session.ready
                    onClicked: view.controller.retry()
                }
                Action {
                    visible: view.controller.step === "done"
                    text: "Restart now"
                    primary: true
                    enabled: view.controller.session.ready
                    onClicked: view.controller.send("reboot", {})
                }
            }
        }
    }

    Component {
        id: welcomePage
        Column {
            spacing: 24
            Heading {
                width: parent.width
                text: "Welcome to Emaki"
                font.pixelSize: 50
            }
            Lead {
                width: parent.width
                text: "Your desktop, ready to explore. Emaki is running from the USB stick. Install it when you’re ready."
            }
            Choice {
                width: parent.width
                text: "Install Emaki"
                detail: "Choose your disk and settings, then review before installing."
                chosen: true
                onClicked: view.controller.next()
            }
            Choice {
                width: parent.width
                text: "Try Emaki first"
                detail: "The window steps aside. Open “Install Emaki” from the launcher to return."
                onClicked: view.hideRequested()
            }
            Hint {
                width: parent.width
                text: "English · Live session"
            }
        }
    }
    Component {
        id: keyboardPage
        Column {
            spacing: 16
            Heading {
                text: "Make yourself at home"
                width: parent.width
            }
            Lead {
                width: parent.width
                text: "Choose up to four keyboard layouts. The first is your default; Mod+Space switches between them."
            }
            Flow {
                width: parent.width
                spacing: 8
                Repeater {
                    model: view.controller.layouts
                    delegate: Action {
                        required property string modelData
                        required property int index
                        text: modelData + (index === 0 ? " · default" : " · make default")
                        implicitHeight: 40
                        primary: index === 0
                        onClicked: view.controller.defaultLayout(modelData)
                    }
                }
            }
            Field {
                id: layoutSearch
                width: parent.width
                placeholderText: "Search layouts and variants"
            }
            ListView {
                id: layoutList
                width: parent.width
                height: view.controller.catalog.trial ? 170 : 245
                clip: true
                spacing: 6
                model: view.controller.catalog.layouts.filter(x => (x.label + " " + x.layout + " " + x.variant).toLowerCase().indexOf(layoutSearch.text.toLowerCase()) >= 0)
                C.ScrollBar.vertical: C.ScrollBar {
                    policy: C.ScrollBar.AlwaysOn
                }
                delegate: Choice {
                    required property var modelData
                    width: layoutList.width - 14
                    text: modelData.label
                    detail: modelData.layout + (modelData.variant ? " · " + modelData.variant + " · variant unavailable in this installer" : "")
                    enabled: !modelData.variant && (view.controller.layouts.indexOf(modelData.layout) >= 0 || view.controller.layouts.length < 4)
                    chosen: !modelData.variant && view.controller.layouts.indexOf(modelData.layout) >= 0
                    onClicked: view.controller.toggleLayout(modelData.layout)
                }
            }
            Hint {
                width: parent.width
                text: "This version installs base layouts. Variants are listed for reference."
            }
            Action {
                visible: view.controller.catalog.trial
                text: "Apply layouts for testing"
                enabled: !view.controller.helperBusy
                onClicked: view.controller.callHelper("trial", {
                    layouts: view.controller.layouts
                })
            }
            Field {
                width: parent.width
                visible: view.controller.catalog.trial
                placeholderText: "Type to test · Mod+Space to switch"
            }
        }
    }
    Component {
        id: networkPage
        Column {
            id: netPage
            property var selected: null
            spacing: 16
            Heading {
                text: "Get connected"
                width: parent.width
            }
            Lead {
                width: parent.width
                text: "Offline installation is fully supported: packages come from the USB; being online only adds a final update."
            }
            Copy {
                visible: view.controller.network.wired
                text: "Wired · Connected"
                font.weight: Font.DemiBold
            }
            RowLayout {
                width: parent.width
                Hint {
                    Layout.fillWidth: true
                    text: view.controller.helperBusy ? "Checking the network…" : "Wi-Fi networks"
                }
                Action {
                    text: "Refresh"
                    implicitHeight: 40
                    enabled: !view.controller.helperBusy
                    onClicked: view.controller.callHelper("network", {})
                }
            }
            Repeater {
                model: view.controller.network.networks || []
                delegate: Choice {
                    required property var modelData
                    width: parent.width
                    text: modelData.ssid
                    detail: modelData.connected ? "Connected" : modelData.enterprise ? "Enterprise network · connect through the desktop network settings" : modelData.strength + "% signal · " + (modelData.security || "Open network")
                    enabled: !modelData.enterprise && !view.controller.helperBusy
                    chosen: modelData.connected || netPage.selected?.bssid === modelData.bssid
                    onClicked: {
                        netPage.selected = modelData;
                        wifiPassword.clear();
                    }
                }
            }
            Hint {
                visible: !(view.controller.network.networks || []).length && !view.controller.network.wired
                width: parent.width
                text: "No Wi-Fi networks found. You can continue offline."
            }
            Column {
                visible: !!netPage.selected && !netPage.selected.connected
                width: parent.width
                spacing: 10
                Field {
                    id: wifiPassword
                    width: parent.width
                    placeholderText: "Wi-Fi password"
                    echoMode: TextInput.Password
                    visible: !!netPage.selected?.security && netPage.selected.security !== "--"
                }
                Action {
                    text: "Connect"
                    primary: true
                    enabled: !view.controller.helperBusy
                    onClicked: {
                        view.controller.callHelper("join", {
                            bssid: netPage.selected.bssid,
                            device: netPage.selected.device,
                            password: wifiPassword.text
                        });
                        wifiPassword.clear();
                    }
                }
            }
            Check {
                width: parent.width
                text: "Update Emaki at the end when connected"
                checked: view.controller.onlineUpdate
                onToggled: view.controller.onlineUpdate = checked
            }
        }
    }
    Component {
        id: diskPage
        Column {
            spacing: 14
            Heading {
                width: parent.width
                text: view.controller.manualAssignments && view.controller.mode === "manual" ? "Assign your partitions" : "Where should Emaki go?"
            }
            Lead {
                width: parent.width
                text: view.controller.manualAssignments && view.controller.mode === "manual" ? view.diskLabel() : "Choose a disk. The live USB and unavailable disks cannot be selected."
            }
            Copy {
                width: parent.width
                visible: view.controller.session.inventory && !view.controller.session.inventory.uefi
                text: "This computer must boot the USB in UEFI mode to install Emaki."
                color: view.danger
            }
            Repeater {
                model: view.controller.manualAssignments && view.controller.mode === "manual" ? [] : view.controller.session.inventory?.disks || []
                delegate: Choice {
                    required property var modelData
                    width: parent.width
                    text: modelData.model + " · " + view.size(modelData.size_bytes)
                    detail: view.diskReason(modelData) || modelData.path + " · " + modelData.bus + " · " + modelData.partitions.length + " partitions"
                    chosen: view.controller.diskId === modelData.id
                    enabled: !view.diskReason(modelData) && !view.controller.locked
                    onClicked: view.controller.chooseDisk(modelData.id)
                }
            }
            Action {
                text: view.controller.manualAssignments && view.controller.mode === "manual" ? "Change disk" : "Refresh disks"
                implicitHeight: 40
                enabled: !view.controller.locked
                onClicked: {
                    view.controller.manualAssignments = false;
                    view.controller.probe();
                }
            }
            Choice {
                width: parent.width
                visible: !(view.controller.manualAssignments && view.controller.mode === "manual")
                text: "Erase disk"
                detail: "Delete everything on the selected disk and install Emaki."
                chosen: view.controller.mode === "erase"
                enabled: !view.controller.locked
                onClicked: view.controller.mode = "erase"
            }
            Choice {
                width: parent.width
                visible: !view.controller.manualAssignments
                text: "Manual"
                detail: "Prepare partitions in GParted, then assign their mountpoints."
                chosen: view.controller.mode === "manual"
                enabled: !view.controller.locked
                onClicked: view.controller.mode = "manual"
            }
            Choice {
                width: parent.width
                visible: view.controller.canAlongside
                text: "Install alongside Windows"
                detail: "Availability will be checked by the installer."
                chosen: view.controller.mode === "alongside"
                enabled: !view.controller.locked
                onClicked: view.controller.mode = "alongside"
            }
            Column {
                width: parent.width
                spacing: 12
                visible: view.controller.mode === "manual"
                Action {
                    text: "Open GParted"
                    enabled: !view.controller.locked && view.controller.session.ready
                    onClicked: view.controller.gpartedWarning = true
                }
                Rectangle {
                    width: parent.width
                    height: warning.implicitHeight + 32
                    radius: 18
                    color: "#20a01b45"
                    visible: view.controller.gpartedWarning
                    Column {
                        id: warning
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.margins: 16
                        y: 16
                        spacing: 12
                        Copy {
                            width: parent.width
                            color: view.danger
                            text: "GParted writes changes to your disks immediately when you apply them. Its changes are separate from this installer and cannot be undone here."
                        }
                        Row {
                            spacing: 12
                            Action {
                                text: "Go back"
                                onClicked: view.controller.gpartedWarning = false
                            }
                            Action {
                                text: "Open GParted"
                                destructive: true
                                onClicked: view.controller.openGparted()
                            }
                        }
                    }
                }
                Copy {
                    text: "Assign mountpoints"
                    font.pixelSize: 22
                    font.weight: Font.DemiBold
                }
                Hint {
                    width: parent.width
                    text: "Choose / for the system, /efi for the EFI System Partition, and optionally /home. Format deletes the data on that partition. Other partitions stay unassigned."
                }
                Repeater {
                    model: view.controller.selectedDisk?.partitions || []
                    delegate: Column {
                        id: partitionRow
                        required property var modelData
                        width: parent.width
                        spacing: 6
                        property var assignment: view.controller.mounts.find(m => m.partition_id === modelData.id) || null
                        function apply(): void {
                            view.controller.assign(modelData, mountSelect.currentText, fsSelect.currentText, formatCheck.checked);
                        }
                        Copy {
                            width: parent.width
                            text: partitionRow.modelData.path + " · " + view.size(partitionRow.modelData.size_bytes) + " · " + (partitionRow.modelData.fs || "unformatted") + (partitionRow.modelData.esp ? " · EFI" : "")
                        }
                        RowLayout {
                            width: parent.width
                            spacing: 12
                            Select {
                                id: mountSelect
                                Layout.fillWidth: true
                                model: ["none", "/", "/efi", "/home"]
                                currentIndex: partitionRow.assignment ? model.indexOf(partitionRow.assignment.mountpoint) : 0
                                onActivated: partitionRow.apply()
                            }
                            Select {
                                id: fsSelect
                                Layout.preferredWidth: 130
                                model: ["btrfs", "ext4", "vfat"]
                                currentIndex: Math.max(0, model.indexOf(partitionRow.assignment?.fs || partitionRow.modelData.fs))
                                onActivated: partitionRow.apply()
                            }
                            Check {
                                id: formatCheck
                                text: "Format"
                                checked: partitionRow.assignment?.format || false
                                onToggled: partitionRow.apply()
                            }
                        }
                    }
                }
            }
        }
    }
    Component {
        id: filesystemPage
        Column {
            spacing: 20
            Heading {
                width: parent.width
                text: "A home for your files"
            }
            Lead {
                width: parent.width
                text: "Choose how Emaki stores your system and files."
            }
            Choice {
                width: parent.width
                text: "btrfs · Recommended"
                detail: "Snapshots: roll back from the boot menu"
                chosen: view.controller.filesystem === "btrfs"
                onClicked: view.controller.filesystem = "btrfs"
            }
            Choice {
                width: parent.width
                text: "ext4"
                detail: "A simple, established file system. No system snapshots."
                chosen: view.controller.filesystem === "ext4"
                onClicked: view.controller.filesystem = "ext4"
            }
        }
    }
    Component {
        id: youPage
        Column {
            id: account
            spacing: 16
            function submit(): void {
                view.controller.plan(password.text, confirmation.text);
            }
            Connections {
                target: view
                function onRequestPlan(): void {
                    account.submit();
                }
            }
            Connections {
                target: view.controller
                function onClearPasswords(): void {
                    password.clear();
                    confirmation.clear();
                }
            }
            Heading {
                width: parent.width
                text: "About you"
            }
            Lead {
                width: parent.width
                text: "Your account on this computer."
            }
            GridLayout {
                width: parent.width
                columns: 2
                columnSpacing: 20
                rowSpacing: 8
                Hint {
                    text: "Full name"
                }
                Hint {
                    text: "Login"
                }
                Field {
                    Layout.fillWidth: true
                    text: view.controller.fullName
                    onTextEdited: {
                        view.controller.fullName = text;
                        if (!view.controller.loginEdited)
                            view.controller.login = Protocol.loginFromName(text);
                    }
                }
                Field {
                    Layout.fillWidth: true
                    text: view.controller.login
                    onTextEdited: {
                        view.controller.login = text;
                        view.controller.loginEdited = true;
                    }
                }
                Hint {
                    text: "Password"
                    Layout.topMargin: 10
                }
                Hint {
                    text: "Confirm password"
                    Layout.topMargin: 10
                }
                Field {
                    id: password
                    Layout.fillWidth: true
                    echoMode: TextInput.Password
                }
                Field {
                    id: confirmation
                    Layout.fillWidth: true
                    echoMode: TextInput.Password
                    onAccepted: account.submit()
                }
                Hint {
                    text: "Computer name"
                    Layout.topMargin: 10
                }
                Hint {
                    text: "Time zone"
                    Layout.topMargin: 10
                }
                Field {
                    Layout.fillWidth: true
                    text: view.controller.hostname
                    onTextEdited: view.controller.hostname = text
                }
                Field {
                    id: zoneSearch
                    Layout.fillWidth: true
                    placeholderText: "Search time zones"
                }
            }
            Hint {
                width: parent.width
                text: "Selected time zone: " + view.controller.timezone
            }
            ListView {
                id: zoneList
                width: parent.width
                height: 150
                clip: true
                spacing: 4
                model: view.controller.catalog.zones.filter(z => z.toLowerCase().indexOf(zoneSearch.text.toLowerCase()) >= 0)
                C.ScrollBar.vertical: C.ScrollBar {
                    policy: C.ScrollBar.AlwaysOn
                }
                delegate: Action {
                    required property string modelData
                    width: zoneList.width - 14
                    implicitHeight: 40
                    text: modelData
                    primary: view.controller.timezone === modelData
                    onClicked: {
                        view.controller.timezone = modelData;
                        view.controller.timezoneEdited = true;
                    }
                }
            }
            Hint {
                width: parent.width
                visible: !!password.text || !!confirmation.text
                color: view.danger
                text: Protocol.accountError(view.controller.fullName, view.controller.login, password.text, confirmation.text, view.controller.hostname)
            }
        }
    }
    Component {
        id: reviewPage
        Column {
            spacing: 16
            Heading {
                width: parent.width
                text: "One last look"
            }
            Lead {
                width: parent.width
                text: "Review the installation plan for " + view.diskLabel() + "."
            }
            Repeater {
                model: view.controller.session.plan?.summary || []
                delegate: Copy {
                    required property string modelData
                    width: parent.width
                    text: "•  " + modelData
                }
            }
            Repeater {
                model: view.controller.session.plan?.warnings || []
                delegate: Copy {
                    required property var modelData
                    width: parent.width
                    text: modelData.msg
                    color: view.danger
                }
            }
            Repeater {
                model: view.controller.session.plan?.errors || []
                delegate: Copy {
                    required property var modelData
                    width: parent.width
                    text: modelData.msg
                    color: view.danger
                    font.weight: Font.DemiBold
                }
            }
            Row {
                visible: (view.controller.session.plan?.errors || []).length > 0
                spacing: 12
                Action {
                    text: "Edit disk settings"
                    onClicked: view.controller.edit("disk")
                }
                Action {
                    text: "Edit account"
                    onClicked: view.controller.edit("you")
                }
            }
            Rectangle {
                visible: !!view.controller.session.plan?.token
                width: parent.width
                height: destructive.implicitHeight + 36
                radius: 20
                color: "#1aa01b45"
                border.color: "#59a01b45"
                Column {
                    id: destructive
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 18
                    y: 18
                    spacing: 8
                    Copy {
                        width: parent.width
                        color: view.danger
                        text: view.controller.mode === "erase" ? "Everything on this disk will be deleted. This cannot be undone." : "Formatted partitions will lose their data. Emaki will write system and account files to the assigned partitions."
                    }
                    Check {
                        visible: view.controller.mode === "erase"
                        width: parent.width
                        text: "I understand " + view.diskLabel() + " will be erased"
                        checked: view.controller.agreed
                        onToggled: view.controller.agreed = checked
                    }
                }
            }
            Hint {
                width: parent.width
                visible: !!view.controller.session.plan?.token
                text: "This review expires in " + Math.min(10, Math.max(0, Math.ceil((view.controller.session.deadline - view.controller.clockMs) / 60000))) + " minutes. Changes require a new review."
            }
        }
    }
    Component {
        id: installPage
        Column {
            spacing: 16
            Heading {
                width: parent.width
                text: "Making room for you"
            }
            Lead {
                width: parent.width
                text: view.controller.session.confirming ? "Starting the installation…" : "Emaki is being installed. Keep this computer on."
            }
            C.ProgressBar {
                id: progress
                width: parent.width
                height: 12
                from: 0
                to: 100
                value: view.controller.session.totalPct
                background: Rectangle {
                    radius: 6
                    color: "#18241018"
                }
                contentItem: Item {
                    Rectangle {
                        width: progress.visualPosition * parent.width
                        height: parent.height
                        radius: 6
                        color: view.accent
                    }
                }
            }
            RowLayout {
                width: parent.width
                Copy {
                    Layout.fillWidth: true
                    text: Math.round(view.controller.session.totalPct) + "% complete"
                }
                Hint {
                    text: "Elapsed " + view.duration(view.controller.elapsed)
                }
            }
            Column {
                width: parent.width
                spacing: 4
                Repeater {
                    model: view.phaseKeys
                    delegate: RowLayout {
                        id: phaseRow
                        required property string modelData
                        required property int index
                        width: parent.width
                        height: 32
                        readonly property bool current: view.controller.session.phase === modelData
                        Copy {
                            text: phaseRow.index < view.phaseKeys.indexOf(view.controller.session.phase) ? "✓" : phaseRow.current ? "●" : "○"
                            color: phaseRow.current ? view.accent : view.dim
                        }
                        Copy {
                            Layout.fillWidth: true
                            text: view.phaseNames[phaseRow.index]
                            color: phaseRow.current ? view.ink : view.dim
                            font.weight: phaseRow.current ? Font.DemiBold : Font.Normal
                        }
                        C.BusyIndicator {
                            visible: phaseRow.current && view.controller.session.indeterminate
                            running: visible
                            implicitWidth: 28
                            implicitHeight: 28
                        }
                        Hint {
                            visible: phaseRow.current
                            text: view.controller.session.indeterminate ? "Working…" : view.controller.session.phasePct === null ? "Skipped" : Math.round(view.controller.session.phasePct) + "%"
                        }
                    }
                }
            }
            Rectangle {
                width: parent.width
                height: tip.implicitHeight + 32
                radius: 18
                color: Shell.LiquidPalette.flatDrop
                Column {
                    id: tip
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 16
                    y: 16
                    spacing: 8
                    Hint {
                        text: "MAKE YOURSELF AT HOME"
                        color: Shell.LiquidPalette.accentOnLight
                    }
                    Copy {
                        width: parent.width
                        text: view.tips[Math.floor(view.controller.elapsed / 12) % view.tips.length]
                        font.pixelSize: 19
                    }
                }
            }
            Hint {
                width: parent.width
                text: view.controller.session.cancelMessage || (view.controller.session.cancelPending ? "The installer will stop at the next phase boundary and clean up. Disk changes are not undone." : "")
            }
        }
    }
    component LogSave: Column {
        spacing: 12
        Hint {
            width: parent.width
            text: view.controller.media.length ? "Save the installation log to a removable medium:" : "To save the log, mount a removable medium under /run/media/live/."
        }
        Repeater {
            model: view.controller.media
            delegate: Action {
                required property string modelData
                required property int index
                text: "Save log · " + modelData.split("/").pop()
                enabled: view.controller.session.ready
                onClicked: view.controller.saveLog(index)
            }
        }
        Action {
            text: "Refresh removable media"
            implicitHeight: 42
            enabled: !view.controller.helperBusy
            onClicked: view.controller.callHelper("media", {})
        }
    }
    Component {
        id: donePage
        Column {
            spacing: 24
            Heading {
                width: parent.width
                text: "Welcome home."
                font.pixelSize: 50
            }
            Lead {
                width: parent.width
                text: "Emaki is installed. Restart to begin using your new desktop, and remove the USB when the computer restarts."
            }
            Copy {
                text: "Installed in " + view.duration(Math.round(view.controller.session.seconds)) + "."
            }
            LogSave {
                width: parent.width
            }
        }
    }
    Component {
        id: errorPage
        Column {
            spacing: 22
            Heading {
                width: parent.width
                text: "Installation stopped"
            }
            Lead {
                width: parent.width
                text: view.controller.session.error?.message || "The installer could not continue."
                color: view.danger
            }
            Copy {
                width: parent.width
                text: view.controller.session.cancelMessage
                visible: text !== ""
            }
            Hint {
                width: parent.width
                text: view.controller.session.error?.retryable ? "You can check your settings and prepare a fresh plan." : "Save the log to help diagnose the problem before starting again."
            }
            LogSave {
                width: parent.width
            }
        }
    }
}
