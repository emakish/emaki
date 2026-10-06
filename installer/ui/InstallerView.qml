pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell.Io
import "file:///usr/share/emaki/shell" as Shell
import "Protocol.js" as Protocol
import "Timezones.js" as Timezones

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
            timezone: "Time zone",
            disk: "Disk",
            filesystem: "File system",
            encryption: "Encryption",
            you: "You",
            software: "Software",
            review: "Review",
            install: "Install",
            done: "Done",
            error: "Error"
        })
    readonly property var phaseKeys: ["prepare_disk", "copy_packages", "bootloader", "account", "settings", "snapshot", "update", "finish"]
    readonly property var phaseNames: ["Prepare the disk", "Copy Emaki from the USB", "Set up the boot menu", "Create your account", "Apply your settings", "Create the first snapshot", "Check for updates", "Finish installation"]
    readonly property var tips: ["Super+D opens the app launcher.", "Super+Left / Right moves between columns.", "Super+Up / Down moves between windows and workspaces.", "Super+Shift+Left / Right moves a column.", "Super+Space switches keyboard layouts.", "Super+O opens the overview.", "Super+R changes the column width.", "Super+Shift+Slash shows all keyboard shortcuts."]
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
    // The planner takes FAT32 only at /efi, and /efi only as FAT32 (planner.manual_partitions).
    // An unassigned row only shows the format the partition has.
    function formats(mountpoint: string, current: var): var {
        if (mountpoint === "none" && current)
            return [current];
        return mountpoint === "/efi" ? ["vfat"] : ["btrfs", "ext4"];
    }
    // Shown on the welcome page and on the Disk step: one sentence for both.
    readonly property bool uefiRefused: !!controller.session.inventory && !controller.session.inventory.uefi
    readonly property string uefiRefusal: controller.session.inventory?.uefi_bits === 32 ? "64-bit UEFI is required." : "Emaki installs only on computers with UEFI. If the boot menu offers a UEFI entry for the USB stick, choose it; otherwise this computer is not supported."
    function capture(path: string): void {
        view.grabToImage(result => result.saveToFile(path));
    }
    // The first rule the You form fails (its passwords stay inside the page); "" when it can continue.
    property string accountBlocked: ""
    // The first rule the You form fails that no field shows under itself yet.
    property string accountHint: ""
    // Why the step's main button is grey: its first failing condition, in the words the window
    // already uses for it. The line under the buttons shows it while the window is not busy.
    function blockedReason(): string {
        const c = controller;
        if (c.locked)
            return "";
        if (c.step === "timezone")
            return c.applyingTimezone ? "Setting the live clock…" : c.timezoneInfo?.timezone === c.timezone ? "" : c.timezoneMessage;
        if (c.step === "encryption") {
            if (c.encryptionReady)
                return "";
            if (c.encryption !== "encrypted")
                return "Choose whether to encrypt the disk.";
            if (c.encryptionPassword === "account")
                return accountChoiceRefusal();
            return Protocol.diskPasswordError(c.diskPassword) || (c.diskPassword !== c.diskConfirmation ? "The disk passwords do not match." : "");
        }
        if (c.step === "you")
            return c.session.ready ? accountHint : "";
        if (c.step !== "disk" || !c.session.inventory)
            return "";
        if (!c.session.inventory.uefi)
            return uefiRefusal;
        if (!c.selectedDisk)
            return "Choose a disk.";
        if (diskReason(c.selectedDisk))
            return diskReason(c.selectedDisk);
        if (c.encryptedRefusal)
            return c.encryptedRefusal;
        if (!c.encryptedConfirmed)
            return "Type ERASE to confirm erasing the listed volumes.";
        if (c.mode === "alongside" && !c.alongsideSizeValid)
            return "Not enough space for this hibernation reservation. Turn off hibernation or free more space in Windows, then refresh.";
        if (c.mode === "manual")
            return Protocol.manualReason(c.selectedDisk) || (c.manualAssignments && !c.manualReady ? "Exactly one / and one /efi are required." : "");
        return "";
    }
    function accountChoiceRefusal(): string {
        return "Not available: the login screen starts in " + controller.layoutLabel(controller.layouts[0].toUpperCase()) + ", but the disk is unlocked at startup in " + controller.layoutLabel(controller.unlockLayout.toUpperCase()) + ".";
    }
    // The password field (or its eye) that has focus; the controller switches the live layouts for it.
    property var focusedSecret: null
    function secretFocused(field: var): void {
        if (field.activeFocus || field.eyeFocused)
            focusedSecret = field;
        else if (focusedSecret === field)
            focusedSecret = null;
    }
    // Under the account and disk password fields while one of them has focus (the reports that
    // carry Caps Lock are read only then), as the lock screen says it under its field.
    readonly property bool capsLockShown: controller.capsLock && !!focusedSecret && focusedSecret.checksLayout === true
    // The same for Num Lock: niri starts every session with it off, where keypad keys move the cursor.
    readonly property bool numLockShown: controller.numLock && !!focusedSecret && focusedSecret.checksLayout === true
    readonly property string numLockLine: "Num Lock is on — the login screen starts with Num Lock off; type digits on the main row."
    Binding {
        target: view.controller
        property: "secretFocus"
        value: !view.focusedSecret ? "" : !view.focusedSecret.checksLayout ? "plain" : view.focusedSecret.unlock ? "unlock" : "secret"
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
        // A hidden button gives up keyboard focus, so a later Return cannot press it unseen.
        onVisibleChanged: if (!visible)
            focus = false
        Keys.onReturnPressed: event => {
            if (!event.isAutoRepeat)
                clicked();
        }
        Keys.onEnterPressed: event => {
            if (!event.isAutoRepeat)
                clicked();
        }
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
    component PasswordField: Field {
        id: secretField
        property bool revealed: false
        property string passwordLabel: "Password"
        // A disk passphrase: typed in the startup layout, which the live session runs while it has focus.
        property bool unlock: false
        // Typed again later at the login screen or at startup, so only in the layout used there.
        // A Wi-Fi password is used now: any layout the person picks types it.
        property bool checksLayout: true
        readonly property bool eyeFocused: eye.activeFocus
        // The text before the edit being reported: textEdited comes before textChanged (Qt 6.11).
        property string shownText: ""
        echoMode: revealed ? TextInput.Normal : TextInput.Password
        // Keys typed while niri runs another layout than the field needs would land in that layout.
        readOnly: checksLayout && !view.controller.keyboardReady
        rightPadding: 58 + (layoutChip.visible ? layoutChip.implicitWidth + 8 : 0)
        onTextChanged: {
            shownText = text;
            if (!text)
                revealed = false;
        }
        // Pasted text was never typed with the login screen's keys, so a field that checks the
        // layout takes none: no context menu, no paste or undo keys (Ctrl+V, Shift+Insert, Ctrl+Z
        // can bring back emptied text), no middle-click paste of the primary selection (below).
        // ContextMenu.menu is a deferred property: reading it first runs the style's assignment
        // (TextEditingContextMenu), which would otherwise replace a null set before it.
        Component.onCompleted: if (checksLayout && C.ContextMenu.menu)
            C.ContextMenu.menu = null
        // A shown password is plain text to Qt: Copy and Cut keys put it on the clipboard, which the
        // session's history keeps, and a left release after a mouse selection publishes it as the
        // primary selection. These fields copy and cut nothing, shown or not, and a shown one
        // cannot be selected with the mouse.
        selectByMouse: !(checksLayout && revealed)
        Keys.onPressed: event => {
            if (!secretField.checksLayout)
                return;
            if (event.matches(StandardKey.Paste)) {
                event.accepted = true;
                view.controller.secretPasted(false);
            } else if (event.matches(StandardKey.Undo) || event.matches(StandardKey.Redo) || event.matches(StandardKey.Copy) || event.matches(StandardKey.Cut))
                event.accepted = true;
            else if (secretField.keypadDigit(event))
                event.accepted = true;
        }
        // A keypad digit or decimal key types text only with Num Lock on; at the login screen (and
        // at startup) Num Lock is off and the same key moves the cursor, so it types nothing here.
        function keypadDigit(event: KeyEvent): bool {
            return (event.modifiers & Qt.KeypadModifier) && event.text !== "" && ((event.key >= Qt.Key_0 && event.key <= Qt.Key_9) || event.key === Qt.Key_Period || event.key === Qt.Key_Comma);
        }
        onActiveFocusChanged: {
            view.secretFocused(secretField);
            if (activeFocus && checksLayout)
                view.controller.secretEntered();
        }
        onEyeFocusedChanged: view.secretFocused(secretField)
        Component.onDestruction: if (view.focusedSecret === secretField)
            view.focusedSecret = null
        // The layout this field types in, with the code the login and lock screens show.
        Copy {
            id: layoutChip
            objectName: secretField.objectName + "Layout"
            anchors.right: eye.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            visible: view.controller.liveKeyboard
            font.pixelSize: 14
            color: view.dim
            wrapMode: Text.NoWrap
            text: {
                const keyboard = view.controller;
                if (keyboard.keyboardSwitching)
                    return "Applying keyboard…";
                if (secretField.unlock && view.focusedSecret !== secretField)
                    return keyboard.layoutLabel(keyboard.unlockLayout.toUpperCase());
                if (!keyboard.liveCode)
                    return "Layout unknown";
                return secretField.unlock ? keyboard.layoutLabel(keyboard.liveCode) : keyboard.liveCode;
            }
        }
        Connections {
            target: view.controller
            function onStepChanged(): void {
                secretField.revealed = false;
            }
            function onClearPasswords(): void {
                secretField.clear();
                secretField.revealed = false;
            }
            function onSecretsDiscarded(): void {
                if (!secretField.checksLayout)
                    return;
                secretField.clear();
                secretField.revealed = false;
            }
        }
        // Every key is checked against niri's next report (InstallerController.secretEdited); an
        // edit that brought more than one character (a paste route not caught above, an input
        // method's string) empties the page's password fields instead.
        Connections {
            target: secretField
            enabled: secretField.checksLayout
            function onTextEdited(): void {
                if (Protocol.insertedLength(secretField.shownText, secretField.text) > 1)
                    view.controller.secretPasted(true);
                else
                    view.controller.secretEdited();
            }
        }
        // A middle click pastes the primary selection (Qt reads it on release); this area takes
        // the press first. It lies under the eye button, which takes only the left button.
        MouseArea {
            objectName: secretField.objectName + "MiddleClick"
            anchors.fill: parent
            enabled: secretField.checksLayout
            acceptedButtons: Qt.MiddleButton
            onPressed: view.controller.secretPasted(false)
        }
        C.AbstractButton {
            id: eye
            objectName: secretField.objectName + "Toggle"
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.rightMargin: 5
            width: 44
            height: 42
            checkable: true
            checked: secretField.revealed
            Accessible.name: (checked ? "Hide " : "Show ") + secretField.passwordLabel.toLowerCase()
            Accessible.role: Accessible.CheckBox
            onClicked: secretField.revealed = !secretField.revealed
            Keys.onReturnPressed: clicked()
            Keys.onEnterPressed: clicked()
            background: Rectangle {
                radius: 10
                color: eye.hovered || eye.checked ? "#20e2733f" : "transparent"
                border.width: eye.activeFocus ? 2 : 0
                border.color: view.accent
            }
            contentItem: Canvas {
                id: eyeDrawing
                onPaint: {
                    const ctx = getContext("2d");
                    ctx.reset();
                    ctx.strokeStyle = view.ink;
                    ctx.lineWidth = 1.8;
                    const x = width / 2;
                    const y = height / 2;
                    ctx.beginPath();
                    ctx.moveTo(x - 11, y);
                    ctx.bezierCurveTo(x - 5, y - 10, x + 5, y - 10, x + 11, y);
                    ctx.bezierCurveTo(x + 5, y + 10, x - 5, y + 10, x - 11, y);
                    ctx.stroke();
                    ctx.beginPath();
                    ctx.arc(x, y, 3, 0, Math.PI * 2);
                    ctx.stroke();
                    if (secretField.revealed) {
                        ctx.beginPath();
                        ctx.moveTo(x - 12, y + 10);
                        ctx.lineTo(x + 12, y - 10);
                        ctx.stroke();
                    }
                }
                Connections {
                    target: secretField
                    function onRevealedChanged(): void {
                        eyeDrawing.requestPaint();
                    }
                }
            }
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
        onVisibleChanged: if (!visible)
            focus = false
        Keys.onReturnPressed: event => {
            if (!event.isAutoRepeat)
                clicked();
        }
        Keys.onEnterPressed: event => {
            if (!event.isAutoRepeat)
                clicked();
        }
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
                text: view.controller.session.version ? "Install · " + view.controller.session.version + (view.controller.session.label ? " " + view.controller.session.label : "") : "Installer"
                Layout.bottomMargin: 22
            }
            Repeater {
                model: view.controller.steps
                delegate: C.AbstractButton {
                    id: stepButton
                    required property string modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: view.height < 680 ? 30 : view.height < 740 ? 36 : 44
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
                    objectName: "installerNotice"
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
                // The style hides an idle bar; a page taller than the body must show that it scrolls.
                readonly property bool overflowing: contentHeight > height + 0.5
                C.ScrollBar.vertical.policy: overflowing ? C.ScrollBar.AlwaysOn : C.ScrollBar.AsNeeded
                function revealItem(item: Item): void {
                    if (!item || !item.visible)
                        return;
                    let ancestor = item;
                    while (ancestor && ancestor !== page)
                        ancestor = ancestor.parent;
                    if (!ancestor)
                        return;
                    const flick = scroll.contentItem as Flickable;
                    const top = item.mapToItem(flick.contentItem, 0, 0).y;
                    const bottom = top + item.height;
                    let offset = flick.contentY;
                    if (top < offset || item.height > flick.height)
                        offset = top;
                    else if (bottom > offset + flick.height)
                        offset = bottom - flick.height;
                    flick.contentY = Math.max(0, Math.min(offset, flick.contentHeight - flick.height));
                }
                function revealFocus(): void {
                    revealItem(view.Window.window?.activeFocusItem);
                }
                Connections {
                    target: view.Window.window
                    function onActiveFocusItemChanged(): void {
                        // Focus can arrive before the new page's layout has settled.
                        Qt.callLater(scroll.revealFocus);
                    }
                }
                Loader {
                    id: page
                    objectName: "installerPage"
                    // The bar is drawn over the page: leave it the gutter the inner lists leave.
                    width: scroll.availableWidth - (scroll.overflowing ? 14 : 0)
                    sourceComponent: ({
                            welcome: welcomePage,
                            keyboard: keyboardPage,
                            network: networkPage,
                            timezone: timezonePage,
                            disk: diskPage,
                            filesystem: filesystemPage,
                            encryption: encryptionPage,
                            you: youPage,
                            software: softwarePage,
                            review: reviewPage,
                            install: installPage,
                            done: donePage,
                            error: errorPage
                        })[view.controller.step]
                }
            }
            // Outside the scrolled page: the agreement and the install button are always on screen together.
            Check {
                objectName: "agreementCheck"
                Layout.fillWidth: true
                visible: view.controller.step === "review" && !!view.controller.session.plan?.token && view.controller.needsAgreement
                text: view.controller.mode === "alongside" ? "I have backed up my files and understand Windows will be resized" : "I understand " + view.diskLabel() + " will be erased"
                checked: view.controller.agreed
                onToggled: view.controller.agreed = checked
            }
            Hint {
                Layout.fillWidth: true
                visible: view.controller.helperMessage !== ""
                text: view.controller.helperMessage
                color: view.controller.helperFailed ? view.danger : view.dim
            }
            RowLayout {
                Layout.fillWidth: true
                visible: view.controller.keyboardMessage !== ""
                spacing: 12
                Hint {
                    objectName: "keyboardProblem"
                    Layout.fillWidth: true
                    // Wraps in the width it gets; its one-line width must not narrow the window's other column.
                    Layout.preferredWidth: 0
                    text: view.controller.keyboardMessage
                    color: view.danger
                }
                Action {
                    objectName: "keyboardRetry"
                    text: "Try again"
                    implicitHeight: 40
                    visible: view.controller.keyboardFailed && !view.controller.keyboardUnavailable
                    // A click leaves the focus in the password field: the retry writes the layouts that field needs.
                    focusPolicy: Qt.NoFocus
                    onClicked: view.controller.applyLayouts()
                }
            }
            RowLayout {
                objectName: "installerFooter"
                Layout.fillWidth: true
                spacing: 12
                Action {
                    text: "Back"
                    visible: ["keyboard", "network", "timezone", "disk", "filesystem", "encryption", "you", "software", "review"].indexOf(view.controller.step) >= 0
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
                    id: continueAction
                    objectName: "continueButton"
                    visible: ["keyboard", "network", "timezone", "disk", "filesystem", "encryption"].indexOf(view.controller.step) >= 0
                    text: view.controller.partitioning ? "GParted is open…" : "Continue"
                    primary: true
                    enabled: !view.controller.locked && (view.controller.step !== "encryption" || (view.controller.encryptionReady && view.controller.secretsChecked)) && (view.controller.step !== "timezone" || (view.controller.timezoneInfo?.timezone === view.controller.timezone && !view.controller.applyingTimezone)) && (view.controller.step !== "disk" || (view.controller.session.inventory?.uefi && !!view.controller.selectedDisk && view.controller.encryptedConfirmed && !view.diskReason(view.controller.selectedDisk) && (view.controller.mode !== "alongside" || view.controller.alongsideSizeValid) && (view.controller.mode !== "manual" || (!Protocol.manualReason(view.controller.selectedDisk) && (!view.controller.manualAssignments || view.controller.manualReady)))))
                    onClicked: view.controller.next()
                }
                Action {
                    id: accountAction
                    objectName: "accountContinue"
                    visible: view.controller.step === "you"
                    text: "Continue"
                    primary: true
                    // The same rule as the other steps: only a form the review would accept continues.
                    enabled: view.controller.session.ready && !view.controller.locked && view.accountBlocked === "" && view.controller.secretsChecked
                    onClicked: view.requestPlan()
                }
                Action {
                    visible: view.controller.step === "software"
                    text: view.controller.session.planning ? "Checking…" : "Review installation"
                    primary: true
                    enabled: view.controller.session.ready && !view.controller.locked
                    onClicked: view.controller.reviewSoftware()
                }
                Action {
                    visible: view.controller.step === "review"
                    text: view.controller.mode === "erase" ? "Erase disk and install" : "Install"
                    destructive: view.controller.needsAgreement
                    primary: true
                    enabled: view.controller.session.ready && !view.controller.locked && view.controller.encryptedConfirmed && !!view.controller.session.plan?.token && (!view.controller.needsAgreement || view.controller.agreed)
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
            // One line under a grey Continue saying why.
            Hint {
                objectName: "blockedReason"
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignRight
                visible: text !== ""
                text: (continueAction.visible && !continueAction.enabled) || (accountAction.visible && !accountAction.enabled) ? view.blockedReason() : ""
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
            Copy {
                objectName: "welcomeUefiRefusal"
                width: parent.width
                visible: view.uefiRefused
                text: view.uefiRefusal
                color: view.danger
            }
            Choice {
                objectName: "installChoice"
                Component.onCompleted: forceActiveFocus()
                width: parent.width
                text: "Install Emaki"
                detail: "Choose your disk and settings, then review before installing."
                // Without installable UEFI only the live session is offered.
                enabled: !view.uefiRefused
                chosen: enabled
                onClicked: view.controller.next()
            }
            Choice {
                objectName: "tryChoice"
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
            id: keyboardColumn
            spacing: 16
            // Height of everything but the layout list; never reads the list's own height (binding loop).
            function otherHeight(): real {
                let total = 0, shown = 0;
                for (const child of children) {
                    if (!child.visible || (child !== layoutList && child.height <= 0))
                        continue;
                    ++shown;
                    if (child !== layoutList)
                        total += child.height;
                }
                return total + Math.max(0, shown - 1) * spacing;
            }
            Heading {
                text: "Make yourself at home"
                width: parent.width
            }
            Lead {
                width: parent.width
                text: "Choose up to four keyboard layouts. The first is your default; Super+Space switches between them."
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
                objectName: "layoutSearch"
                Component.onCompleted: forceActiveFocus()
                width: parent.width
                placeholderText: "Search layouts and variants"
            }
            ListView {
                id: layoutList
                width: parent.width
                height: Math.max(view.controller.catalog.trial ? 170 : 245, Math.floor(scroll.availableHeight - keyboardColumn.otherHeight()))
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
                enabled: !view.controller.keyboardSwitching
                onClicked: view.controller.applyLayouts()
            }
            Field {
                width: parent.width
                visible: view.controller.catalog.trial
                placeholderText: "Type to test · Super+Space to switch"
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
                    objectName: "wifiNetwork"
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
                PasswordField {
                    id: wifiPassword
                    objectName: "wifiPassword"
                    passwordLabel: "Wi-Fi password"
                    checksLayout: false
                    width: parent.width
                    placeholderText: "Wi-Fi password"
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
        id: timezonePage
        Column {
            id: zonePage
            spacing: 8
            property var filtered: view.controller.catalog.zones.filter(z => Timezones.matches(z, zoneSearch.text))
            function syncSelection(): void {
                zoneList.currentIndex = filtered.indexOf(view.controller.timezone);
                if (zoneList.currentIndex >= 0)
                    zoneList.positionViewAtIndex(zoneList.currentIndex, ListView.Contain);
            }
            function chooseCurrent(): void {
                if (zoneList.currentIndex >= 0 && zoneList.currentIndex < filtered.length)
                    view.controller.chooseTimezone(filtered[zoneList.currentIndex]);
            }
            // Height of everything but the zone list; never reads the list's own height (binding loop).
            function otherHeight(): real {
                let total = 0, shown = 0;
                for (const child of children) {
                    if (!child.visible || (child !== zoneList && child.height <= 0))
                        continue;
                    ++shown;
                    if (child !== zoneList)
                        total += child.height;
                }
                return total + Math.max(0, shown - 1) * spacing;
            }
            Timer {
                id: syncZone
                interval: 0
                onTriggered: {
                    zonePage.syncSelection();
                    if (zoneList.currentIndex < 0 && zonePage.filtered.length)
                        zoneList.currentIndex = 0;
                }
            }
            Component.onCompleted: {
                syncSelection();
                zoneSearch.forceActiveFocus();
            }
            RowLayout {
                width: parent.width
                Heading {
                    Layout.fillWidth: true
                    text: "Time zone"
                }
                Column {
                    Copy {
                        objectName: "zoneClock"
                        text: view.controller.zoneClock()
                        font.pixelSize: 28
                        font.weight: Font.DemiBold
                    }
                    Hint {
                        text: view.controller.applyingTimezone ? "Setting the live clock…" : "Current local time"
                    }
                }
            }
            TimezoneMap {
                objectName: "timezoneMap"
                width: parent.width
                height: Math.max(120, Math.min(260, scroll.availableHeight - 290))
                timezone: view.controller.timezone
                availableZones: view.controller.catalog.zones
                onPicked: zone => {
                    zoneSearch.clear();
                    view.controller.chooseTimezone(zone);
                    zonePage.syncSelection();
                }
            }
            RowLayout {
                width: parent.width
                Copy {
                    Layout.fillWidth: true
                    text: Timezones.label(view.controller.timezone)
                    font.weight: Font.DemiBold
                }
                Hint {
                    text: view.controller.timezoneInfo?.timezone === view.controller.timezone ? view.controller.timezoneInfo.abbreviation : ""
                }
            }
            Field {
                id: zoneSearch
                objectName: "zoneSearch"
                width: parent.width
                implicitHeight: 44
                placeholderText: "Search a city, region or time zone"
                Accessible.name: "Search a city, region or time zone"
                onAccepted: zonePage.chooseCurrent()
                Keys.onDownPressed: {
                    zoneList.currentIndex = Math.min(zonePage.filtered.length - 1, Math.max(0, zoneList.currentIndex + 1));
                    zoneList.forceActiveFocus();
                }
                Keys.onUpPressed: {
                    zoneList.currentIndex = Math.max(0, zoneList.currentIndex - 1);
                    zoneList.forceActiveFocus();
                }
            }
            ListView {
                id: zoneList
                objectName: "zoneList"
                width: parent.width
                height: Math.max(108, Math.floor(scroll.availableHeight - zonePage.otherHeight()))
                clip: true
                spacing: 2
                model: zonePage.filtered
                onModelChanged: syncZone.restart()
                activeFocusOnTab: true
                keyNavigationEnabled: true
                highlightMoveDuration: 0
                Keys.onReturnPressed: zonePage.chooseCurrent()
                Keys.onEnterPressed: zonePage.chooseCurrent()
                Keys.onEscapePressed: zoneSearch.forceActiveFocus()
                C.ScrollBar.vertical: C.ScrollBar {
                    policy: C.ScrollBar.AlwaysOn
                }
                delegate: Action {
                    id: zoneChoice
                    required property string modelData
                    required property int index
                    width: zoneList.width - 14
                    implicitHeight: 34
                    focusPolicy: Qt.NoFocus
                    text: (view.controller.timezone === modelData ? "✓  " : "") + Timezones.label(modelData)
                    primary: view.controller.timezone === modelData
                    onClicked: {
                        zoneList.currentIndex = index;
                        view.controller.chooseTimezone(modelData);
                    }
                    background: Rectangle {
                        radius: 10
                        color: zoneChoice.primary ? "#45e2733f" : "#60ffffff"
                        border.width: zoneList.activeFocus && zoneList.currentIndex === zoneChoice.index ? 2 : 0
                        border.color: view.accent
                    }
                }
                Hint {
                    anchors.centerIn: parent
                    visible: zonePage.filtered.length === 0
                    text: "No matching zones. Try a nearby city or region."
                }
            }
            Hint {
                width: parent.width
                font.pixelSize: 12
                text: "Map: Timezone Boundary Builder / © OpenStreetMap contributors · ODbL 1.0"
            }
            RowLayout {
                width: parent.width
                visible: !!view.controller.timezoneMessage
                Hint {
                    Layout.fillWidth: true
                    text: view.controller.timezoneMessage
                    color: view.danger
                }
                Action {
                    text: "Try again"
                    implicitHeight: 40
                    enabled: view.controller.session.ready && !view.controller.applyingTimezone
                    onClicked: view.controller.applyTimezone()
                }
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
                objectName: "uefiRefusal"
                width: parent.width
                visible: view.controller.session.inventory && !view.controller.session.inventory.uefi
                text: view.uefiRefusal
                color: view.danger
            }
            Row {
                objectName: "diskScanning"
                spacing: 12
                visible: view.controller.session.probing
                C.BusyIndicator {
                    running: visible
                    implicitWidth: 28
                    implicitHeight: 28
                }
                Hint {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Scanning disks…"
                }
            }
            Repeater {
                model: view.controller.manualAssignments && view.controller.mode === "manual" ? [] : view.controller.session.inventory?.disks || []
                delegate: Choice {
                    required property var modelData
                    objectName: "disk-" + modelData.id
                    width: parent.width
                    text: modelData.model + " · " + view.size(modelData.size_bytes)
                    detail: view.diskReason(modelData) || modelData.path + " · " + modelData.bus + " · " + modelData.partitions.length + " partitions" + Protocol.diskContents(modelData)
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
                objectName: "eraseChoice"
                text: "Erase disk"
                detail: "Delete everything on the selected disk and install Emaki. Root needs at least " + view.size(view.controller.rootMinimum) + "."
                chosen: view.controller.mode === "erase"
                enabled: !view.controller.locked
                onClicked: view.controller.mode = "erase"
            }
            Choice {
                width: parent.width
                visible: !view.controller.manualAssignments
                objectName: "manualChoice"
                text: "Manual"
                detail: Protocol.manualReason(view.controller.selectedDisk) || "Prepare partitions in GParted, then assign their mountpoints."
                chosen: view.controller.mode === "manual"
                enabled: !view.controller.locked && !Protocol.manualReason(view.controller.selectedDisk)
                onClicked: view.controller.mode = "manual"
            }
            Choice {
                width: parent.width
                visible: view.controller.canAlongside
                objectName: "alongsideChoice"
                text: "Install alongside Windows"
                detail: "Keep Windows and use part of its free space for Emaki."
                chosen: view.controller.mode === "alongside"
                enabled: !view.controller.locked
                onClicked: view.controller.mode = "alongside"
            }
            Column {
                id: encryptedWarning
                width: parent.width
                spacing: 10
                visible: view.controller.encryptedTargets.length > 0
                readonly property var warnings: view.controller.encryptedRefusal ? [
                    {
                        warning: view.controller.encryptedRefusal
                    }
                ] : view.controller.encryptedTargets
                function reveal(): void {
                    scroll.revealItem(encryptedWarning);
                }
                // Selection and wrapping can change before the column has been positioned.
                // Reveal the whole warning after layout, keeping focus on the selected disk.
                onWarningsChanged: Qt.callLater(reveal)
                onVisibleChanged: Qt.callLater(reveal)
                onHeightChanged: Qt.callLater(reveal)
                onYChanged: Qt.callLater(reveal)
                Repeater {
                    model: encryptedWarning.warnings
                    delegate: Copy {
                        required property var modelData
                        width: parent.width
                        color: view.danger
                        text: modelData.warning
                    }
                }
                Field {
                    objectName: "encryptedEraseConfirmation"
                    width: parent.width
                    visible: !view.controller.encryptedRefusal
                    placeholderText: "Type ERASE"
                    text: view.controller.encryptedEraseText
                    enabled: !view.controller.locked
                    onTextEdited: view.controller.encryptedEraseText = text
                    Accessible.name: "Confirm erasing the listed volumes"
                }
            }
            Check {
                objectName: "hibernationCheck"
                width: parent.width
                text: (view.controller.session.inventory?.memory_bytes || 0) > 0 ? "Enable hibernation · reserves " + view.size(view.controller.session.inventory.memory_bytes) + " (same as RAM)" : "Hibernation unavailable: firmware did not report the RAM size"
                checked: view.controller.hibernation
                enabled: !view.controller.locked && (view.controller.session.inventory?.memory_bytes || 0) > 0
                onToggled: view.controller.hibernation = checked
            }
            Hint {
                width: parent.width
                visible: view.controller.hibernation && view.controller.encryption !== "encrypted"
                text: "Without encryption, hibernation writes memory, including passwords, to the disk unencrypted."
            }
            Copy {
                width: parent.width
                visible: !!view.controller.selectedDisk?.shrink?.reason
                text: view.controller.selectedDisk?.shrink?.reason || ""
            }
            Column {
                width: parent.width
                spacing: 8
                visible: view.controller.mode === "alongside" && view.controller.canAlongside
                Copy {
                    width: parent.width
                    text: Protocol.alongsideReview(view.controller.windowsPartition, view.controller.shrinkBytes)
                }
                C.Slider {
                    objectName: "alongsideSlider"
                    width: parent.width
                    from: Math.min(view.controller.alongsideMinimum, to)
                    to: view.controller.windowsPartition?.shrink.max_free_bytes || 32 * 1073741824
                    stepSize: 1048576
                    value: view.controller.shrinkBytes
                    enabled: !view.controller.locked
                    palette.highlight: view.accent
                    onMoved: view.controller.shrinkBytes = Math.round(value / 1048576) * 1048576
                }
                Hint {
                    width: parent.width
                    text: "At least " + (view.controller.alongsideMinimum / 1073741824).toFixed(2) + " GiB for Emaki. Windows retains its verified minimum plus 2 GiB. Windows will run chkdsk on its first boot; let it finish."
                }
            }
            Copy {
                width: parent.width
                visible: view.controller.mode === "alongside" && !view.controller.alongsideSizeValid
                text: "Not enough space for this hibernation reservation. Turn off hibernation or free more space in Windows, then refresh."
                color: view.danger
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
                Hint {
                    objectName: "noPartitions"
                    width: parent.width
                    visible: !!view.controller.selectedDisk && view.controller.selectedDisk.partitions.length === 0
                    text: "This disk has no partitions yet. Create them with the partition editor button above, then assign them here."
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
                            // A format the new mountpoint does not offer falls back to the partition's own.
                            const formats = view.formats(mountSelect.currentText, partitionRow.modelData.fs);
                            const fs = formats.indexOf(fsSelect.currentText) >= 0 ? fsSelect.currentText : formats.indexOf(modelData.fs) >= 0 ? modelData.fs : formats[0];
                            view.controller.assign(modelData, mountSelect.currentText, fs, formatCheck.checked);
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
                                objectName: "mountSelect-" + partitionRow.modelData.id
                                Layout.fillWidth: true
                                model: ["none", "/", "/efi", "/home"]
                                currentIndex: partitionRow.assignment ? model.indexOf(partitionRow.assignment.mountpoint) : 0
                                onActivated: partitionRow.apply()
                            }
                            Select {
                                id: fsSelect
                                objectName: "fsSelect-" + partitionRow.modelData.id
                                Layout.preferredWidth: 130
                                model: view.formats(mountSelect.currentText, partitionRow.modelData.fs)
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
                        // The planner's root minimum, before the review refuses it.
                        Hint {
                            objectName: "rootMinimum-" + partitionRow.modelData.id
                            width: parent.width
                            visible: partitionRow.assignment?.mountpoint === "/"
                            text: "Root needs at least " + view.size(view.controller.rootMinimum) + "."
                            color: partitionRow.modelData.size_bytes < view.controller.rootMinimum ? view.danger : view.dim
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
                objectName: "filesystemBtrfs"
                // The chosen option takes focus; the default does when nothing else is chosen.
                Component.onCompleted: if (view.controller.filesystem !== "ext4")
                    forceActiveFocus()
                width: parent.width
                text: "btrfs · Recommended"
                detail: "Snapshots: roll back from the boot menu"
                chosen: view.controller.filesystem === "btrfs"
                onClicked: view.controller.filesystem = "btrfs"
            }
            Choice {
                objectName: "filesystemExt4"
                Component.onCompleted: if (chosen)
                    forceActiveFocus()
                width: parent.width
                text: "ext4"
                detail: "A simple, established file system. No system snapshots."
                chosen: view.controller.filesystem === "ext4"
                onClicked: view.controller.filesystem = "ext4"
            }
        }
    }
    Component {
        id: encryptionPage
        Column {
            spacing: 14
            Heading {
                width: parent.width
                text: "Protect your files"
            }
            Lead {
                width: parent.width
                text: "Encryption protects files if this computer is lost or stolen. Unlock the disk each time you start it."
            }
            Hint {
                width: parent.width
                visible: view.controller.encryption === "encrypted"
                text: "If you forget the disk password, your files cannot be recovered. Startup uses an English (US) keyboard."
            }
            Row {
                width: parent.width
                spacing: 12
                Choice {
                    objectName: "encryptYes"
                    width: (parent.width - 12) / 2
                    text: "Encrypt the disk"
                    chosen: view.controller.encryption === "encrypted"
                    onClicked: {
                        view.controller.encryption = "encrypted";
                        if (!view.controller.accountUnlocks)
                            view.controller.encryptionPassword = "separate";
                    }
                }
                Choice {
                    objectName: "encryptNo"
                    width: (parent.width - 12) / 2
                    text: "Leave it unencrypted"
                    chosen: view.controller.encryption === "none"
                    onClicked: {
                        view.controller.encryption = "none";
                        view.controller.diskPassword = "";
                        view.controller.diskConfirmation = "";
                    }
                }
            }
            Column {
                width: parent.width
                spacing: 12
                visible: view.controller.encryption === "encrypted"
                Choice {
                    objectName: "encryptAccount"
                    width: parent.width
                    text: "One password for everything · Recommended"
                    detail: view.controller.accountUnlocks ? "Use your account password: less to remember. Anyone who knows it can also unlock your files. Changing it later does not change the disk password." : view.accountChoiceRefusal()
                    enabled: view.controller.accountUnlocks
                    chosen: view.controller.encryptionPassword === "account"
                    onClicked: {
                        view.controller.encryptionPassword = "account";
                        view.controller.diskPassword = "";
                        view.controller.diskConfirmation = "";
                    }
                }
                Choice {
                    objectName: "encryptSeparate"
                    width: parent.width
                    text: "A separate disk password"
                    detail: "Keep disk access separate from your account. You must remember two passwords and use the disk password at startup."
                    chosen: view.controller.encryptionPassword === "separate"
                    onClicked: view.controller.encryptionPassword = "separate"
                }
                Row {
                    width: parent.width
                    spacing: 12
                    visible: view.controller.encryptionPassword === "separate"
                    PasswordField {
                        objectName: "diskPassword"
                        unlock: true
                        passwordLabel: "Disk password"
                        width: (parent.width - 12) / 2
                        placeholderText: "Disk password"
                        text: view.controller.diskPassword
                        onTextEdited: view.controller.diskPassword = text
                    }
                    PasswordField {
                        objectName: "diskConfirmation"
                        unlock: true
                        width: (parent.width - 12) / 2
                        placeholderText: "Confirm disk password"
                        passwordLabel: "Disk password confirmation"
                        text: view.controller.diskConfirmation
                        onTextEdited: view.controller.diskConfirmation = text
                    }
                }
                Hint {
                    objectName: "diskCapsLock"
                    width: parent.width
                    visible: view.capsLockShown
                    text: "Caps Lock is on"
                }
                Hint {
                    objectName: "diskNumLock"
                    width: parent.width
                    visible: view.numLockShown
                    text: view.numLockLine
                }
                Hint {
                    width: parent.width
                    visible: view.controller.diskConfirmation.length > 0 && view.controller.diskPassword !== view.controller.diskConfirmation
                    text: "The disk passwords do not match."
                    color: view.danger
                }
                Hint {
                    width: parent.width
                    visible: view.controller.encryptionPassword === "separate" && view.controller.diskPassword.length > 0 && !!Protocol.diskPasswordError(view.controller.diskPassword)
                    text: Protocol.diskPasswordError(view.controller.diskPassword)
                    color: view.danger
                }
                Hint {
                    width: parent.width
                    visible: view.controller.mode === "manual"
                    text: "Root must be formatted. Only root is encrypted; separate data partitions stay unencrypted."
                }
                Hint {
                    width: parent.width
                    visible: view.controller.mode === "alongside"
                    text: "Only Emaki is encrypted. Its password is needed before the startup menu appears, including the Windows choice."
                }
            }
            Hint {
                width: parent.width
                visible: view.controller.hibernation && view.controller.encryption === "none"
                text: "Hibernation writes memory, including passwords, to the disk unencrypted."
            }
        }
    }
    Component {
        id: youPage
        Column {
            id: account
            spacing: 16
            property bool submitted: false
            // Fields the person typed in; the login also changes when the name it is made from does.
            property var touched: ({})
            function touch(field: string): void {
                const next = Object.assign({}, touched);
                next[field] = true;
                touched = next;
            }
            // One message per field. While the confirmation is still empty, it is not a mismatch yet.
            readonly property var problems: view.controller.accountProblems(password.text, submitted || confirmation.text ? confirmation.text : password.text)
            function submit(): void {
                submitted = true;
                view.controller.stageAccount(password.text, confirmation.text);
                const first = [nameProblem, loginProblem, passwordProblem, confirmProblem, hostnameProblem].find(item => item.text !== "");
                if (first) {
                    // The line has just become visible; lay it out before scrolling to it.
                    account.forceLayout();
                    const flick = scroll.contentItem as Flickable;
                    const bottom = first.mapToItem(account, 0, first.height).y;
                    flick.contentY = Math.max(flick.contentY, bottom - flick.height, 0);
                }
            }
            Connections {
                target: view
                function onRequestPlan(): void {
                    account.submit();
                }
            }
            Binding {
                target: view
                property: "accountBlocked"
                value: view.controller.accountProblem(password.text, confirmation.text)
            }
            Binding {
                target: view
                property: "accountHint"
                value: {
                    const shown = {
                        name: !!account.touched.name,
                        login: !!account.touched.login,
                        password: !!password.text,
                        confirm: !!confirmation.text,
                        hostname: !!account.touched.hostname
                    };
                    const field = ["name", "login", "password", "confirm", "hostname"].find(key => account.problems[key] && !account.submitted && !shown[key]);
                    return field ? account.problems[field] : "";
                }
            }
            Connections {
                target: view.controller
                function onClearPasswords(): void {
                    account.submitted = false;
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
                uniformCellWidths: true
                Hint {
                    Layout.row: 0
                    Layout.column: 0
                    text: "Full name"
                }
                Hint {
                    Layout.row: 0
                    Layout.column: 1
                    text: "Login"
                }
                Field {
                    objectName: "fullName"
                    Layout.row: 1
                    Layout.column: 0
                    Component.onCompleted: forceActiveFocus()
                    Layout.fillWidth: true
                    text: view.controller.fullName
                    onTextEdited: {
                        view.controller.fullName = text;
                        account.touch("name");
                        if (!view.controller.loginEdited) {
                            view.controller.login = Protocol.loginFromName(text);
                            account.touch("login");
                        }
                    }
                }
                Field {
                    objectName: "loginField"
                    Layout.row: 1
                    Layout.column: 1
                    Layout.fillWidth: true
                    text: view.controller.login
                    onTextEdited: {
                        view.controller.login = text;
                        view.controller.loginEdited = true;
                        account.touch("login");
                    }
                }
                Hint {
                    id: nameProblem
                    objectName: "nameProblem"
                    Layout.row: 2
                    Layout.column: 0
                    Layout.fillWidth: true
                    visible: text !== ""
                    color: view.danger
                    text: account.submitted || account.touched.name ? account.problems.name : ""
                }
                Hint {
                    id: loginProblem
                    objectName: "loginProblem"
                    Layout.row: 2
                    Layout.column: 1
                    Layout.fillWidth: true
                    visible: text !== ""
                    color: view.danger
                    text: account.submitted || account.touched.login ? account.problems.login : ""
                }
                Hint {
                    Layout.row: 3
                    Layout.column: 0
                    text: "Password"
                    Layout.topMargin: 10
                }
                Hint {
                    Layout.row: 3
                    Layout.column: 1
                    text: "Confirm password"
                    Layout.topMargin: 10
                }
                // With one password for everything the account password also unlocks the disk.
                PasswordField {
                    id: password
                    objectName: "userPassword"
                    Layout.row: 4
                    Layout.column: 0
                    unlock: view.controller.encryption === "encrypted" && view.controller.encryptionPassword === "account"
                    Layout.fillWidth: true
                }
                PasswordField {
                    id: confirmation
                    objectName: "confirmPassword"
                    Layout.row: 4
                    Layout.column: 1
                    unlock: password.unlock
                    passwordLabel: "Confirm password"
                    Layout.fillWidth: true
                    onAccepted: account.submit()
                }
                Hint {
                    id: passwordProblem
                    objectName: "passwordProblem"
                    Layout.row: 5
                    Layout.column: 0
                    Layout.fillWidth: true
                    visible: text !== ""
                    color: view.danger
                    text: account.submitted || password.text ? account.problems.password : ""
                }
                Hint {
                    id: confirmProblem
                    objectName: "confirmProblem"
                    Layout.row: 5
                    Layout.column: 1
                    Layout.fillWidth: true
                    visible: text !== ""
                    color: view.danger
                    text: account.submitted || confirmation.text ? account.problems.confirm : ""
                }
                // The password is typed blind; the login screen starts with Caps Lock off.
                Hint {
                    objectName: "capsLock"
                    Layout.row: 6
                    Layout.column: 0
                    Layout.fillWidth: true
                    visible: view.capsLockShown
                    text: "Caps Lock is on"
                }
                Hint {
                    objectName: "numLock"
                    Layout.row: 7
                    Layout.column: 0
                    Layout.fillWidth: true
                    visible: view.numLockShown
                    text: view.numLockLine
                }
                // The text console types some characters with other keys (planner.console_unsafe_chars):
                // a warning, Continue stays available.
                Hint {
                    objectName: "consoleWarning"
                    Layout.row: 8
                    Layout.column: 0
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: Protocol.consoleWarning(view.controller.session.consoleChars, view.controller.layouts, password.text)
                }
                Hint {
                    Layout.row: 9
                    Layout.column: 0
                    text: "Computer name"
                    Layout.topMargin: 10
                }
                Field {
                    objectName: "hostnameField"
                    Layout.row: 10
                    Layout.column: 0
                    Layout.fillWidth: true
                    Layout.columnSpan: 2
                    text: view.controller.hostname
                    onTextEdited: {
                        view.controller.hostname = text;
                        account.touch("hostname");
                    }
                }
                Hint {
                    id: hostnameProblem
                    objectName: "hostnameProblem"
                    Layout.row: 11
                    Layout.column: 0
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    visible: text !== ""
                    color: view.danger
                    text: account.submitted || account.touched.hostname ? account.problems.hostname : ""
                }
            }
        }
    }
    Component {
        id: softwarePage
        Column {
            spacing: 20
            Heading {
                width: parent.width
                text: "Make it your desktop"
            }
            Lead {
                width: parent.width
                text: "Choose the apps to have ready when you first sign in. Both choices install from the USB, without internet."
            }
            Choice {
                objectName: "softwareRich"
                // The chosen option takes focus; the default does when nothing else is chosen.
                Component.onCompleted: if (view.controller.software !== "minimal")
                    forceActiveFocus()
                width: parent.width
                text: "Rich · Recommended"
                detail: "Everyday apps for office work, email, photos, music and video. Includes recording, printing, system utilities and media codecs."
                chosen: view.controller.software === "rich"
                onClicked: view.controller.software = "rich"
            }
            Choice {
                objectName: "softwareMinimal"
                Component.onCompleted: if (chosen)
                    forceActiveFocus()
                width: parent.width
                text: "Minimal"
                detail: "Dolphin for your files, Firefox for the web and kitty for the terminal. Add more apps whenever you need them."
                chosen: view.controller.software === "minimal"
                onClicked: view.controller.software = "minimal"
            }
            Hint {
                width: parent.width
                text: "You can add or remove apps later."
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
            // The keyboard line first: it names the layouts the login screen will use, and the
            // planner puts it near the end, below the fold of a 768-pixel screen.
            Repeater {
                model: view.controller.session.plan?.summary || []
                delegate: Copy {
                    required property string modelData
                    objectName: modelData.startsWith("Keyboard: ") ? "reviewKeyboard" : ""
                    visible: modelData.startsWith("Keyboard: ")
                    width: parent.width
                    text: "•  " + modelData
                }
            }
            Repeater {
                model: view.controller.session.plan?.summary || []
                delegate: Copy {
                    required property string modelData
                    visible: !modelData.startsWith("Keyboard: ")
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
                        text: view.controller.mode === "erase" ? "Everything on this disk will be deleted. This cannot be undone." : view.controller.mode === "alongside" ? Protocol.alongsideReview(view.controller.windowsPartition, view.controller.shrinkBytes) : "Formatted partitions will lose their data. Emaki will write system and account files to the assigned partitions."
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
                            objectName: phaseRow.current ? "phaseStatus" : ""
                            visible: phaseRow.current
                            text: view.controller.session.indeterminate ? Protocol.activityText(view.controller.session.activity) || "Working…" : view.controller.session.phasePct === null ? "Skipped" : Math.round(view.controller.session.phasePct) + "%"
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
            text: view.controller.media.length ? "Save the installation log to a removable medium:" : "To save the log, plug in a second USB stick and press Refresh."
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
            // What the worker reports as finished with a caveat, such as a partial online update.
            Copy {
                objectName: "doneWarnings"
                width: parent.width
                visible: text !== ""
                text: view.controller.session.doneWarnings.join("\n")
            }
            LogSave {
                width: parent.width
            }
        }
    }
    Component {
        id: errorPage
        Column {
            id: errorColumn
            spacing: 22
            // Plain words for the worker's error code; its own message moves to the details.
            readonly property var words: Protocol.errorPresentation(view.controller.session.error)
            Heading {
                width: parent.width
                text: "Installation stopped"
            }
            Lead {
                objectName: "errorSentence"
                width: parent.width
                text: errorColumn.words.sentence
                color: view.danger
            }
            Copy {
                objectName: "errorAction"
                width: parent.width
                text: errorColumn.words.action
                visible: text !== ""
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
            Hint {
                objectName: "errorCode"
                width: parent.width
                visible: !!view.controller.session.error?.code
                text: "Error code: " + (view.controller.session.error?.code || "")
            }
            LogSave {
                width: parent.width
            }
            Check {
                id: detailsToggle
                objectName: "errorDetailsToggle"
                visible: errorColumn.words.details !== "" || view.controller.session.logs.length > 0
                text: "Show details"
                onCheckedChanged: if (checked)
                    Qt.callLater(errorColumn.revealDetails)
            }
            // Bring the opened log into view, its newest lines first.
            function revealDetails(): void {
                errorColumn.forceLayout();
                const page = scroll.contentItem as Flickable;
                page.contentY = Math.max(page.contentY, detailsBox.y + detailsBox.height - page.height, 0);
                const lines = detailsBox.contentItem as Flickable;
                lines.contentY = Math.max(0, lines.contentHeight - lines.height);
            }
            Connections {
                // The page settles after the box opens: a newly shown scroll bar narrows it and the text rewraps.
                target: scroll.contentItem
                enabled: detailsToggle.checked
                function onContentHeightChanged(): void {
                    Qt.callLater(errorColumn.revealDetails);
                }
            }
            C.ScrollView {
                id: detailsBox
                objectName: "errorDetailsBox"
                width: parent.width
                height: 220
                visible: detailsToggle.visible && detailsToggle.checked
                C.TextArea {
                    objectName: "errorDetails"
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    color: view.ink
                    selectionColor: view.accent
                    font.family: "monospace"
                    font.pixelSize: 14
                    text: [errorColumn.words.details, view.controller.session.logs.join("\n")].filter(part => part !== "").join("\n\n")
                    background: Rectangle {
                        radius: 12
                        color: "#aaffffff"
                        border.width: 1
                        border.color: Shell.LiquidPalette.flatDropRim
                    }
                }
            }
        }
    }
}
