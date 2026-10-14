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
    // Pages: browse, search, category, list (a shelf's "See all"), program, aur, installed, updates.
    property string page: "browse"
    property string category: ""
    property string categoryName: ""
    property string query: ""
    property string listId: ""
    property string installedQuery: ""
    // Where the open program page came from; its back control returns there.
    property var returnTo: ({page: "browse", label: "Browse", tab: "browse"})
    // The AUR's warning was accepted in this window (or it asked not to be shown again).
    property bool aurAcknowledged: false
    // Each tab owns its top: Browse its catalog search and categories, AUR and Installed their own search.
    readonly property string tab: page === "program" ? (returnTo.tab || "browse")
        : ["aur", "installed", "updates"].includes(page) ? page : "browse"
    readonly property bool packagesPage: programs.scope === "packages" && (page === "browse" || page === "search")
    // Pages that are one long list scroll in a ListView that creates only the rows in sight.
    readonly property bool listPage: !packagesPage && ["category", "list", "search", "aur", "installed"].includes(page)
    readonly property var pageRows: page === "category" || page === "search" ? regularResults : page === "list" ? lists[listId]?.rows || []
        : page === "aur" ? (aurOpen ? aurList : []) : page === "installed" ? installedList : []
    readonly property bool aurOpen: programs.aurEnabled && (aurAcknowledged || !programs.aurShowWarning)
    readonly property int gutter: 28
    readonly property var categoryRows: [
        {id: "Network", name: "Internet"}, {id: "Office", name: "Office"}, {id: "Graphics", name: "Graphics"},
        {id: "AudioVideo", name: "Audio & Video"}, {id: "Game", name: "Games"}, {id: "Development", name: "Development"},
        {id: "Education", name: "Education"}, {id: "System", name: "System"}, {id: "Utility", name: "Utilities"}
    ]
    // Picks are data (update-manager/storefront.json, read by the engine), never ids written here.
    readonly property var storefront: programs.storefront
    // A pick the catalog has no metadata for (its name is only its ID) is not shown.
    readonly property var featured: (storefront.featured?.programs || []).map(id => programs.allPrograms.find(row => Logic.identity(row) === id)).filter(row => !!row && !!row.summary && String(row.name).toLowerCase() !== Logic.identity(row)).map(row => shortName(row))
    readonly property bool popularMeasured: Logic.hasPopularity(programs.allPrograms)
    // A list page draws every row; the first hundred are what a "most used" list is for.
    readonly property var popular: Logic.popular(programs.allPrograms).slice(0, 100).map(row => shortName(row))
    readonly property var essentials: programs.essentials.minimal ? Logic.essentials(programs.allPrograms, programs.essentials.programs).map(row => shortName(row)) : []
    readonly property var fresh: Logic.fresh(programs.allPrograms).map(row => shortName(row))
    readonly property var lists: ({
        popular: {title: "Popular", rows: popular},
        essentials: {title: "Essentials", rows: essentials},
        fresh: {title: "New and updated", rows: fresh}
    })
    readonly property var regularResults: programs.programs.filter(row => row.kind !== "aur")
    readonly property var aurList: programs.aurQuery.trim().length >= 2 ? programs.aurRows : Logic.byVotes(programs.aurPopular)
    readonly property var selfRow: ({key: "self:emaki-update-manager", id: "emaki-update-manager", package: "emaki-update-manager",
        name: "Programs & Updates", summary: "This window", kind: "arch", installed: true, self: true,
        icon: String(brandIconSource), actions: []})
    readonly property var installedList: Logic.installedWithSelf(programs.installedPrograms, installedQuery, selfRow)
    radius: 16
    color: "transparent"
    clip: true
    focus: true
    function resetScroll(): void { body.contentItem.contentY = 0; packageList.positionViewAtBeginning(); rowList.positionViewAtBeginning(); }
    function scroller(): var { return packagesPage ? packageList : listPage ? rowList : body.contentItem; }
    function reveal(item: var): void {
        if (!item) return;
        const flick = scroller();
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
    // A window reopened on a list or program page it cannot rebuild starts on Browse.
    Component.onCompleted: if (page === "list" && !lists[listId] || page === "program" && !programs.selectedEdition) page = "browse"
    onQueryChanged: clearTransactionMessage()
    function navigate(name: string): void {
        clearTransactionMessage();
        page = name; category = ""; categoryName = ""; query = ""; listId = ""; search.text = "";
        // The AUR warning shows again on the next visit until the person turns it off.
        if (name !== "aur") aurAcknowledged = false;
        programs.selectCategory(""); resetScroll();
        if (name === "updates") controller.refreshRestorePoints();
        if (name === "aur") enterAur();
    }
    function enterAur(): void {
        if (!programs.aurKnown || !programs.aurEnabled) return;
        if (!aurOpen) { aurWarning.dontShow = false; aurWarning.open(); return; }
        programs.readPopular();
    }
    function place(): var {
        if (page === "category") return {page: page, category: category, categoryName: categoryName, label: categoryName, tab: "browse"};
        if (page === "search") return {page: page, query: query, label: "Results", tab: "browse"};
        if (page === "list") return {page: page, listId: listId, label: lists[listId]?.title || "Browse", tab: "browse"};
        if (page === "aur") return {page: page, label: "AUR", tab: "aur"};
        if (page === "installed") return {page: page, label: "Installed", tab: "installed"};
        if (page === "program") return returnTo;
        return {page: "browse", label: "Browse", tab: "browse"};
    }
    function showProgram(row: var): void {
        if (programs.busy || !row || row.self) return;
        returnTo = place();
        programs.openProgram(row); page = "program"; resetScroll();
    }
    function installProgram(row: var): void {
        // Install from a list: the program's page opens behind its install preview.
        if (programs.busy || programs.externalBusy) return;
        showProgram(row);
        programs.prepareInstall();
    }
    function goBack(): void {
        const back = returnTo;
        clearTransactionMessage();
        if (back.page === "category") showCategory({id: back.category, name: back.categoryName});
        else if (back.page === "search") { page = "search"; query = back.query; search.text = back.query; }
        else if (back.page === "list") showList(back.listId);
        else if (back.page === "aur" || back.page === "installed") page = back.page;
        // Back to the All packages list keeps it as it was; navigate() would read it again.
        else if (back.page === "browse" && programs.scope === "packages") page = "browse";
        else navigate("browse");
        resetScroll();
    }
    function showCategory(row: var): void {
        programs.selectScope("programs");
        page = "category"; category = row.id; categoryName = row.name; listId = "";
        query = ""; search.text = ""; programs.selectCategory(row.id); resetScroll();
    }
    function showList(id: string): void {
        page = "list"; listId = id; category = ""; query = ""; search.text = ""; resetScroll();
    }
    function shortName(row: var): var {
        const name = (storefront.names || {})[Logic.identity(row)];
        return name ? Object.assign({}, row, {name: name}) : row;
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
    readonly property string noSnapshotsText: "This computer does not support system snapshots, so AUR installs are unavailable. Emaki needs a snapshot before installing from the AUR. You can still browse."
    function source(row: var): string { return row.sourceLabel || (row.kind === "flatpak" ? "Flathub" : row.kind === "aur" ? "AUR" : "Arch"); }
    function count(value: int, one: string, many: string): string { return value.toLocaleString(Qt.locale("en_US"), "f", 0) + " " + (value === 1 ? one : many); }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Slash && !(Window.activeFocusItem instanceof TextInput)) {
            const field = tab === "aur" ? aurSearch : tab === "installed" ? installedSearch : search;
            if (field.visible) { field.forceActiveFocus(); event.accepted = true; }
        } else if (event.key === Qt.Key_Escape) {
            if (programs.previewOpen && !programs.busy) programs.cancelPlan();
            else if (page === "program" && !programs.mutating) goBack();
            else if (page === "search" || page === "category" || page === "list") navigate("browse");
            event.accepted = true;
        } else {
            const flick = scroller();
            let position = flick.contentY;
            const typing = Window.activeFocusItem instanceof TextInput;
            if (event.key === Qt.Key_PageDown) position += flick.height * .9;
            else if (event.key === Qt.Key_PageUp) position -= flick.height * .9;
            else if (event.key === Qt.Key_Home && !typing) position = 0;
            else if (event.key === Qt.Key_End && !typing) position = flick.contentHeight;
            else return;
            flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), position));
            event.accepted = true;
        }
    }
    Connections {
        target: view.Window.window
        function onActiveFocusItemChanged(): void { view.reveal(view.Window.window.activeFocusItem); }
    }
    Connections {
        target: view.programs
        function onAurEnabledChanged(): void { if (view.page === "aur" && view.programs.aurEnabled && view.aurOpen) view.programs.readPopular(); }
    }
    Shell.SettingsGlass { anchors.fill: parent; wallpaperTexture: view.wallpaperTexture; dpr: view.dpr }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 2
        spacing: 0
        Rectangle {
            objectName: "tab-bar"
            Layout.fillWidth: true
            implicitHeight: 62
            color: Qt.rgba(1,246/255,240/255,.22)
            RowLayout {
                id: tabRow
                anchors { left: parent.left; leftMargin: view.gutter - 6; verticalCenter: parent.verticalCenter }
                spacing: 4
                Repeater {
                    model: [{id: "browse", name: "Browse"}, {id: "aur", name: "AUR"}, {id: "installed", name: "Installed"}, {id: "updates", name: "Updates"}]
                    TabButton {
                        required property var modelData
                        objectName: "tab-" + modelData.id
                        text: modelData.name
                        selected: view.tab === modelData.id
                        badge: modelData.id === "updates" ? (view.controller.data.updates || []).length : 0
                        onClicked: view.navigate(modelData.id)
                    }
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(36/255,16/255,24/255,.14) }
        }
        ColumnLayout {
            // Browse's own top: the catalog search (Arch and Flathub) and the category strip.
            objectName: "browse-top"
            Layout.fillHeight: false
            visible: view.page === "browse" || view.page === "search"
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 20
            spacing: 14
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                SearchField {
                    id: search
                    objectName: "program-search"
                    Layout.fillWidth: true
                    placeholderText: view.programs.scope === "packages" ? "Search all packages" : "Search programs"
                    text: view.query
                    onTextEdited: {
                        view.query = text; view.category = "";
                        view.page = text.trim() ? "search" : "browse";
                        view.programs.search(text); view.resetScroll();
                    }
                    Keys.onEscapePressed: view.navigate("browse")
                    onCleared: view.navigate("browse")
                }
                ManagerButton {
                    // Every Arch package, not only programs with a window.
                    objectName: "scope-packages"
                    text: "All packages"
                    quiet: view.programs.scope !== "packages"
                    primary: view.programs.scope === "packages"
                    Accessible.role: Accessible.CheckBox
                    Accessible.checkable: true
                    Accessible.checked: view.programs.scope === "packages"
                    onClicked: {
                        view.programs.selectScope(view.programs.scope === "packages" ? "programs" : "packages");
                        view.category = "";
                        view.page = view.query.trim() ? "search" : "browse";
                        view.resetScroll();
                    }
                }
            }
            Flow {
                objectName: "category-strip"
                visible: view.programs.scope === "programs"
                Layout.fillWidth: true
                spacing: 8
                Chip { objectName: "category-all"; text: "All"; selected: view.page === "browse"; onClicked: view.navigate("browse") }
                Repeater {
                    model: view.categoryRows
                    Chip {
                        required property var modelData
                        objectName: "category-" + modelData.id
                        text: modelData.name
                        selected: view.category === modelData.id
                        onClicked: view.showCategory(modelData)
                    }
                }
            }
        }
        RowLayout {
            // The AUR's own top: its search, and the stripe that stays on every AUR list.
            objectName: "aur-top"
            Layout.fillHeight: false
            visible: view.page === "aur" && view.aurOpen
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 20
            spacing: 10
            SearchField {
                id: aurSearch
                objectName: "aur-search"
                Layout.fillWidth: true
                placeholderText: "Search the AUR"
                enabled: view.programs.aurEnabled
                // A recreated window shows the search its results still follow.
                text: view.programs.aurQuery
                onTextEdited: view.programs.searchAur(text)
                onCleared: view.programs.searchAur("")
            }
            ManagerButton {
                objectName: "close-aur"
                text: "Close the AUR section"
                quiet: true
                enabled: view.programs.aurEnabled && !view.programs.mutating
                onClicked: { view.programs.setAur(false, true); aurSearch.text = ""; view.aurAcknowledged = false; view.navigate("browse"); }
            }
        }
        AurStripe {
            objectName: "aur-stripe"
            visible: view.aurOpen && view.page === "aur"
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 12
        }
        SearchField {
            // Installed's own top.
            id: installedSearch
            objectName: "installed-search"
            visible: view.page === "installed"
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 20
            placeholderText: "Search installed"
            onTextEdited: { view.installedQuery = text; view.resetScroll(); }
            onCleared: view.installedQuery = ""
        }
        ColumnLayout {
            // The way back and the store's notices stay above the scrolling list.
            objectName: "pinned"
            Layout.fillHeight: false
            visible: !view.packagesPage && view.page !== "updates"
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter
            spacing: 0
            BackLink {
                Layout.topMargin: 10
                objectName: "back"
                visible: view.page === "program" || view.page === "category" || view.page === "list"
                text: "‹  " + (view.page === "program" ? view.returnTo.label || "Browse" : "Browse")
                onClicked: view.page === "program" ? view.goBack() : view.navigate("browse")
            }
            ManagerLabel {
                Layout.topMargin: 10
                objectName: "program-message"
                visible: view.programs.message !== "" && view.page !== "updates" && !view.programs.previewOpen
                text: view.programs.message
                color: view.programs.messageIsError ? "#a01b45" : "#426448"
            }
            ManagerButton {
                Layout.topMargin: 10
                text: "Restore before this install…"
                visible: !!view.programs.recoveryPoint && view.page !== "updates"
                enabled: !view.programs.busy && !view.controller.busy && !view.controller.restorePending
                onClicked: updatesPage.confirmRestore(view.programs.recoveryPoint)
            }
            Repeater {
                model: view.page !== "updates" && view.page !== "installed" ? view.programs.warnings : []
                ManagerLabel { required property var modelData; text: modelData; color: "#866c70"; font.pixelSize: 12; Layout.topMargin: 6 }
            }
            RowLayout {
                Layout.topMargin: 10
                visible: view.programs.busy && !view.programs.mutating && view.page !== "updates" && view.page !== "installed"
                C.BusyIndicator { running: parent.visible; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                ManagerLabel { text: view.programs.phase === "planning" ? "Checking what will change…" : "Loading programs…"; color: "#866c70" }
            }
        }
        ColumnLayout {
            // The page's heading and messages stay above its rows.
            objectName: "page-head"
            Layout.fillHeight: false
            visible: view.listPage
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 12
            spacing: 0
            ColumnLayout {
                // A shelf's "See all": the whole list.
                objectName: "list-page"
                visible: view.page === "list"
                Layout.fillWidth: true
                spacing: 0
                Heading { title: view.lists[view.listId]?.title || ""; note: view.count((view.lists[view.listId]?.rows || []).length, "program", "programs") }
            }
            ColumnLayout {
                objectName: "category-page"
                visible: view.page === "category"
                Layout.fillWidth: true
                spacing: 0
                Heading { title: view.categoryName; note: view.count(view.regularResults.length, "program", "programs") }
                ManagerLabel {
                    objectName: "category-empty"
                    visible: view.page === "category" && !view.regularResults.length && !view.programs.busy
                    text: view.programs.allPrograms.length ? "There are no programs in " + view.categoryName + " yet."
                        : "There are no programs here yet: the catalog could not be loaded. Check the internet connection, then Refresh on Browse."
                    color: "#866c70"
                    Layout.topMargin: 12
                }
            }
            ColumnLayout {
                objectName: "search-page"
                visible: view.page === "search"
                Layout.fillWidth: true
                spacing: 0
                Heading { title: "Results for “" + view.query + "”"; note: view.count(view.regularResults.length, "program from Arch and Flathub", "programs from Arch and Flathub") }
            }
            ColumnLayout {
                objectName: "aur-page"
                visible: view.page === "aur"
                Layout.fillWidth: true
                spacing: 0
                ManagerLabel {
                    // Decision of 2026-10-13: AUR installs are refused without system snapshots (ext4).
                    objectName: "aur-no-snapshots"
                    visible: view.programs.aurKnown && !view.programs.aurSnapshots
                    text: view.noSnapshotsText
                    color: "#a01b45"; font.pixelSize: 13; font.weight: Font.DemiBold
                    Layout.bottomMargin: 12
                }
                Rectangle {
                    objectName: "aur-entry"
                    visible: !view.aurOpen
                    Layout.fillWidth: true
                    implicitHeight: aurEntry.implicitHeight + 32
                    radius: 16
                    color: Qt.rgba(1,.98,.96,.42)
                    border.color: Qt.rgba(36/255,16/255,24/255,.14)
                    RowLayout {
                        id: aurEntry
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 18 }
                        spacing: 16
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            ManagerLabel { text: "AUR — programs from the community"; font.weight: Font.DemiBold }
                            ManagerLabel {
                                text: view.programs.aurEnabled ? "Open. Anyone can publish there and nobody checks it, neither Emaki nor Arch."
                                    : "What Arch and Flathub do not have, published by other people. Closed until you read what it means and open it."
                                color: "#866c70"; font.pixelSize: 13
                            }
                        }
                        ManagerButton {
                            objectName: "open-aur"
                            text: "Open the AUR…"
                            primary: true
                            enabled: view.programs.aurKnown
                            onClicked: { aurWarning.dontShow = false; aurWarning.open(); }
                        }
                    }
                }
                ManagerLabel { visible: !view.programs.aurKnown || view.programs.phase === "aur"; text: "Opening the AUR…"; color: "#866c70" }
                Heading {
                    objectName: "aur-popular"
                    visible: view.aurOpen && view.programs.aurQuery.trim().length < 2
                    title: "Popular on the AUR"
                    note: "By votes"
                }
                ManagerLabel {
                    visible: view.aurOpen && view.programs.aurQuery.trim().length < 2 && view.programs.aurPopularLoaded && !view.programs.aurPopular.length && view.programs.phase !== "aurPopular"
                    text: "The list could not be loaded. Search still works."
                    color: "#866c70"
                }
                Heading {
                    visible: view.aurOpen && view.programs.aurQuery.trim().length >= 2
                    title: "Results for “" + view.programs.aurQuery.trim() + "”"
                    note: view.programs.aurSearched ? view.count(view.programs.aurRows.length, "package", "packages") : "Searching…"
                }
                ManagerLabel {
                    visible: view.aurOpen && view.programs.aurSearched && !view.programs.aurRows.length && view.programs.aurQuery.trim().length >= 2
                    text: "Nothing found in the AUR for “" + view.programs.aurQuery + "”."
                    color: "#866c70"
                }
            }
            ColumnLayout {
                objectName: "installed-page"
                visible: view.page === "installed"
                Layout.fillWidth: true
                spacing: 0
                ManagerLabel { visible: view.programs.inventoryError !== ""; text: view.programs.inventoryError; color: "#a01b45" }
                ManagerLabel { visible: view.programs.inventoryLoading; text: "Loading programs…"; color: "#866c70" }
                Heading { title: "Installed"; note: view.count(view.installedList.length, "program", "programs") }
            }
            ManagerLabel {
                visible: view.page === "search" && !view.regularResults.length && !view.programs.busy
                text: "Nothing found for “" + view.query + "”. Try another word, or look in the AUR tab."
                color: "#866c70"
                Layout.topMargin: 24
            }
            ManagerLabel {
                visible: view.page === "installed" && view.installedQuery.trim() !== "" && !view.installedList.length
                text: "Nothing installed matches “" + view.installedQuery.trim() + "”."
                color: "#866c70"
                Layout.topMargin: 12
            }
        }
        ListView {
            // Category, See all, search, AUR and Installed: hundreds of rows, drawn only where they are seen.
            id: rowList
            objectName: "row-list"
            visible: view.listPage
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            clip: true
            topMargin: 4
            bottomMargin: 26
            model: visible ? view.pageRows : []
            C.ScrollBar.vertical: C.ScrollBar {}
            delegate: Item {
                id: rowDelegate
                required property var modelData
                width: rowList.width
                height: 68
                ProgramRow {
                    row: rowDelegate.modelData
                    installedRow: view.page === "installed"
                    x: view.gutter
                    width: rowList.width - 2 * view.gutter
                    height: 68
                }
            }
        }
        ManagerLabel {
            visible: view.page === "installed"
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.bottomMargin: 14
            text: "Programs marked “Part of Emaki” keep the system working and cannot be removed here."; color: "#866c70"; font.pixelSize: 13
        }
        ListView {
            id: packageList
            objectName: "package-list"
            visible: view.packagesPage
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            clip: true
            spacing: 0
            topMargin: 18
            bottomMargin: 26
            model: visible ? view.programs.packageRows : []
            reuseItems: true
            C.ScrollBar.vertical: C.ScrollBar {}
            header: Item {
                width: packageList.width
                height: packageHeading.implicitHeight
                ColumnLayout {
                    id: packageHeading
                    x: view.gutter
                    width: packageList.width - 2 * view.gutter
                    spacing: 10
                    RowLayout {
                        ManagerLabel { text: "All packages"; font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: false }
                        ManagerLabel { text: view.count(view.programs.packageTotal, "package", "packages"); color: "#866c70"; font.pixelSize: 13 }
                        ManagerButton { text: "Refresh"; quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh() }
                    }
                    ManagerLabel { visible: view.programs.message !== ""; text: view.programs.message; color: view.programs.messageIsError ? "#a01b45" : "#426448" }
                    Repeater {
                        model: view.programs.warnings
                        ManagerLabel { required property var modelData; text: modelData; color: "#866c70"; font.pixelSize: 12 }
                    }
                    ManagerLabel { visible: view.programs.busy; text: view.programs.mutating ? view.programs.output.split("\n").slice(-1)[0] : "Loading packages…"; color: "#866c70" }
                    ManagerLabel { visible: view.programs.packagesReceived && !view.programs.packageRows.length; text: "Nothing found for “" + view.query + "”."; color: "#866c70" }
                    Item { implicitHeight: 4 }
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
                    x: view.gutter
                    width: packageList.width - 2 * view.gutter
                }
            }
        }
        C.ScrollView {
            id: body
            // A gap under the pinned tops, so a scrolled row is not cut flush against them.
            Layout.topMargin: 8
            visible: !view.packagesPage && !view.listPage
            objectName: "managerBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            contentWidth: availableWidth
            contentHeight: content.implicitHeight + 40
            clip: true
            C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
            ColumnLayout {
                id: content
                objectName: "manager-content"
                // The content fills the window's width; no narrow column with empty fields beside it.
                x: view.gutter
                y: 12
                width: body.availableWidth - 2 * view.gutter
                spacing: 18
                ManagerLabel {
                    visible: view.programs.mutating && view.page !== "program" && view.page !== "updates"
                    text: view.programs.phase === "removing" ? "Removing…" : view.programs.phase === "undoing" ? "Undoing the install…" : "Installing…"
                    font.weight: Font.DemiBold
                }
                ColumnLayout {
                    // Browse's start screen.
                    objectName: "browse-page"
                    visible: view.page === "browse"
                    Layout.fillWidth: true
                    spacing: 22
                    Shelf {
                        objectName: "shelf-picked"
                        title: view.storefront.featured?.title || ""
                        note: view.storefront.featured?.note || ""
                        visible: view.featured.length > 0
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12
                            Repeater {
                                model: view.featured
                                ProgramCard {
                                    required property var modelData
                                    required property int index
                                    program: modelData
                                    featured: index === 0
                                    installEnabled: !view.programs.busy && !view.programs.externalBusy && (modelData.actions || []).includes("install")
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: index === 0 ? 14 : 10
                                    Layout.fillHeight: true
                                    onClicked: view.showProgram(program)
                                    onInstallRequested: view.installProgram(program)
                                }
                            }
                        }
                    }
                    ManagerLabel { visible: (view.storefront.featured?.programs || []).length > 0 && !view.featured.length && !view.programs.busy; text: "Our picks are not available right now. Try Refresh."; color: "#866c70" }
                    Shelf {
                        objectName: "shelf-popular"
                        title: "Popular"
                        note: view.popularMeasured ? "Most installed this month" : ""
                        more: view.popular.length > 4 ? "popular" : ""
                        visible: view.popular.length > 0
                        CardRow { rows: view.popular.slice(0, 4) }
                    }
                    Shelf {
                        objectName: "shelf-essentials"
                        title: "Essentials"
                        note: "What the full install adds"
                        visible: view.essentials.length > 0
                        CardRow { rows: view.essentials.slice(0, 3); allCount: view.essentials.length > 3 ? view.essentials.length : 0; allList: "essentials" }
                    }
                    Shelf {
                        objectName: "shelf-fresh"
                        title: "New and updated"
                        more: view.fresh.length > 4 ? "fresh" : ""
                        visible: view.fresh.length > 0
                        CardRow { rows: view.fresh.slice(0, 4) }
                    }
                    Shelf {
                        // Emaki's own optional packages; which ones is data (update-manager/emaki-shelf.json).
                        objectName: "emaki-shelf"
                        title: "From Emaki"
                        note: "Emaki’s own extras. Each installs with one button."
                        visible: view.programs.shelf.length > 0
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 12
                            rowSpacing: 12
                            Repeater {
                                model: view.programs.shelf
                                Rectangle {
                                    id: shelfItem
                                    required property var modelData
                                    objectName: "shelf-" + modelData.package
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    implicitHeight: shelfRow.implicitHeight + 24
                                    radius: 14
                                    color: Qt.rgba(1,.98,.96,.42)
                                    border.color: Qt.rgba(36/255,16/255,24/255,.14)
                                    RowLayout {
                                        id: shelfRow
                                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
                                        spacing: 12
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            ManagerLabel { text: shelfItem.modelData.name || ""; font.weight: Font.DemiBold }
                                            ManagerLabel { text: shelfItem.modelData.summary || ""; color: "#866c70"; font.pixelSize: 12 }
                                        }
                                        ManagerButton {
                                            objectName: "shelf-install-" + shelfItem.modelData.package
                                            text: shelfItem.modelData.installed ? "Installed" : "Install"
                                            primary: !shelfItem.modelData.installed
                                            enabled: !shelfItem.modelData.installed && !view.programs.busy && !view.programs.externalBusy
                                                && (shelfItem.modelData.actions || []).includes("install")
                                            onClicked: view.programs.preparePackage(shelfItem.modelData, "install")
                                        }
                                    }
                                }
                            }
                        }
                    }
                    ManagerLabel {
                        objectName: "browse-empty"
                        visible: view.programs.catalogReceived && !view.programs.busy && !view.programs.allPrograms.length
                        text: "No programs could be loaded. Check the internet connection, then Refresh."
                        color: "#866c70"
                    }
                    ManagerButton { objectName: "refresh"; text: "Refresh"; quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh() }
                }
                ManagerProgram { visible: view.page === "program"; Layout.fillWidth: true; controller: view.programs }
                ManagerUpdates { id: updatesPage; visible: view.page === "updates"; Layout.fillWidth: true; controller: view.controller; programs: view.programs.allRows; launchEntries: view.programs.launchEntries || []; transactionBusy: view.programs.mutating }
                Item { implicitHeight: 24; Layout.fillWidth: true }
            }
        }
    }
    Rectangle { anchors.fill: parent; radius: 16; color: "transparent"; border.width: 2; border.color: Qt.rgba(226/255,115/255,63/255,.62) }
    component TabButton: C.AbstractButton {
        id: tabButton
        property bool selected: false
        property int badge: 0
        implicitHeight: 38
        leftPadding: 16; rightPadding: 16
        focusPolicy: Qt.StrongFocus
        Accessible.role: Accessible.PageTab
        Accessible.name: text
        Keys.onReturnPressed: clicked()
        Keys.onEnterPressed: clicked()
        contentItem: RowLayout {
            spacing: 7
            Text {
                text: tabButton.text
                font.family: "Noto Sans"; font.pixelSize: 14; font.weight: Font.DemiBold
                color: tabButton.selected ? "#241018" : "#866c70"
                verticalAlignment: Text.AlignVCenter
            }
            Rectangle {
                objectName: "tab-badge"
                visible: tabButton.badge > 0
                implicitWidth: Math.max(22, badgeText.implicitWidth + 12)
                implicitHeight: 18
                radius: 9
                color: "#e2733f"
                Text { id: badgeText; anchors.centerIn: parent; text: tabButton.badge; font.family: "Noto Sans"; font.pixelSize: 11; font.bold: true; color: "#2a0f05" }
            }
        }
        background: Rectangle {
            radius: 12
            color: tabButton.selected ? Qt.rgba(1,.98,.96,.82) : tabButton.hovered ? Qt.rgba(1,.98,.96,.4) : "transparent"
            border.width: tabButton.activeFocus ? 2 : 1
            border.color: tabButton.activeFocus ? "#c2481f" : tabButton.selected ? Qt.rgba(36/255,16/255,24/255,.14) : "transparent"
        }
    }
    component Chip: C.AbstractButton {
        id: chip
        property bool selected: false
        implicitHeight: 32
        leftPadding: 14; rightPadding: 14
        focusPolicy: Qt.StrongFocus
        Accessible.role: Accessible.RadioButton
        Accessible.checkable: true
        Accessible.checked: selected
        Keys.onReturnPressed: clicked()
        Keys.onEnterPressed: clicked()
        contentItem: Text {
            objectName: "chip-label"
            text: chip.text
            font.family: "Noto Sans"; font.pixelSize: 13
            color: chip.selected ? "#c2481f" : "#241018"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: height / 2
            color: chip.hovered ? Qt.rgba(1,.98,.96,.68) : Qt.rgba(1,.98,.96,.5)
            border.width: chip.activeFocus ? 2 : 1
            border.color: chip.selected || chip.activeFocus ? "#e2733f" : Qt.rgba(36/255,16/255,24/255,.14)
        }
    }
    component SearchField: C.TextField {
        id: field
        signal cleared
        implicitHeight: 40
        leftPadding: 36
        rightPadding: 34
        font.family: "Noto Sans"
        font.pixelSize: 14
        color: "#241018"
        placeholderTextColor: "#866c70"
        selectByMouse: true
        background: Rectangle {
            radius: 12
            color: Qt.rgba(1,1,1,.62)
            border.color: field.activeFocus ? "#e2733f" : Qt.rgba(36/255,16/255,24/255,.14)
            Text { anchors { left: parent.left; leftMargin: 13; verticalCenter: parent.verticalCenter } text: "⌕"; color: "#866c70"; font.pixelSize: 17 }
        }
        ManagerButton {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 30; height: 30; padding: 0; leftPadding: 0; rightPadding: 0
            visible: field.text !== ""
            text: "×"; quiet: true
            Accessible.name: "Clear"
            onClicked: { field.text = ""; field.cleared(); }
        }
    }
    component BackLink: C.AbstractButton {
        id: back
        implicitHeight: 28
        leftPadding: 0; rightPadding: 8
        focusPolicy: Qt.StrongFocus
        Accessible.role: Accessible.Link
        Accessible.name: text
        Keys.onReturnPressed: clicked()
        Keys.onEnterPressed: clicked()
        contentItem: Text {
            text: back.text
            font.family: "Noto Sans"; font.pixelSize: 14; font.weight: Font.DemiBold
            color: back.hovered ? "#a83c18" : "#c2481f"
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { radius: 6; color: "transparent"; border.width: back.activeFocus ? 2 : 0; border.color: "#c2481f" }
    }
    component Heading: RowLayout {
        id: heading
        property string title: ""
        property string note: ""
        Layout.fillWidth: true
        Layout.bottomMargin: 6
        spacing: 10
        // A long search elides its title; the count beside it stays whole and the row never widens the page.
        ManagerLabel {
            objectName: "heading-title"
            text: heading.title; font.pixelSize: 17; font.weight: Font.DemiBold; wrapMode: Text.NoWrap; elide: Text.ElideRight
            // Fills only up to its text, so the count sits beside it; may shrink to nothing.
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: implicitWidth
        }
        ManagerLabel { id: headingNote; text: heading.note; color: "#866c70"; font.pixelSize: 13; visible: text !== ""; wrapMode: Text.NoWrap; Layout.fillWidth: false }
        Item { Layout.fillWidth: true }
    }
    component Shelf: ColumnLayout {
        id: shelf
        property string title: ""
        property string note: ""
        // The list a "See all" opens; empty: no link.
        property string more: ""
        Layout.fillWidth: true
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            ManagerLabel { text: shelf.title; font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: false; wrapMode: Text.NoWrap }
            ManagerLabel { text: shelf.note; color: "#866c70"; font.pixelSize: 13; visible: text !== "" }
            Item { Layout.fillWidth: true; visible: shelf.note === "" }
            ManagerButton {
                objectName: "see-all-" + shelf.more
                visible: shelf.more !== ""
                text: "See all ›"
                quiet: true
                onClicked: view.showList(shelf.more)
            }
        }
    }
    component CardRow: RowLayout {
        id: cardRow
        property var rows: []
        // The last card opens the whole list ("All N ›") instead of a program.
        property int allCount: 0
        property string allList: ""
        Layout.fillWidth: true
        spacing: 12
        Repeater {
            model: cardRow.rows
            ProgramCard {
                required property var modelData
                program: modelData
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                onClicked: view.showProgram(program)
            }
        }
        ProgramCard {
            objectName: "see-all-" + cardRow.allList
            visible: cardRow.allCount > 0
            program: ({name: "All " + cardRow.allCount + " ›", icon: ""})
            hint: "See the whole set"
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            onClicked: view.showList(cardRow.allList)
        }
        // Keeps a short row's cards at a quarter of the width.
        Repeater {
            model: Math.max(0, 4 - cardRow.rows.length - (cardRow.allCount > 0 ? 1 : 0))
            Item { Layout.fillWidth: true; Layout.preferredWidth: 1 }
        }
    }
    // Stays on every AUR list; it cannot be hidden.
    component AurStripe: Rectangle {
        Layout.fillWidth: true
        implicitHeight: stripeText.implicitHeight + 18
        radius: 10
        color: Qt.rgba(226/255,115/255,63/255,.13)
        border.color: Qt.rgba(226/255,115/255,63/255,.42)
        ManagerLabel {
            id: stripeText
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 14; rightMargin: 14 }
            text: "AUR — nobody checked these programs."
            font.pixelSize: 13; font.weight: Font.DemiBold; color: "#8a3412"
        }
    }
    component Tag: Rectangle {
        property string text: ""
        implicitWidth: tagText.implicitWidth + 18
        implicitHeight: 24
        radius: 8
        color: "transparent"
        border.color: Qt.rgba(36/255,16/255,24/255,.2)
        Text { id: tagText; anchors.centerIn: parent; text: parent.text; font.family: "Noto Sans"; font.pixelSize: 12; color: "#866c70" }
    }
    component ProgramRow: Item {
        id: rowItem
        required property var row
        property bool installedRow: false
        property bool packageRow: false
        readonly property bool aur: row.kind === "aur" && !installedRow
        readonly property bool partOfEmaki: installedRow && Logic.partOfEmaki(row)
        readonly property bool aurRefused: aur && view.programs.aurKnown && !view.programs.aurSnapshots
        objectName: packageRow ? "package-row-" + row.package : installedRow ? "installed-row-" + (row.package || row.id) : "program-row-" + (row.package || row.id)
        implicitHeight: 68
        RowLayout {
            anchors { fill: parent; leftMargin: 4; rightMargin: 4 }
            spacing: 14
            ProgramIcon { program: rowItem.row; Layout.preferredWidth: 40; Layout.preferredHeight: 40 }
            C.AbstractButton {
                Layout.fillWidth: true
                implicitHeight: 52
                focusPolicy: rowItem.row.self ? Qt.NoFocus : Qt.StrongFocus
                Keys.onReturnPressed: clicked()
                Keys.onEnterPressed: clicked()
                enabled: !rowItem.row.self && (!rowItem.packageRow || !!rowItem.row.programKey)
                onClicked: {
                    if (rowItem.packageRow) {
                        if (view.programs.busy) return;
                        view.returnTo = view.place();
                        view.programs.openPackage(rowItem.row); view.page = "program"; view.resetScroll();
                    } else view.showProgram(rowItem.row);
                }
                contentItem: ColumnLayout {
                    spacing: 2
                    ManagerLabel { text: rowItem.row.name || ""; font.weight: Font.DemiBold; maximumLineCount: 1; elide: Text.ElideRight; wrapMode: Text.NoWrap }
                    ManagerLabel {
                        text: rowItem.installedRow ? [rowItem.row.summary || "", Logic.removalStatus(rowItem.row) || (rowItem.row.self ? "" : view.source(rowItem.row))].filter(Boolean).join(" · ")
                            : rowItem.packageRow ? [rowItem.row.summary || "", rowItem.row.repository || "", rowItem.row.installedVersion || rowItem.row.version || ""].filter(Boolean).join(" · ")
                            : rowItem.row.summary || ""
                        visible: text !== ""; color: "#866c70"; font.pixelSize: 12; maximumLineCount: 1; elide: Text.ElideRight; wrapMode: Text.NoWrap
                    }
                    Stars { objectName: "row-rating"; rating: rowItem.row.rating || null }
                }
                background: Rectangle { color: "transparent"; radius: 8; border.width: 2; border.color: parent.activeFocus ? "#e2733f" : "transparent" }
            }
            ManagerLabel {
                visible: rowItem.aur && (rowItem.row.orphan || rowItem.row.outOfDate)
                text: [rowItem.row.orphan ? "no maintainer" : "", rowItem.row.outOfDate ? "out of date" : ""].filter(Boolean).join(" · ")
                Layout.fillWidth: false; font.pixelSize: 12; color: "#a01b45"
            }
            Tag {
                objectName: "aur-votes"
                visible: rowItem.aur
                text: rowItem.row.votes === null || rowItem.row.votes === undefined ? "Votes unknown" : view.count(rowItem.row.votes, "vote", "votes")
            }
            Tag {
                objectName: "part-of-emaki"
                visible: rowItem.partOfEmaki
                text: "Part of Emaki"
            }
            Tag {
                visible: !rowItem.installedRow && !!rowItem.row.installed
                text: "Installed"
            }
            ManagerButton {
                objectName: "remove"
                visible: !rowItem.partOfEmaki && (rowItem.row.actions || []).includes("remove") && (rowItem.installedRow || rowItem.packageRow && rowItem.row.installed)
                text: "Remove"; danger: true
                enabled: !view.programs.busy && !view.programs.externalBusy && (rowItem.row.actions || []).includes("remove")
                onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "remove") : view.programs.prepareRemove(rowItem.row)
            }
            ManagerButton {
                objectName: "install"
                visible: !rowItem.installedRow && !rowItem.row.installed
                text: rowItem.aur ? "Build and install" : "Install"
                primary: true
                enabled: !view.programs.busy && !view.programs.externalBusy && !rowItem.aurRefused
                    && !view.programs.refreshPending && ((rowItem.packageRow || rowItem.aur ? rowItem.row : Logic.edition(rowItem.row) || {}).actions || []).includes("install")
                onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "install") : view.installProgram(rowItem.row)
            }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(36/255,16/255,24/255,.12) }
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
            readonly property string action: view.programs.planAction
            readonly property bool snapshotUndo: action === "undo" && view.programs.plan?.transaction?.mode === "snapshot"
            readonly property bool aurPlan: action === "install" && view.programs.selectedEdition?.kind === "aur"
            readonly property bool planning: view.programs.phase === "planning"
            readonly property var builds: Logic.aurBuilds(view.programs.plan)
            readonly property var runPulls: Logic.aurPulls(view.programs.plan, "run")
            readonly property var buildPulls: Logic.aurPulls(view.programs.plan, "build")
            readonly property var kept: Logic.undoRows(view.programs.plan)
            readonly property string programName: view.programs.selectedEdition?.name || ""
            ManagerLabel {
                text: previewColumn.action === "remove" ? "Remove " + previewColumn.programName + "?"
                    : previewColumn.action === "undo" ? "Undo the install of " + previewColumn.programName + "?"
                    : previewColumn.aurPlan ? "Build " + previewColumn.programName + " from the AUR?"
                    : view.programs.selectedEdition?.kind === "arch" ? "Installing also brings the system up to date."
                    : "Install " + previewColumn.programName + "?"
                font.pixelSize: 21; font.weight: Font.DemiBold
            }
            ManagerLabel { objectName: "program-preview-error"; text: view.programs.message; visible: text !== ""; color: "#a01b45" }
            ManagerLabel {
                visible: previewColumn.aurPlan
                text: "Nobody checked how these programs are built, neither Emaki nor Arch. Their build instructions run on this computer while the program is built. Building also brings the system up to date first."
                font.pixelSize: 13
            }
            ManagerLabel {
                visible: !previewColumn.snapshotUndo && (previewColumn.planning || !!view.programs.plan && !previewColumn.aurPlan)
                text: previewColumn.planning ? (previewColumn.aurPlan ? "Reading the build instructions and what they need…" : "Checking what will change…")
                    : previewColumn.action === "remove" || previewColumn.action === "undo" ? (view.programs.planRows.length === 1 ? "This goes:" : "These " + view.programs.planRows.length + " packages go:")
                    : view.programs.planRows.length === 1 ? "This changes:" : "These " + view.programs.planRows.length + " packages change:"
            }
            ManagerLabel {
                visible: previewColumn.action === "install" && view.programs.selectedEdition?.kind === "arch" && !!view.programs.plan && !previewColumn.planning
                text: view.planDownload()
                color: "#866c70"; font.pixelSize: 13
            }
            C.ScrollView {
                Layout.fillWidth: true
                visible: !previewColumn.snapshotUndo
                Layout.preferredHeight: Math.min(previewColumn.aurPlan ? 300 : 210, previewRows.implicitHeight)
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
                                text: view.isPlanTarget(parent.modelData) ? "the program"
                                    : view.programs.planAction === "undo" ? "came with it"
                                    : view.programs.planAction === "remove" && view.programs.selectedEdition?.kind !== "flatpak" ? "nothing else needs it" : parent.modelData.detail || ""
                                Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12
                            }
                        }
                    }
                    ManagerLabel { visible: previewColumn.builds.length > 0; text: "BUILT FROM THE AUR (" + previewColumn.builds.length + ")"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70" }
                    Repeater {
                        model: previewColumn.builds
                        ColumnLayout {
                            id: buildRow
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                ManagerLabel { text: buildRow.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                ManagerLabel { text: buildRow.modelData.target ? "the program" : "it needs this"; Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12 }
                            }
                            ManagerLabel {
                                text: [buildRow.modelData.version, buildRow.modelData.maintainer, buildRow.modelData.outOfDate].filter(Boolean).join(" · ")
                                color: buildRow.modelData.outOfDate || buildRow.modelData.maintainer === "nobody maintains it" ? "#a01b45" : "#866c70"; font.pixelSize: 12
                            }
                            ManagerButton {
                                visible: buildRow.modelData.recipe !== ""
                                text: "See how it is built · " + buildRow.modelData.commit
                                quiet: true; implicitHeight: 26; leftPadding: 0; font.pixelSize: 12
                                onClicked: Qt.openUrlExternally(buildRow.modelData.recipe)
                            }
                        }
                    }
                    ManagerLabel { visible: previewColumn.runPulls.length > 0; text: "FROM ARCH, STAYS INSTALLED (" + previewColumn.runPulls.length + ")"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; Layout.topMargin: 6 }
                    Repeater {
                        model: previewColumn.runPulls
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                            ManagerLabel { text: parent.modelData.repository; Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12 }
                        }
                    }
                    ManagerLabel { visible: previewColumn.buildPulls.length > 0; text: "FROM ARCH, ONLY TO BUILD IT (" + previewColumn.buildPulls.length + ") — removed afterwards"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; Layout.topMargin: 6 }
                    Repeater {
                        model: previewColumn.buildPulls
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                            ManagerLabel { text: parent.modelData.repository; Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12 }
                        }
                    }
                    ManagerLabel { visible: previewColumn.kept.length > 0; text: "THESE STAY"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; Layout.topMargin: 6 }
                    Repeater {
                        model: previewColumn.kept
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                            ManagerLabel { text: parent.modelData.detail; Layout.fillWidth: false; color: "#866c70"; font.pixelSize: 12 }
                        }
                    }
                }
            }
            ManagerLabel {
                visible: previewColumn.action === "remove"
                text: view.programs.selectedEdition?.kind === "flatpak" ? "Your files stay. The program’s own settings stay too, so they are back if you install it again." : "Your files and the program’s settings in your home folder stay as they are."
                color: "#866c70"; font.pixelSize: 13
            }
            ManagerLabel {
                visible: previewColumn.action === "undo" && !previewColumn.planning
                text: previewColumn.snapshotUndo ? (view.programs.plan?.transaction?.message || "") : ["Exactly what this install added goes, in one step. Your files and the program’s settings in your home folder stay.",
                       (view.programs.plan?.transaction?.upgraded || []).length ? "Updates that came with the install stay." : "",
                       view.programs.plan?.transaction?.snapshot ? "The system saved before the install is still in Updates, under If something broke." : ""].filter(Boolean).join(" ")
                color: "#866c70"; font.pixelSize: 13
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                ManagerButton { text: "Cancel"; enabled: !view.programs.busy; onClicked: view.programs.cancelPlan() }
                ManagerButton {
                    objectName: "confirm-program-operation"
                    text: previewColumn.snapshotUndo ? "Restore and restart later" : previewColumn.action === "remove" ? "Remove" : previewColumn.action === "undo" ? "Undo install" : previewColumn.aurPlan ? "Build and install" : "Install"
                    primary: true; danger: previewColumn.action === "remove" || previewColumn.action === "undo"
                    enabled: !view.programs.busy && !view.programs.externalBusy && view.programs.plan?.ok === true && !!view.programs.plan?.token
                    onClicked: {
                        if (view.programs.planAction !== "remove" && view.page !== "program") {
                            view.returnTo = view.place(); view.page = "program"; view.resetScroll();
                        }
                        view.programs.executePlan();
                    }
                }
            }
        }
    }
    C.Popup {
        id: aurWarning
        objectName: "aur-warning"
        property bool dontShow: false
        parent: view
        x: 0; y: 0
        width: view.width
        height: view.height
        padding: 0
        modal: true
        focus: true
        closePolicy: C.Popup.CloseOnEscape
        background: Rectangle { radius: 16; color: "#fff9f5" }
        C.Overlay.modal: Rectangle { color: Qt.rgba(36/255,16/255,24/255,.26) }
        C.ScrollView {
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: warningFooter.top; bottomMargin: 16 }
            objectName: "aur-warning-body"
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                id: warningColumn
                width: Math.min(640, aurWarning.width - 80)
                x: (aurWarning.width - width) / 2
                spacing: 16
                Item { implicitHeight: 34 }
                ManagerLabel { text: "AUR — programs from the community"; font.pixelSize: 28; font.weight: Font.DemiBold }
                ManagerLabel { text: "WHY IT IS USEFUL"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                ManagerLabel { text: "Here is what the regular catalog lacks: programs that are not in Arch or Flathub, official builds of some programs, the newest versions, rare tools, drivers and fonts. If a program is nowhere else, it is probably here. Someone from the community published it and maintains it."; lineHeight: 1.35 }
                ManagerLabel { text: "WHAT YOU RISK"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                ManagerLabel { text: "Anyone can publish a program here and nobody checks it, neither Emaki nor Arch. A program can fail to work, break your system, or contain malware — for example, code that steals passwords or files. You cannot tell a good one from a bad one in advance here."; lineHeight: 1.35 }
                ManagerLabel { text: "WHAT EMAKI DOES"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                ManagerLabel {
                    text: "A system snapshot before every install. If something broke, the install can be undone. A snapshot does not protect against stolen data."
                    lineHeight: 1.35
                }
                ManagerLabel {
                    objectName: "aur-warning-no-snapshots"
                    visible: !view.programs.aurSnapshots
                    text: view.noSnapshotsText
                    color: "#a01b45"; font.weight: Font.DemiBold; lineHeight: 1.35
                }
                ManagerLabel { text: "THE DECISION IS YOURS"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70"; font.letterSpacing: .8 }
                ManagerLabel { text: "Installing from here, you are responsible for the consequences."; lineHeight: 1.35 }
                Item { implicitHeight: 8 }
            }
        }
        ColumnLayout {
            id: warningFooter
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 24 }
            width: Math.min(640, aurWarning.width - 80)
            spacing: 16
            C.CheckBox {
                id: dontShowBox
                objectName: "aur-dont-show"
                text: "Don’t show this again"
                checked: aurWarning.dontShow
                onToggled: aurWarning.dontShow = checked
                font.family: "Noto Sans"
                font.pixelSize: 13
                focusPolicy: Qt.StrongFocus
                // Drawn here: the platform style would draw a dark box with pale text.
                indicator: Rectangle {
                    x: dontShowBox.leftPadding
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: 18; implicitHeight: 18; radius: 5
                    color: dontShowBox.checked ? "#e2733f" : "#fffaf6"
                    border.width: dontShowBox.activeFocus ? 2 : 1
                    border.color: dontShowBox.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.35)
                    Text { anchors.centerIn: parent; visible: dontShowBox.checked; text: "✓"; color: "#2a0f05"; font.pixelSize: 13; font.bold: true }
                }
                contentItem: Text {
                    leftPadding: dontShowBox.indicator.width + 10
                    text: dontShowBox.text
                    font: dontShowBox.font
                    color: "#241018"
                    verticalAlignment: Text.AlignVCenter
                }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                ManagerButton { objectName: "aur-not-now"; text: "Not now"; onClicked: aurWarning.close() }
                ManagerButton {
                    objectName: "aur-accept"
                    text: "Got it, open the AUR"
                    primary: true
                    enabled: view.programs.aurKnown && !view.programs.externalBusy
                    onClicked: {
                        view.programs.setAur(true, !aurWarning.dontShow);
                        aurWarning.close();
                        view.aurAcknowledged = true;
                        if (view.page === "aur") view.enterAur(); else view.navigate("aur");
                    }
                }
            }
        }
    }
}
