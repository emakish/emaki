// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell
import "file:@EMAKI_DATADIR@/shell" as Shell
import "ManagerLogic.js" as Logic

Rectangle {
    id: view
    required property var controller
    required property var programs
    property string wallpaperTexture: ""
    property real dpr: 1
    property url brandIconSource: "file:" + Shell.Platform.prefix + "/share/icons/hicolor/scalable/apps/emaki-programs-updates.svg"
    property string page: "browse"
    property string category: ""
    property string categoryName: ""
    property string query: ""
    readonly property bool packagesPage: programs.scope === "packages" && (page === "browse" || page === "search")
    readonly property var categoryRows: [
        {id:"Network", name:"Internet", icon:"applications-internet", color:"#7d9ab8"},
        {id:"Office", name:"Office", icon:"applications-office", color:"#e8b55a"},
        {id:"Graphics", name:"Graphics", icon:"applications-graphics", color:"#d2809a"},
        {id:"AudioVideo", name:"Audio & Video", icon:"applications-multimedia", color:"#e05a4f"},
        {id:"Game", name:"Games", icon:"applications-games", color:"#a8b86a"},
        {id:"Development", name:"Development", icon:"applications-development", color:"#827568"},
        {id:"Utility", name:"Utilities", icon:"applications-utilities", color:"#8ab8a8"}
    ]
    readonly property var picks: ["org.kde.krita", "md.obsidian.Obsidian", "net.supertuxkart.SuperTuxKart"]
    readonly property var featured: picks.map(id => programs.allPrograms.find(row => Logic.identity(row) === id.toLowerCase())).filter(Boolean)
    readonly property var regularResults: programs.programs.filter(row => row.kind !== "aur")
    readonly property var aurResults: programs.programs.filter(row => row.kind === "aur")
    radius: 16
    color: "transparent"
    clip: true
    focus: true
    function resetScroll(): void { body.contentItem.contentY = 0; packageList.positionViewAtBeginning(); }
    function reveal(item: var): void {
        if (!item) return;
        const flick = packagesPage ? packageList : body.contentItem;
        let parent = item.parent;
        while (parent && parent !== flick.contentItem) parent = parent.parent;
        if (!parent) return;
        const rect = item.mapToItem(flick.contentItem, 0, 0, item.width, item.height);
        if (rect.y < flick.contentY) flick.contentY = Math.max(0, rect.y - 8);
        else if (rect.y + rect.height > flick.contentY + flick.height)
            flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), rect.y + rect.height - flick.height + 8);
    }
    function present(): void { resetScroll(); programs.refresh(); }
    function clearTransactionMessage(): void {
        if (!programs.mutating) { programs.message = ""; programs.messageIsError = true; }
    }
    onPageChanged: clearTransactionMessage()
    onQueryChanged: clearTransactionMessage()
    function navigate(name: string): void {
        clearTransactionMessage();
        page = name; category = ""; query = ""; search.text = "";
        programs.selectCategory(""); resetScroll();
        if (name === "updates") controller.refreshRestorePoints();
    }
    function showProgram(row: var): void {
        if (programs.busy) return;
        programs.openProgram(row); page = "program"; resetScroll();
    }
    function showCategory(row: var): void {
        programs.selectScope("programs");
        page = "category"; category = row.id; categoryName = row.name;
        query = ""; search.text = ""; programs.selectCategory(row.id); resetScroll();
    }
    function shelf(category: string): var {
        const rows = Logic.filter(programs.allPrograms, "", category);
        const order = category === "Graphics" ? ["krita", "gimp", "inkscape", "blender"]
            : category === "Development" ? ["emacs", "godot", "neovim", "zed"] : [];
        function rank(row) {
            const index = order.indexOf(String(row.package || row.name).toLowerCase());
            return index < 0 ? order.length : index;
        }
        return rows.slice().sort((a, b) => rank(a) - rank(b) || String(a.name).localeCompare(String(b.name)))
            .map(row => Logic.identity(row) === "org.gimp.gimp" ? Object.assign({}, row, {name: "GIMP"}) : row);
    }
    function planDownload(): string {
        const rows = programs.plan?.transaction?.packages || [];
        if (!rows.length || rows.some(row => row.downloadSize === null || row.downloadSize === undefined)) return "Download size unknown";
        return Math.ceil(rows.reduce((total, row) => total + row.downloadSize, 0) / 1048576) + " MB to download in total";
    }
    readonly property var previewPlanRows: programs.planAction === "install"
        ? programs.planRows.filter(row => isPlanTarget(row)).concat(programs.planRows.filter(row => !isPlanTarget(row)))
        : programs.planRows
    function isPlanTarget(row: var): bool {
        const selected = programs.selectedEdition || {};
        return row.name === selected.package || row.name === selected.ref
            || selected.kind === "flatpak" && String(row.name).split("/")[1] === selected.id;
    }
    function source(row: var): string { return row.sourceLabel || (row.kind === "flatpak" ? "Flathub" : row.kind === "aur" ? "AUR" : "Arch"); }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Slash && !search.activeFocus) { search.forceActiveFocus(); event.accepted = true; }
        else if (event.key === Qt.Key_Escape) {
            if (programs.previewOpen && !programs.busy) programs.cancelPlan();
            else if (query) navigate("browse");
            else if (page === "program" || page === "category") navigate("browse");
            event.accepted = true;
        } else {
            const flick = packagesPage ? packageList : body.contentItem;
            let position = flick.contentY;
            if (event.key === Qt.Key_PageDown) position += flick.height * .9;
            else if (event.key === Qt.Key_PageUp) position -= flick.height * .9;
            else if (event.key === Qt.Key_Home && !search.activeFocus) position = 0;
            else if (event.key === Qt.Key_End && !search.activeFocus) position = flick.contentHeight;
            else return;
            flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), position));
            event.accepted = true;
        }
    }
    Connections {
        target: view.Window.window
        function onActiveFocusItemChanged(): void { view.reveal(view.Window.window.activeFocusItem); }
    }
    Shell.SettingsGlass { anchors.fill: parent; wallpaperTexture: view.wallpaperTexture; dpr: view.dpr }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 2
        spacing: 0
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 112
            Layout.minimumHeight: 112
            Layout.maximumHeight: 112
            color: Qt.rgba(1,246/255,240/255,.22)
            Item {
                anchors.fill: parent
                anchors.leftMargin: 22; anchors.rightMargin: 22
                RowLayout {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.verticalCenterOffset: -23
                    width: 260
                    spacing: 11
                    Image { source: view.brandIconSource; sourceSize.width: 32; sourceSize.height: 32; Layout.preferredWidth: 32; Layout.preferredHeight: 32 }
                    ManagerLabel { text: "Programs & Updates"; font.pixelSize: 15; font.weight: Font.DemiBold; wrapMode: Text.NoWrap }
                }
                Rectangle {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: -23
                    implicitWidth: tabRow.implicitWidth + 6
                    implicitHeight: 42
                    radius: 13
                    color: Qt.rgba(36/255,16/255,24/255,.065)
                    border.color: Qt.rgba(36/255,16/255,24/255,.14)
                    RowLayout {
                        id: tabRow
                        anchors.centerIn: parent
                        spacing: 2
                        Repeater {
                            model: [{id:"browse", name:"Browse"}, {id:"installed", name:"Installed"}, {id:"updates", name:"Updates"}]
                            ManagerButton {
                                required property var modelData
                                objectName: "tab-" + modelData.id
                                text: modelData.name
                                badge: modelData.id === "updates" ? (view.controller.data.updates || []).length : 0
                                primary: view.page === modelData.id || modelData.id === "browse" && ["program","category","search"].includes(view.page)
                                quiet: true
                                onClicked: view.navigate(modelData.id)
                            }
                        }
                    }
                }
                RowLayout {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 10
                    spacing: 8
                    Repeater {
                        model: [{id: "programs", name: "Programs"}, {id: "packages", name: "All packages"}]
                        ManagerButton {
                            required property var modelData
                            objectName: "scope-" + modelData.id
                            text: modelData.name
                            primary: view.programs.scope === modelData.id
                            quiet: true
                            Accessible.role: Accessible.RadioButton
                            Accessible.checkable: true
                            Accessible.checked: view.programs.scope === modelData.id
                            onClicked: {
                                view.programs.selectScope(modelData.id);
                                view.category = "";
                                view.page = view.query.trim() ? "search" : "browse";
                                view.resetScroll();
                            }
                        }
                    }
                    C.TextField {
                        id: search
                        objectName: "program-search"
                        Layout.preferredWidth: Math.min(300, view.width * .35)
                        implicitHeight: 36
                        leftPadding: 14
                        rightPadding: 32
                        font.family: "Noto Sans"
                        font.pixelSize: 14
                        color: "#241018"
                        placeholderText: view.programs.scope === "packages" ? "Search all packages" : "Search programs"
                        text: view.query
                        selectByMouse: true
                        background: Rectangle { radius: 12; color: Qt.rgba(1,1,1,.62); border.color: search.activeFocus ? "#e2733f" : Qt.rgba(36/255,16/255,24/255,.14) }
                        onTextEdited: {
                            view.query = text; view.category = "";
                            view.page = text.trim() ? "search" : "browse";
                            view.programs.search(text); view.resetScroll();
                        }
                        Keys.onEscapePressed: view.navigate("browse")
                        ManagerButton {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            width: 30; height: 30; padding: 0; leftPadding: 0; rightPadding: 0
                            visible: search.text !== ""
                            text: "×"; quiet: true
                            Accessible.name: "Clear"
                            onClicked: view.navigate("browse")
                        }
                    }
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(36/255,16/255,24/255,.14) }
        }
        ListView {
            id: packageList
            objectName: "package-list"
            visible: view.packagesPage
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            clip: true
            spacing: 8
            topMargin: 20
            bottomMargin: 26
            model: visible ? view.programs.packageRows : []
            reuseItems: true
            C.ScrollBar.vertical: C.ScrollBar {}
            header: Item {
                width: packageList.width
                height: packageHeading.implicitHeight
                ColumnLayout {
                    id: packageHeading
                    width: Math.min(968, packageList.width - 72)
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 10
                    ManagerLabel { text: "All packages"; font.pixelSize: 26; font.weight: Font.DemiBold }
                    RowLayout {
                        ManagerLabel { text: view.programs.packageTotal + (view.programs.packageTotal === 1 ? " package" : " packages"); color: "#866c70" }
                        ManagerButton { text: "Refresh"; quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh() }
                    }
                    ManagerLabel { visible: view.programs.message !== ""; text: view.programs.message; color: view.programs.messageIsError ? "#a01b45" : "#426448" }
                    Repeater {
                        model: view.programs.warnings
                        ManagerLabel { required property var modelData; text: modelData; color: "#866c70"; font.pixelSize: 12 }
                    }
                    ManagerLabel { visible: view.programs.busy; text: view.programs.mutating ? view.programs.output : "Loading packages…"; color: "#866c70" }
                    ManagerLabel { visible: view.programs.packagesReceived && !view.programs.packageRows.length; text: "Nothing found for “" + view.query + "”."; color: "#866c70" }
                    Item { implicitHeight: 6 }
                }
            }
            delegate: Item {
                id: packageDelegate
                required property var modelData
                width: packageList.width
                height: packageEntry.implicitHeight
                ProgramRow {
                    id: packageEntry
                    row: packageDelegate.modelData
                    packageRow: true
                    width: Math.min(968, packageList.width - 72)
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }
        }
        C.ScrollView {
            id: body
            visible: !view.packagesPage
            objectName: "managerBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            contentWidth: availableWidth
            contentHeight: content.implicitHeight + 26
            clip: true
            C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
            ColumnLayout {
                id: content
                width: Math.min(968, body.availableWidth - 72)
                x: (body.availableWidth - width) / 2
                y: 26
                spacing: 22
                ManagerLabel {
                    visible: view.programs.message !== "" && view.page !== "updates"
                    text: view.programs.message
                    color: view.programs.messageIsError ? "#a01b45" : "#426448"
                }
                Repeater {
                    model: view.page !== "updates" ? view.programs.warnings : []
                    ManagerLabel { required property var modelData; text: modelData; color: "#866c70"; font.pixelSize: 12 }
                }
                RowLayout {
                    visible: view.programs.busy && !view.programs.mutating && view.page !== "updates"
                    C.BusyIndicator { running: parent.visible; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                    ManagerLabel { text: view.programs.phase === "planning" ? "Checking what will change…" : "Loading programs…"; color: "#866c70" }
                }
                ManagerLabel {
                    visible: view.programs.mutating && view.page !== "program"
                    text: (view.programs.phase === "removing" ? "Removing…" : "Installing…") + "\n" + view.programs.output.split("\n").slice(-3).join("\n")
                }
                ColumnLayout {
                    visible: view.page === "browse" || view.page === "category"
                    Layout.fillWidth: true
                    spacing: 12
                    ManagerLabel { text: "CATEGORIES"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: view.categoryRows
                            C.AbstractButton {
                                id: categoryButton
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                implicitHeight: 90
                                focusPolicy: Qt.StrongFocus
                                Keys.onReturnPressed: clicked()
                                Keys.onEnterPressed: clicked()
                                onClicked: view.showCategory(modelData)
                                background: Rectangle {
                                    radius: 14
                                    color: categoryButton.hovered ? Qt.rgba(1,.98,.96,.6) : Qt.rgba(1,.98,.96,.42)
                                    border.color: categoryButton.activeFocus || view.category === categoryButton.modelData.id ? "#e2733f" : Qt.rgba(36/255,16/255,24/255,.14)
                                }
                                contentItem: ColumnLayout {
                                    spacing: 8
                                    Rectangle {
                                        Layout.alignment: Qt.AlignHCenter
                                        Layout.topMargin: 12
                                        implicitWidth: 36; implicitHeight: 36; radius: 10
                                        color: categoryButton.modelData.color
                                        Image { id: categoryImage; anchors.centerIn: parent; width: 23; height: 23; source: Quickshell.iconPath(categoryButton.modelData.icon, true) }
                                        Text { anchors.centerIn: parent; visible: categoryImage.status !== Image.Ready; text: ({Network:"◎", Office:"▤", Graphics:"◉", AudioVideo:"▶", Game:"⚄", Development:"‹/›", Utility:"⚒"})[categoryButton.modelData.id]; color: "#fffaf6"; font.pixelSize: 21 }
                                    }
                                    ManagerLabel { text: categoryButton.modelData.name; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; Layout.bottomMargin: 12 }
                                }
                            }
                        }
                    }
                }
                ColumnLayout {
                    visible: view.page === "browse"
                    Layout.fillWidth: true
                    spacing: 12
                    RowLayout {
                        ManagerLabel { text: "WORTH TRYING"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; Layout.fillWidth: false; font.letterSpacing: .8 }
                        ManagerLabel { text: "Picked by Emaki"; font.pixelSize: 12; color: "#aa8d8a" }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 14
                        Repeater {
                            model: view.featured
                            ProgramCard {
                                required property var modelData
                                required property int index
                                program: modelData
                                featured: true
                                featureColor: ["#bb6b90", "#8e74b0", "#df9d52"][index]
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                onClicked: view.showProgram(program)
                            }
                        }
                    }
                    ManagerLabel { visible: !view.featured.length && !view.programs.busy; text: "Our picks are not available right now. Try Refresh."; color: "#866c70" }
                    Repeater {
                        model: [{id:"Graphics", name:"Graphics"}, {id:"Development", name:"Development"}]
                        ColumnLayout {
                            id: shelf
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.topMargin: 10
                            spacing: 10
                            RowLayout {
                                ManagerLabel { text: shelf.modelData.name.toUpperCase(); font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                                ManagerButton { text: "See all " + view.shelf(shelf.modelData.id).length; quiet: true; onClicked: view.showCategory(shelf.modelData) }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                Repeater {
                                    model: view.shelf(shelf.modelData.id).slice(0,4)
                                    ProgramCard {
                                        required property var modelData
                                        program: modelData
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 1
                                        onClicked: view.showProgram(program)
                                    }
                                }
                            }
                        }
                    }
                    ManagerButton { text: "Refresh"; quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh() }
                }
                ColumnLayout {
                    visible: view.page === "category" || view.page === "search"
                    Layout.fillWidth: true
                    spacing: 12
                    ManagerLabel { text: view.page === "search" ? "Results for “" + view.query + "”" : view.categoryName; font.pixelSize: 26; font.weight: Font.DemiBold }
                    ManagerLabel { text: view.regularResults.length + (view.regularResults.length === 1 ? " program from Arch and Flathub" : " programs from Arch and Flathub") + (view.page === "category" ? ", by name" : ", " + view.aurResults.length + " from AUR"); color: "#866c70" }
                    Repeater {
                        model: view.page === "search" ? view.regularResults : []
                        ProgramRow { required property var modelData; row: modelData; Layout.fillWidth: true }
                    }
                    GridLayout {
                        visible: view.page === "category"
                        Layout.fillWidth: true
                        columns: 4
                        columnSpacing: 12
                        rowSpacing: 12
                        Repeater {
                            model: view.page === "category" ? view.regularResults : []
                            ProgramCard {
                                required property var modelData
                                program: modelData
                                Layout.preferredWidth: (content.width - 36) / 4
                                Layout.fillWidth: true
                                onClicked: view.showProgram(program)
                            }
                        }
                    }
                    ManagerLabel { visible: view.aurResults.length > 0; text: "FROM AUR"; color: "#866c70"; font.pixelSize: 12; font.weight: Font.DemiBold }
                    ManagerLabel { visible: view.aurResults.length > 0; text: "Shown only. Emaki does not install these; build them in a terminal."; color: "#866c70"; font.pixelSize: 13 }
                    Repeater {
                        model: view.page === "search" ? view.aurResults : []
                        ProgramRow { required property var modelData; row: modelData; Layout.fillWidth: true }
                    }
                    ManagerLabel {
                        visible: !view.programs.programs.length && !view.programs.busy
                        text: "Nothing found for “" + view.query + "”. Try another word, or a category on Browse."
                        horizontalAlignment: Text.AlignHCenter
                        color: "#866c70"
                        Layout.topMargin: 30
                    }
                }
                ColumnLayout {
                    visible: view.page === "installed"
                    Layout.fillWidth: true
                    spacing: 12
                    ManagerLabel { text: "Installed"; font.pixelSize: 26; font.weight: Font.DemiBold }
                    ManagerLabel { visible: view.programs.inventoryError !== ""; text: view.programs.inventoryError; color: "#a01b45" }
                    ManagerLabel { visible: view.programs.inventoryLoading; text: "Loading programs…"; color: "#866c70" }
                    ManagerLabel { visible: !view.programs.inventoryLoading && !view.programs.inventoryError; text: view.programs.installedPrograms.length + (view.programs.installedPrograms.length === 1 ? " program you can open or remove" : " programs you can open or remove"); color: "#866c70" }
                    Repeater {
                        model: view.page === "installed" ? view.programs.installedPrograms : []
                        ProgramRow { required property var modelData; row: modelData; installedRow: true; Layout.fillWidth: true }
                    }
                    ManagerLabel { text: "Parts of Emaki and of the system are not listed here. They cannot be removed from this window."; color: "#866c70"; font.pixelSize: 13; Layout.topMargin: 16 }
                }
                ManagerProgram { visible: view.page === "program"; Layout.fillWidth: true; controller: view.programs; onBackRequested: view.navigate("browse") }
                ManagerUpdates { visible: view.page === "updates"; Layout.fillWidth: true; controller: view.controller; programs: view.programs.allRows; launchEntries: view.programs.launchEntries || []; transactionBusy: view.programs.mutating }
                Item { implicitHeight: 40; Layout.fillWidth: true }
            }
        }
    }
    Rectangle { anchors.fill: parent; radius: 16; color: "transparent"; border.width: 2; border.color: Qt.rgba(226/255,115/255,63/255,.62) }
    component ProgramRow: Rectangle {
        id: rowItem
        required property var row
        property bool installedRow: false
        objectName: packageRow ? "package-row-" + row.package : ""
        property bool packageRow: false
        implicitHeight: 76
        radius: 16
        color: Qt.rgba(1,.98,.96,.42)
        border.color: Qt.rgba(36/255,16/255,24/255,.14)
        RowLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 14
            ProgramIcon { program: rowItem.row; Layout.preferredWidth: 38; Layout.preferredHeight: 38 }
            C.AbstractButton {
                Layout.fillWidth: true
                implicitHeight: 50
                focusPolicy: Qt.StrongFocus
                Keys.onReturnPressed: clicked()
                Keys.onEnterPressed: clicked()
                enabled: !rowItem.packageRow || !!rowItem.row.programKey
                onClicked: {
                    if (rowItem.packageRow) {
                        if (view.programs.busy) return;
                        view.programs.openPackage(rowItem.row); view.page = "program"; view.resetScroll();
                    } else view.showProgram(rowItem.row);
                }
                contentItem: ColumnLayout {
                    spacing: 3
                    ManagerLabel { text: rowItem.row.name || ""; font.weight: Font.DemiBold; maximumLineCount: 1; elide: Text.ElideRight }
                    ManagerLabel { text: [rowItem.row.summary || "", rowItem.row.preinstalled ? "Came with Emaki" : ""].filter(Boolean).join(" · "); visible: text !== ""; color: "#866c70"; font.pixelSize: 12; maximumLineCount: 1; elide: Text.ElideRight }
                }
                background: Rectangle { color: "transparent"; radius: 8; border.width: 2; border.color: parent.activeFocus ? "#e2733f" : "transparent" }
            }
            ManagerLabel { text: rowItem.packageRow ? rowItem.row.repository : view.source(rowItem.row); Layout.fillWidth: false; font.pixelSize: 12; color: "#866c70" }
            ManagerLabel { visible: rowItem.installedRow || rowItem.packageRow; text: rowItem.row.installedVersion || rowItem.row.version || ""; Layout.fillWidth: false; font.pixelSize: 12; color: "#866c70" }
            ManagerLabel { visible: !rowItem.installedRow && rowItem.row.kind === "aur"; text: "Build in a terminal"; Layout.fillWidth: false; font.pixelSize: 12; color: "#866c70" }
            ManagerLabel { visible: rowItem.packageRow && rowItem.row.installed; text: "Installed"; Layout.fillWidth: false; font.pixelSize: 12; color: "#c2481f" }
            ManagerButton {
                visible: rowItem.installedRow
                text: "Open"
                enabled: !view.programs.busy
                onClicked: { view.programs.openProgram(rowItem.row); view.programs.openInstalled(); }
            }
            ManagerButton {
                visible: rowItem.installedRow || rowItem.packageRow && rowItem.row.installed
                text: "Remove"; danger: true
                enabled: !view.programs.busy && !view.programs.externalBusy && (rowItem.row.actions || []).includes("remove")
                onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "remove") : view.programs.prepareRemove(rowItem.row)
            }
            ManagerButton {
                visible: !rowItem.installedRow && rowItem.row.kind !== "aur" && (!rowItem.packageRow || !rowItem.row.installed)
                text: rowItem.row.installed ? "Installed" : "Install"
                enabled: !view.programs.busy && (!rowItem.packageRow || !view.programs.externalBusy && (rowItem.row.actions || []).includes("install"))
                onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "install") : view.showProgram(rowItem.row)
            }
        }
    }
    C.Popup {
        id: preview
        objectName: "program-preview"
        parent: view
        anchors.centerIn: parent
        width: Math.min(470, view.width - 40)
        height: Math.min(previewColumn.implicitHeight + 48, view.height - 40)
        padding: 24
        modal: true
        focus: true
        closePolicy: C.Popup.NoAutoClose
        visible: view.programs.previewOpen && !view.programs.mutating
        background: Rectangle { radius: 18; color: "#fff9f5"; border.width: 2; border.color: "#e2733f" }
        C.Overlay.modal: Rectangle { color: Qt.rgba(36/255,16/255,24/255,.26) }
        ColumnLayout {
            id: previewColumn
            anchors.fill: parent
            spacing: 14
            ManagerLabel { text: view.programs.planAction === "remove" ? "Remove " + (view.programs.selectedEdition?.name || "") + "?" : view.programs.selectedEdition?.kind === "arch" ? "Installing also brings the system up to date." : "Install " + (view.programs.selectedEdition?.name || "") + "?"; font.pixelSize: 21; font.weight: Font.DemiBold }
            ManagerLabel { text: view.programs.message; visible: text !== ""; color: "#a01b45" }
            ManagerLabel { text: view.programs.phase === "planning" ? "Checking what will change…" : view.programs.planAction === "remove" ? (view.programs.planRows.length === 1 ? "This goes:" : "These " + view.programs.planRows.length + " packages go:") : view.programs.planRows.length === 1 ? "This changes:" : "These " + view.programs.planRows.length + " packages change:" }
            ManagerLabel {
                visible: view.programs.planAction === "install" && view.programs.selectedEdition?.kind === "arch" && view.programs.phase !== "planning"
                text: view.planDownload()
                color: "#866c70"; font.pixelSize: 13
            }
            C.ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(210, previewRows.implicitHeight)
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    id: previewRows
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: view.previewPlanRows
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ManagerLabel { text: parent.modelData.name || ""; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                            ManagerLabel {
                                visible: view.programs.planAction === "install" && view.isPlanTarget(parent.modelData)
                                text: parent.modelData.detail || ""
                                Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12
                            }
                            ManagerLabel {
                                text: view.isPlanTarget(parent.modelData) ? "the program" : view.programs.planAction === "remove" && view.programs.selectedEdition?.kind !== "flatpak" ? "nothing else needs it" : parent.modelData.detail || ""
                                Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12
                            }
                        }
                    }
                }
            }
            ManagerLabel {
                visible: view.programs.planAction === "remove"
                text: view.programs.selectedEdition?.kind === "flatpak" ? "Your files stay. The program’s own settings stay too, so they are back if you install it again." : "Your files and the program’s settings in your home folder stay as they are."
                color: "#866c70"; font.pixelSize: 13
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                ManagerButton { text: "Cancel"; enabled: !view.programs.busy; onClicked: view.programs.cancelPlan() }
                ManagerButton {
                    objectName: "confirm-program-operation"
                    text: view.programs.planAction === "remove" ? "Remove" : "Install"
                    primary: true; danger: view.programs.planAction === "remove"
                    enabled: !view.programs.busy && !view.programs.externalBusy && view.programs.plan?.ok === true && !!view.programs.plan?.token
                    onClicked: { if (view.programs.planAction === "install") { view.page = "program"; view.resetScroll(); } view.programs.executePlan(); }
                }
            }
        }
    }
}
