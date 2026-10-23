// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell
import "file:@EMAKI_DATADIR@/shell" as Shell
import "ManagerLogic.js" as Logic
import "file:@EMAKI_DATADIR@/shell/SystemIcons.js" as SystemIcons

Rectangle {
    id: view
    readonly property bool translationsReady: Shell.Translation.initialize()
    required property var controller
    required property var programs
    property string wallpaperTexture: ""
    property real dpr: 1
    property url brandIconSource: SystemIcons.programsSvg(Shell.ShellPalette.accent, Shell.ShellPalette.accentStrong, Shell.ShellPalette.accentSoft, Shell.ShellPalette.accentShade, Shell.ShellPalette.managerIconInk)
    // Pages: browse, search, category, list (a shelf's "See all"), program, aur, installed, updates.
    property string page: "browse"
    property string category: ""
    property string categoryName: ""
    property string query: ""
    property string listId: ""
    property string installedQuery: ""
    // Where the open program page came from; its back control returns there.
    property var returnTo: ({page: "browse", label: qsTranslate("ManagerView", "Browse"), tab: "browse"})
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
    // A narrow window (a niri column of a third or a half) keeps its margins small, moves the
    // category strip into the page and stacks rows that sit side by side when wide.
    readonly property bool narrow: width < 720
    readonly property int gutter: narrow ? 16 : 28
    // A very short window (a 1280 × 800 screen at scale 2) scrolls a list page's heading, the AUR's
    // close control and the AUR warning's choice with the content.
    readonly property bool shortWindow: height < 400
    // A list page's heading also scrolls with its rows wherever pinning it would leave the rows less
    // than two rows of room: a narrow window with a long notice (no snapshots on the AUR page).
    property bool headInList: false
    // The pinned heading's height, measured while it is pinned: the same heading laid out at the top
    // of the list reports a different height, and choosing from that one flips back and forth.
    property real headHeight: 0
    onShortWindowChanged: placeHead()
    // Later, never inside the heading loader's own activation.
    onHeadHeightChanged: Qt.callLater(placeHead)
    // The rows' room with the heading pinned: measured when pinned, and estimated from the pinned
    // heading's height when it scrolls with the rows.
    function placeHead(): void {
        const note = page === "installed" ? installedNoteLabel.implicitHeight + 14 : 0;
        const room = rowList.height - (headInList ? headHeight + 12 + note : 0);
        const inList = shortWindow || listPage && room < 120;
        if (inList !== headInList) headInList = inList;
    }
    readonly property var categoryRows: [
        {id: "Network", name: qsTranslate("ManagerView", "Internet")}, {id: "Office", name: qsTranslate("ManagerView", "Office")}, {id: "Graphics", name: qsTranslate("ManagerView", "Graphics")},
        {id: "AudioVideo", name: qsTranslate("ManagerView", "Audio & Video")}, {id: "Game", name: qsTranslate("ManagerView", "Games")}, {id: "Development", name: qsTranslate("ManagerView", "Development")},
        {id: "Education", name: qsTranslate("ManagerView", "Education")}, {id: "System", name: qsTranslate("ManagerView", "System")}, {id: "Utility", name: qsTranslate("ManagerView", "Utilities")}
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
        popular: {title: qsTranslate("ManagerView", "Popular"), rows: popular},
        essentials: {title: qsTranslate("ManagerView", "Essentials"), rows: essentials},
        fresh: {title: qsTranslate("ManagerView", "New and updated"), rows: fresh}
    })
    readonly property var regularResults: programs.programs.filter(row => row.kind !== "aur")
    readonly property var aurList: programs.aurQuery.trim().length >= 2 ? programs.aurRows : Logic.byVotes(programs.aurPopular)
    readonly property var selfRow: ({key: "self:emaki-update-manager", id: "emaki-update-manager", package: "emaki-update-manager",
        name: qsTranslate("ManagerView", "Programs & Updates"), summary: qsTranslate("ManagerView", "This window"), kind: "arch", installed: true, self: true,
        icon: String(brandIconSource), actions: []})
    readonly property var installedList: Logic.installedWithSelf(programs.installedPrograms, installedQuery, selfRow)
    radius: 16
    color: "transparent"
    clip: true
    focus: true
    function resetScroll(): void { body.contentItem.contentY = 0; packageList.positionViewAtBeginning(); rowList.positionViewAtBeginning(); }
    function scroller(): var { return packagesPage ? packageList : listPage ? rowList : body.contentItem; }
    // A scrolling area's range, with a list's header (scrolled with its rows in a short window) and margins.
    function scrollTop(flick: var): real { return flick.originY - flick.topMargin; }
    function scrollEnd(flick: var): real { return Math.max(scrollTop(flick), flick.originY + flick.contentHeight - flick.height + flick.bottomMargin); }
    // Keeps the focused control in sight in every area that scrolls around it: a dialog's text, then the page.
    function reveal(item: var): void {
        if (!item) return;
        for (let flick = item.parent; flick; flick = flick.parent) {
            if (!(flick instanceof Flickable) || scrollEnd(flick) <= scrollTop(flick)) continue;
            const rect = item.mapToItem(flick.contentItem, 0, 0, item.width, item.height);
            if (rect.y < flick.contentY) flick.contentY = Math.max(scrollTop(flick), rect.y - 8);
            else if (rect.y + rect.height > flick.contentY + flick.height)
                flick.contentY = Math.min(scrollEnd(flick), rect.y + rect.height - flick.height + 8);
        }
    }
    function present(): void {
        resetScroll(); programs.refresh();
        if (page === "development") programs.readDevelopment();
        if (page === "websites") programs.readWebsites();
    }
    function clearTransactionMessage(): void {
        if (!programs.mutating) { programs.message = ""; programs.messageIsError = true; }
    }
    // Each page first tries its heading pinned, where it is measured.
    onPageChanged: { clearTransactionMessage(); headInList = shortWindow; Qt.callLater(placeHead); }
    // A window reopened on a list or program page it cannot rebuild starts on Browse.
    Component.onCompleted: {
        if (page === "list" && !lists[listId] || page === "program" && !programs.selectedEdition) page = "browse";
        placeHead();
    }
    onQueryChanged: clearTransactionMessage()
    function navigate(name: string): void {
        clearTransactionMessage();
        page = name; category = ""; categoryName = ""; query = ""; listId = ""; search.text = "";
        // The AUR warning shows again on the next visit until the person turns it off.
        if (name !== "aur") aurAcknowledged = false;
        programs.selectCategory(""); resetScroll();
        if (name === "updates") controller.refreshRestorePoints();
        if (name === "aur") enterAur();
        if (name === "development") programs.readDevelopment();
        if (name === "websites") programs.readWebsites();
    }
    function closeAur(): void { programs.setAur(false, true); aurSearch.text = ""; aurAcknowledged = false; navigate("browse"); }
    function enterAur(): void {
        if (!programs.aurKnown || !programs.aurEnabled) return;
        if (!aurOpen) { aurWarning.dontShow = false; aurWarning.open(); return; }
        programs.readPopular();
    }
    function place(): var {
        if (page === "category") return {page: page, category: category, categoryName: categoryName, label: categoryName, tab: "browse"};
        if (page === "search") return {page: page, query: query, label: qsTranslate("ManagerView", "Results"), tab: "browse"};
        if (page === "list") return {page: page, listId: listId, label: lists[listId]?.title || qsTranslate("ManagerView", "Browse"), tab: "browse"};
        if (page === "aur") return {page: page, label: "AUR", tab: "aur"};
        if (page === "installed") return {page: page, label: qsTranslate("ManagerView", "Installed", "list of installed programs"), tab: "installed"};
        if (page === "program") return returnTo;
        return {page: "browse", label: qsTranslate("ManagerView", "Browse"), tab: "browse"};
    }
    function showProgram(row: var): void {
        if ((programs.busy && !programs.backgroundBusy) || !row || row.self) return;
        returnTo = place();
        programs.openProgram(row); page = "program"; resetScroll();
    }
    function installProgram(row: var): void {
        // Install from a list: the program's page opens behind its install preview.
        // During background work the page opens at once and the preview follows when that work ends.
        if ((programs.busy && !programs.backgroundBusy) || programs.externalBusy) return;
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
        if (!rows.length || rows.some(row => row.downloadSize === null || row.downloadSize === undefined)) return qsTranslate("ManagerView", "Download size unknown");
        return qsTranslate("ManagerView", "%1 MB to download in total").arg(Math.ceil(rows.reduce((total, row) => total + row.downloadSize, 0) / 1048576).toFixed(0));
    }
    readonly property var previewPlanRows: programs.planAction === "install"
        ? programs.planRows.filter(row => isPlanTarget(row)).concat(programs.planRows.filter(row => !isPlanTarget(row)))
        : programs.planRows
    function isPlanTarget(row: var): bool {
        const selected = programs.selectedEdition || {};
        return row.name === selected.package || row.name === selected.ref
            || selected.kind === "flatpak" && String(row.name).split("/")[1] === selected.id;
    }
    readonly property string noSnapshotsText: qsTranslate("ManagerView", "This computer does not support system snapshots, so AUR installs are unavailable. Emaki needs a snapshot before installing from the AUR. You can still browse.")
    readonly property string installedNote: qsTranslate("ManagerView", "Programs marked “Part of Emaki” keep the system working and cannot be removed here.")
    function source(row: var): string { return row.sourceLabel || (row.kind === "flatpak" ? "Flathub" : row.kind === "aur" ? "AUR" : "Arch"); }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Slash && !(Window.activeFocusItem instanceof TextInput)) {
            const field = tab === "aur" ? aurSearch : tab === "installed" ? installedSearch : search;
            if (field.visible) { field.forceActiveFocus(); event.accepted = true; }
        } else if (event.key === Qt.Key_Escape) {
            if (programs.previewOpen && !programs.busy) programs.cancelPlan();
            else if (page === "program" && !programs.mutating) goBack();
            else if (["search", "category", "list", "development", "websites"].includes(page)) navigate("browse");
            event.accepted = true;
        } else {
            const flick = scroller();
            let position = flick.contentY;
            const typing = Window.activeFocusItem instanceof TextInput;
            if (event.key === Qt.Key_PageDown) position += flick.height * .9;
            else if (event.key === Qt.Key_PageUp) position -= flick.height * .9;
            else if (event.key === Qt.Key_Home && !typing) position = scrollTop(flick);
            else if (event.key === Qt.Key_End && !typing) position = scrollEnd(flick);
            else return;
            flick.contentY = Math.max(scrollTop(flick), Math.min(scrollEnd(flick), position));
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
            implicitHeight: Math.max(62, tabRow.height + 24)
            color: Qt.alpha(Shell.ShellPalette.managerHeader, .22)
            // The tabs wrap to a second row when the window is narrower than all four.
            Flow {
                id: tabRow
                // Under 300 px the tabs sit closer, so the four fit in two rows.
                anchors { left: parent.left; leftMargin: view.width < 300 ? 6 : view.gutter - 6; right: parent.right; rightMargin: view.width < 300 ? 6 : view.gutter - 6; verticalCenter: parent.verticalCenter }
                spacing: 4
                Repeater {
                    model: [{id: "browse", name: qsTranslate("ManagerView", "Browse")}, {id: "aur", name: "AUR"}, {id: "installed", name: qsTranslate("ManagerView", "Installed", "list of installed programs")}, {id: "updates", name: qsTranslate("ManagerView", "Updates")}]
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
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.alpha(Shell.ShellPalette.managerInk, .14) }
        }
        ColumnLayout {
            // Browse's own top: the catalog search (Arch and Flathub) and the category strip.
            objectName: "browse-top"
            Layout.fillHeight: false
            visible: view.page === "browse" || view.page === "search"
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 20
            spacing: 14
            GridLayout {
                // All packages goes under the search when both do not fit side by side.
                Layout.fillWidth: true
                columns: width >= 360 ? 2 : 1
                columnSpacing: 10
                rowSpacing: 10
                SearchField {
                    id: search
                    objectName: "program-search"
                    Layout.fillWidth: true
                    placeholderText: view.programs.scope === "packages" ? qsTranslate("ManagerView", "Search all packages") : qsTranslate("ManagerView", "Search programs")
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
                    text: qsTranslate("ManagerView", "All packages")
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
            CategoryStrip {
                objectName: "category-strip"
                // In a narrow window the strip scrolls with Browse's page instead of filling the top.
                visible: view.programs.scope === "programs" && !view.narrow
            }
        }
        GridLayout {
            // The AUR's own top: its search, and the stripe that stays on every AUR list.
            objectName: "aur-top"
            Layout.fillHeight: false
            visible: view.page === "aur" && view.aurOpen
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 20
            columns: width >= 420 ? 2 : 1
            columnSpacing: 10
            rowSpacing: 10
            SearchField {
                id: aurSearch
                objectName: "aur-search"
                Layout.fillWidth: true
                placeholderText: qsTranslate("ManagerView", "Search the AUR")
                enabled: view.programs.aurEnabled
                // A recreated window shows the search its results still follow.
                text: view.programs.aurQuery
                onTextEdited: view.programs.searchAur(text)
                onCleared: view.programs.searchAur("")
            }
            ManagerButton {
                objectName: "close-aur"
                text: qsTranslate("ManagerView", "Close the AUR section")
                quiet: true
                enabled: view.programs.aurEnabled && !view.programs.mutating
                visible: !view.shortWindow
                onClicked: view.closeAur()
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
            placeholderText: qsTranslate("ManagerView", "Search installed")
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
                visible: ["program", "category", "list", "development", "websites"].includes(view.page)
                text: "‹  " + (view.page === "program" ? view.returnTo.label || qsTranslate("ManagerView", "Browse") : qsTranslate("ManagerView", "Browse"))
                onClicked: view.page === "program" ? view.goBack() : view.navigate("browse")
            }
            ManagerLabel {
                Layout.topMargin: 10
                objectName: "program-message"
                visible: view.programs.message !== "" && view.page !== "updates" && !view.programs.previewOpen
                text: view.programs.message
                color: view.programs.messageIsError ? Shell.ShellPalette.managerError : Shell.ShellPalette.managerSuccess
            }
            ManagerButton {
                Layout.topMargin: 10
                // A window narrower than the label shortens it.
                Layout.fillWidth: true; Layout.maximumWidth: implicitWidth
                squeezed: parent.width < implicitWidth
                objectName: "program-recovery"
                text: view.programs.recoveryPoint?.operation === "remove" ? qsTranslate("ManagerUpdates", "Restore…") : qsTranslate("ManagerView", "Restore before this install…")
                visible: !!view.programs.recoveryPoint && view.page !== "updates"
                enabled: !view.programs.busy && !view.controller.busy && !view.controller.restorePending
                onClicked: updatesPage.confirmRestore(view.programs.recoveryPoint)
            }
            Repeater {
                model: view.page !== "updates" && view.page !== "installed" ? view.programs.warnings : []
                ManagerLabel { required property var modelData; text: modelData; color: Shell.ShellPalette.managerDim; font.pixelSize: 12; Layout.topMargin: 6 }
            }
            RowLayout {
                Layout.topMargin: 10
                visible: view.programs.foregroundBusy && view.page !== "updates" && view.page !== "installed"
                C.BusyIndicator { running: parent.visible; Layout.preferredWidth: 24; Layout.preferredHeight: 24 }
                ManagerLabel { text: view.programs.phase === "planning" ? qsTranslate("ManagerView", "Checking what will change…") : view.programs.quietRefresh ? qsTranslate("ManagerView", "Refreshing…") : qsTranslate("ManagerView", "Loading programs…"); color: Shell.ShellPalette.managerDim }
            }
        }
        Loader {
            // The page's heading and messages stay above its rows; in a very short window they scroll with them.
            objectName: "page-head"
            active: !view.headInList
            visible: view.listPage && active
            Layout.fillHeight: false
            Layout.fillWidth: true
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.topMargin: 12
            sourceComponent: pageHead
            onImplicitHeightChanged: if (item && !view.headInList) view.headHeight = implicitHeight
        }
        Component {
            id: listHead
            Item {
                width: rowList.width
                height: listHeadLoader.implicitHeight + 12
                Loader { id: listHeadLoader; objectName: "page-head-in-list"; x: view.gutter; y: 12; width: rowList.width - 2 * view.gutter; sourceComponent: pageHead }
            }
        }
        Component {
            id: pageHead
            ColumnLayout {
                spacing: 0
                ColumnLayout {
                    // A shelf's "See all": the whole list.
                    objectName: "list-page"
                    visible: view.page === "list"
                    Layout.fillWidth: true
                    spacing: 0
                    Heading { title: view.lists[view.listId]?.title || ""; note: Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 program(s)", "", (view.lists[view.listId]?.rows || []).length), (view.lists[view.listId]?.rows || []).length).arg(Logic.groupedCount((view.lists[view.listId]?.rows || []).length)) }
                }
                ColumnLayout {
                    objectName: "category-page"
                    visible: view.page === "category"
                    Layout.fillWidth: true
                    spacing: 0
                    Heading { title: view.categoryName; note: Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 program(s)", "", view.regularResults.length), view.regularResults.length).arg(Logic.groupedCount(view.regularResults.length)) }
                    ManagerLabel {
                        objectName: "category-empty"
                        visible: view.page === "category" && !view.regularResults.length && !view.programs.busy
                        text: view.programs.allPrograms.length ? qsTranslate("ManagerView", "There are no programs in %1 yet.").arg(view.categoryName)
                            : qsTranslate("ManagerView", "There are no programs here yet: the catalog could not be loaded. Check the internet connection, then Refresh on Browse.")
                        color: Shell.ShellPalette.managerDim
                        Layout.topMargin: 12
                    }
                }
                ColumnLayout {
                    objectName: "search-page"
                    visible: view.page === "search"
                    Layout.fillWidth: true
                    spacing: 0
                    Heading { title: qsTranslate("ManagerView", "Results for “%1”").arg(view.query); note: Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 program(s) from Arch and Flathub", "", view.regularResults.length), view.regularResults.length).arg(Logic.groupedCount(view.regularResults.length)) }
                }
                ColumnLayout {
                    objectName: "aur-page"
                    visible: view.page === "aur"
                    Layout.fillWidth: true
                    spacing: 0
                    ManagerButton {
                        // In a very short window the AUR's close control scrolls with its list.
                        objectName: "close-aur-in-list"
                        visible: view.shortWindow && view.aurOpen
                        text: qsTranslate("ManagerView", "Close the AUR section")
                        quiet: true
                        enabled: view.programs.aurEnabled && !view.programs.mutating
                        onClicked: view.closeAur()
                        Layout.bottomMargin: 8
                    }
                    ManagerLabel {
                        // Decision of 2026-10-13: AUR installs are refused without system snapshots (ext4).
                        objectName: "aur-no-snapshots"
                        visible: view.programs.aurKnown && !view.programs.aurSnapshots
                        text: view.noSnapshotsText
                        color: Shell.ShellPalette.managerError; font.pixelSize: 13; font.weight: Font.DemiBold
                        Layout.bottomMargin: 12
                    }
                    Rectangle {
                        objectName: "aur-entry"
                        visible: !view.aurOpen
                        Layout.fillWidth: true
                        implicitHeight: aurEntry.implicitHeight + 32
                        radius: 16
                        color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
                        border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
                        GridLayout {
                            id: aurEntry
                            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 18 }
                            // The button goes under the text in a narrow window.
                            columns: width >= 420 ? 2 : 1
                            columnSpacing: 16
                            rowSpacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                ManagerLabel { text: qsTranslate("ManagerView", "AUR — programs from the community"); font.weight: Font.DemiBold }
                                ManagerLabel {
                                    text: view.programs.aurEnabled ? qsTranslate("ManagerView", "Open. Anyone can publish there and nobody checks it, neither Emaki nor Arch.")
                                        : qsTranslate("ManagerView", "What Arch and Flathub do not have, published by other people. Closed until you read what it means and open it.")
                                    color: Shell.ShellPalette.managerDim; font.pixelSize: 13
                                }
                            }
                            ManagerButton {
                                objectName: "open-aur"
                                text: qsTranslate("ManagerView", "Open the AUR…")
                                primary: true
                                enabled: view.programs.aurKnown
                                onClicked: { aurWarning.dontShow = false; aurWarning.open(); }
                            }
                        }
                    }
                    ManagerLabel { visible: !view.programs.aurKnown || view.programs.phase === "aur"; text: qsTranslate("ManagerView", "Opening the AUR…"); color: Shell.ShellPalette.managerDim }
                    Heading {
                        objectName: "aur-popular"
                        visible: view.aurOpen && view.programs.aurQuery.trim().length < 2
                        title: qsTranslate("ManagerView", "Popular on the AUR")
                        note: qsTranslate("ManagerView", "By votes")
                    }
                    ManagerLabel {
                        visible: view.aurOpen && view.programs.aurQuery.trim().length < 2 && view.programs.aurPopularLoaded && !view.programs.aurPopular.length && view.programs.phase !== "aurPopular"
                        text: qsTranslate("ManagerView", "The list could not be loaded. Search still works.")
                        color: Shell.ShellPalette.managerDim
                    }
                    Heading {
                        visible: view.aurOpen && view.programs.aurQuery.trim().length >= 2
                        title: qsTranslate("ManagerView", "Results for “%1”").arg(view.programs.aurQuery.trim())
                        note: view.programs.aurSearched ? Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 package(s)", "", view.programs.aurRows.length), view.programs.aurRows.length).arg(Logic.groupedCount(view.programs.aurRows.length)) : qsTranslate("ManagerView", "Searching…")
                    }
                    ManagerLabel {
                        visible: view.aurOpen && view.programs.aurSearched && !view.programs.aurRows.length && view.programs.aurQuery.trim().length >= 2
                        text: qsTranslate("ManagerView", "Nothing found in the AUR for “%1”.").arg(view.programs.aurQuery)
                        color: Shell.ShellPalette.managerDim
                    }
                }
                ColumnLayout {
                    objectName: "installed-page"
                    visible: view.page === "installed"
                    Layout.fillWidth: true
                    spacing: 0
                    ManagerLabel { visible: view.programs.inventoryError !== ""; text: view.programs.inventoryError; color: Shell.ShellPalette.managerError }
                    ManagerLabel { visible: view.programs.inventoryLoading && !view.programs.installedPrograms.length; text: qsTranslate("ManagerView", "Loading programs…"); color: Shell.ShellPalette.managerDim }
                    Heading { title: qsTranslate("ManagerView", "Installed", "list of installed programs"); note: Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 program(s)", "", view.installedList.length), view.installedList.length).arg(Logic.groupedCount(view.installedList.length)) }
                }
                ManagerLabel {
                    visible: view.page === "search" && !view.regularResults.length && !view.programs.busy
                    text: qsTranslate("ManagerView", "Nothing found for “%1”. Try another word, or look in the AUR tab.").arg(view.query)
                    color: Shell.ShellPalette.managerDim
                    Layout.topMargin: 24
                }
                ManagerLabel {
                    visible: view.page === "installed" && view.installedQuery.trim() !== "" && !view.installedList.length
                    text: qsTranslate("ManagerView", "Nothing installed matches “%1”.").arg(view.installedQuery.trim())
                    color: Shell.ShellPalette.managerDim
                    Layout.topMargin: 12
                }
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
            onHeightChanged: Qt.callLater(view.placeHead)
            header: view.headInList ? listHead : null
            footer: view.headInList && view.page === "installed" ? installedNoteInList : null
            // A list showing its start keeps showing it when its origin moves: in a short window the
            // page's heading scrolls with the rows and takes its height only after the page has changed.
            property real lastOrigin: 0
            onOriginYChanged: {
                if (contentY <= lastOrigin + 0.5) contentY = originY - topMargin;
                lastOrigin = originY;
            }
            C.ScrollBar.vertical: C.ScrollBar {}
            delegate: Item {
                id: rowDelegate
                required property var modelData
                width: rowList.width
                height: rowEntry.implicitHeight
                ProgramRow {
                    id: rowEntry
                    row: rowDelegate.modelData
                    installedRow: view.page === "installed"
                    x: view.gutter
                    width: rowList.width - 2 * view.gutter
                    height: implicitHeight
                }
            }
        }
        ManagerLabel {
            // Under Installed's rows; in a very short window it scrolls after them.
            id: installedNoteLabel
            visible: view.page === "installed" && !view.headInList
            Layout.leftMargin: view.gutter; Layout.rightMargin: view.gutter; Layout.bottomMargin: 14
            text: view.installedNote; color: Shell.ShellPalette.managerDim; font.pixelSize: 13
        }
        Component {
            id: installedNoteInList
            Item {
                width: rowList.width
                height: installedNoteText.implicitHeight + 12
                ManagerLabel { id: installedNoteText; objectName: "installed-note-in-list"; x: view.gutter; y: 12; width: rowList.width - 2 * view.gutter; text: view.installedNote; color: Shell.ShellPalette.managerDim; font.pixelSize: 13 }
            }
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
                    GridLayout {
                        id: packageTitle
                        // The count goes under the title when the three do not fit on one line.
                        readonly property bool stacked: width < allTitle.implicitWidth + allCount.implicitWidth + allRefresh.implicitWidth + 2 * columnSpacing
                        Layout.fillWidth: true
                        columns: stacked ? 2 : 3
                        ManagerLabel { id: allTitle; text: qsTranslate("ManagerView", "All packages"); font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: packageTitle.stacked; elide: Text.ElideRight; wrapMode: Text.NoWrap; Layout.row: 0; Layout.column: 0 }
                        ManagerLabel { id: allCount; text: Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 package(s)", "", view.programs.packageTotal), view.programs.packageTotal).arg(Logic.groupedCount(view.programs.packageTotal)); color: Shell.ShellPalette.managerDim; font.pixelSize: 13; Layout.row: packageTitle.stacked ? 1 : 0; Layout.column: packageTitle.stacked ? 0 : 1; Layout.columnSpan: packageTitle.stacked ? 2 : 1 }
                        ManagerButton { id: allRefresh; text: qsTranslate("ManagerView", "Refresh"); quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh(); Layout.row: 0; Layout.column: packageTitle.stacked ? 1 : 2 }
                    }
                    ManagerLabel { visible: view.programs.message !== ""; text: view.programs.message; color: view.programs.messageIsError ? Shell.ShellPalette.managerError : Shell.ShellPalette.managerSuccess }
                    Repeater {
                        model: view.programs.warnings
                        ManagerLabel { required property var modelData; text: modelData; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                    }
                    ManagerLabel { visible: view.programs.busy; text: view.programs.mutating ? view.programs.output.split("\n").slice(-1)[0] : qsTranslate("ManagerView", "Loading packages…"); color: Shell.ShellPalette.managerDim }
                    ManagerLabel { visible: view.programs.packagesReceived && !view.programs.packageRows.length; text: qsTranslate("ManagerView", "Nothing found for “%1”.").arg(view.query); color: Shell.ShellPalette.managerDim }
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
                    text: view.programs.phase === "removing" ? qsTranslate("ManagerView", "Removing…") : view.programs.phase === "undoing" ? qsTranslate("ManagerView", "Undoing the install…") : qsTranslate("ManagerView", "Installing…")
                    font.weight: Font.DemiBold
                }
                ColumnLayout {
                    // Browse's start screen.
                    objectName: "browse-page"
                    visible: view.page === "browse"
                    Layout.fillWidth: true
                    spacing: 22
                    CategoryStrip {
                        objectName: "category-strip-page"
                        visible: view.programs.scope === "programs" && view.narrow
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 10
                        ManagerButton {
                            objectName: "open-development"
                            text: qsTranslate("ManagerView", "Development kits")
                            width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                            onClicked: view.navigate("development")
                        }
                        ManagerButton {
                            objectName: "open-websites"
                            text: qsTranslate("ManagerView", "Websites as apps")
                            width: Math.min(implicitWidth, parent.width); squeezed: parent.width > 0 && parent.width < implicitWidth
                            onClicked: view.navigate("websites")
                        }
                    }
                    Shelf {
                        objectName: "shelf-picked"
                        title: view.storefront.featured?.title || ""
                        note: view.storefront.featured?.note || ""
                        visible: view.featured.length > 0
                        GridLayout {
                            id: featuredRow
                            Layout.fillWidth: true
                            // Side by side when the large card keeps its Install button beside its text.
                            columns: width >= 760 ? Math.max(1, view.featured.length) : 1
                            columnSpacing: 12
                            rowSpacing: 12
                            Repeater {
                                model: view.featured
                                ProgramCard {
                                    required property var modelData
                                    required property int index
                                    program: modelData
                                    featured: index === 0
                                    compact: featuredRow.width < 360
                                    installEnabled: (!view.programs.busy || view.programs.backgroundBusy) && !view.programs.externalBusy && (modelData.actions || []).includes("install")
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: index === 0 ? 14 : 10
                                    Layout.fillHeight: true
                                    onClicked: view.showProgram(program)
                                    onInstallRequested: view.installProgram(program)
                                }
                            }
                        }
                    }
                    ManagerLabel { visible: (view.storefront.featured?.programs || []).length > 0 && !view.featured.length && !view.programs.busy; text: qsTranslate("ManagerView", "Our picks are not available right now. Try Refresh."); color: Shell.ShellPalette.managerDim }
                    Shelf {
                        objectName: "shelf-popular"
                        title: qsTranslate("ManagerView", "Popular")
                        note: view.popularMeasured ? qsTranslate("ManagerView", "Most installed this month") : ""
                        more: view.popular.length > 4 ? "popular" : ""
                        visible: view.popular.length > 0
                        CardRow { rows: view.popular.slice(0, 4) }
                    }
                    Shelf {
                        objectName: "shelf-essentials"
                        title: qsTranslate("ManagerView", "Essentials")
                        note: qsTranslate("ManagerView", "What the full install adds")
                        visible: view.essentials.length > 0
                        CardRow { rows: view.essentials.slice(0, 3); allCount: view.essentials.length > 3 ? view.essentials.length : 0; allList: "essentials" }
                    }
                    Shelf {
                        objectName: "shelf-fresh"
                        title: qsTranslate("ManagerView", "New and updated")
                        more: view.fresh.length > 4 ? "fresh" : ""
                        visible: view.fresh.length > 0
                        CardRow { rows: view.fresh.slice(0, 4) }
                    }
                    Shelf {
                        // Emaki's own optional packages; which ones is data (update-manager/emaki-shelf.json).
                        objectName: "emaki-shelf"
                        title: qsTranslate("ManagerView", "From Emaki")
                        note: qsTranslate("ManagerView", "Emaki’s own extras. Each installs with one button.")
                        visible: view.programs.shelf.length > 0
                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 600 ? 2 : 1
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
                                    color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
                                    border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
                                    GridLayout {
                                        id: shelfRow
                                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 14 }
                                        // The button goes under the text in a narrow card.
                                        columns: width >= 300 ? 2 : 1
                                        columnSpacing: 12
                                        rowSpacing: 10
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            ManagerLabel { text: shelfItem.modelData.name || ""; font.weight: Font.DemiBold }
                                            ManagerLabel { text: shelfItem.modelData.summary || ""; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                                        }
                                        ManagerButton {
                                            objectName: "shelf-install-" + shelfItem.modelData.package
                                            text: shelfItem.modelData.installed ? qsTranslate("ManagerView", "Installed") : qsTranslate("ManagerView", "Install")
                                            primary: !shelfItem.modelData.installed
                                            enabled: !shelfItem.modelData.installed && (!view.programs.busy || view.programs.backgroundBusy) && !view.programs.externalBusy
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
                        text: qsTranslate("ManagerView", "No programs could be loaded. Check the internet connection, then Refresh.")
                        color: Shell.ShellPalette.managerDim
                    }
                    ManagerButton { objectName: "refresh"; text: qsTranslate("ManagerView", "Refresh"); quiet: true; enabled: !view.programs.busy; onClicked: view.programs.refresh() }
                }
                ManagerDevelopment { visible: view.page === "development"; Layout.fillWidth: true; controller: view.programs }
                ManagerWebApps { visible: view.page === "websites"; Layout.fillWidth: true; controller: view.programs }
                ManagerProgram { visible: view.page === "program"; Layout.fillWidth: true; controller: view.programs }
                ManagerUpdates { id: updatesPage; visible: view.page === "updates"; Layout.fillWidth: true; controller: view.controller; programs: view.programs.allRows; launchEntries: view.programs.launchEntries || []; transactionBusy: view.programs.mutating; aurKnown: view.programs.aurKnown; aurEnabled: view.programs.aurEnabled }
                Item { implicitHeight: 24; Layout.fillWidth: true }
            }
        }
    }
    Rectangle { anchors.fill: parent; radius: 16; color: "transparent"; border.width: 2; border.color: Qt.alpha(Shell.ShellPalette.accent, .62) }
    component TabButton: C.AbstractButton {
        id: tabButton
        property bool selected: false
        property int badge: 0
        implicitHeight: 38
        leftPadding: view.width < 300 ? 10 : 16; rightPadding: view.width < 300 ? 10 : 16
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
                color: tabButton.selected ? Shell.ShellPalette.managerInk : Shell.ShellPalette.managerDim
                verticalAlignment: Text.AlignVCenter
            }
            Rectangle {
                objectName: "tab-badge"
                visible: tabButton.badge > 0
                implicitWidth: Math.max(22, badgeText.implicitWidth + 12)
                implicitHeight: 18
                radius: 9
                color: Shell.ShellPalette.accent
                Text { id: badgeText; anchors.centerIn: parent; text: tabButton.badge; font.family: "Noto Sans"; font.pixelSize: 11; font.bold: true; color: Shell.ShellPalette.accentText }
            }
        }
        background: Rectangle {
            radius: 12
            color: tabButton.selected ? Qt.alpha(Shell.ShellPalette.managerCream, .82) : tabButton.hovered ? Qt.alpha(Shell.ShellPalette.managerCream, .4) : "transparent"
            border.width: tabButton.activeFocus ? 2 : 1
            border.color: tabButton.activeFocus ? Shell.ShellPalette.accentForeground : tabButton.selected ? Qt.alpha(Shell.ShellPalette.managerInk, .14) : "transparent"
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
            color: chip.selected ? Shell.ShellPalette.accentForeground : Shell.ShellPalette.managerInk
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: height / 2
            color: chip.hovered ? Qt.alpha(Shell.ShellPalette.managerCream, .68) : Qt.alpha(Shell.ShellPalette.managerCream, .5)
            border.width: chip.activeFocus ? 2 : 1
            border.color: chip.selected || chip.activeFocus ? Shell.ShellPalette.accent : Qt.alpha(Shell.ShellPalette.managerInk, .14)
        }
    }
    component CategoryStrip: Flow {
        Layout.fillWidth: true
        spacing: 8
        Chip { objectName: "category-all"; text: qsTranslate("ManagerView", "All"); selected: view.page === "browse"; onClicked: view.navigate("browse") }
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
    component SearchField: C.TextField {
        id: field
        signal cleared
        implicitHeight: 40
        leftPadding: 36
        rightPadding: 34
        font.family: "Noto Sans"
        font.pixelSize: 14
        color: Shell.ShellPalette.managerInk
        placeholderTextColor: Shell.ShellPalette.managerDim
        selectByMouse: true
        background: Rectangle {
            radius: 12
            color: Qt.alpha(Shell.ShellPalette.managerWhite, .62)
            border.color: field.activeFocus ? Shell.ShellPalette.accent : Qt.alpha(Shell.ShellPalette.managerInk, .14)
            Text { anchors { left: parent.left; leftMargin: 13; verticalCenter: parent.verticalCenter } text: "⌕"; color: Shell.ShellPalette.managerDim; font.pixelSize: 17 }
        }
        ManagerButton {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 30; height: 30; padding: 0; leftPadding: 0; rightPadding: 0
            visible: field.text !== ""
            text: "×"; quiet: true
            Accessible.name: qsTranslate("ManagerView", "Clear")
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
            color: back.hovered ? Shell.ShellPalette.accentHoverForeground : Shell.ShellPalette.accentForeground
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { radius: 6; color: "transparent"; border.width: back.activeFocus ? 2 : 0; border.color: Shell.ShellPalette.accentForeground }
    }
    component Heading: GridLayout {
        id: heading
        property string title: ""
        property string note: ""
        Layout.fillWidth: true
        Layout.bottomMargin: 6
        // In a narrow window the count goes under the title when both do not fit on one line.
        columns: view.narrow && width < headingTitle.implicitWidth + headingNote.implicitWidth + 10 ? 1 : 3
        columnSpacing: 10
        rowSpacing: 2
        // A long search elides its title; the count beside it stays whole and the row never widens the page.
        ManagerLabel {
            id: headingTitle
            objectName: "heading-title"
            text: heading.title; font.pixelSize: 17; font.weight: Font.DemiBold; wrapMode: Text.NoWrap; elide: Text.ElideRight
            // Fills only up to its text, so the count sits beside it; may shrink to nothing.
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: Math.ceil(implicitWidth)
            Layout.preferredWidth: Math.ceil(implicitWidth)
        }
        ManagerLabel { id: headingNote; text: heading.note; color: Shell.ShellPalette.managerDim; font.pixelSize: 13; visible: text !== ""; wrapMode: heading.columns > 1 ? Text.NoWrap : Text.Wrap; Layout.fillWidth: heading.columns === 1 }
        Item { Layout.fillWidth: true; visible: heading.columns > 1 }
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
            id: shelfHead
            // In a narrow window the note goes under the title and its See all.
            readonly property bool stacked: view.narrow && shelf.note !== ""
                && width < shelfTitle.implicitWidth + shelfNote.implicitWidth + seeAll.implicitWidth + 3 * spacing
            Layout.fillWidth: true
            spacing: 10
            ManagerLabel {
                id: shelfTitle
                text: shelf.title; font.pixelSize: 17; font.weight: Font.DemiBold; wrapMode: Text.NoWrap
                // In a narrow window it fills the row and elides: with its See all it can be wider than the page.
                Layout.fillWidth: shelfHead.stacked || view.narrow && shelf.note === ""; elide: Text.ElideRight
            }
            ManagerLabel { id: shelfNote; text: shelf.note; color: Shell.ShellPalette.managerDim; font.pixelSize: 13; visible: text !== "" && !shelfHead.stacked }
            Item { Layout.fillWidth: true; visible: shelf.note === "" && !view.narrow }
            ManagerButton {
                id: seeAll
                objectName: "see-all-" + shelf.more
                visible: shelf.more !== ""
                text: qsTranslate("ManagerView", "See all ›")
                quiet: true
                onClicked: view.showList(shelf.more)
            }
        }
        ManagerLabel { visible: shelfHead.stacked; text: shelf.note; color: Shell.ShellPalette.managerDim; font.pixelSize: 13; Layout.topMargin: -8 }
    }
    component CardRow: GridLayout {
        id: cardRow
        property var rows: []
        // The last card opens the whole list ("All N ›") instead of a program.
        property int allCount: 0
        property string allList: ""
        Layout.fillWidth: true
        // Four cards side by side, two or one in a narrow window.
        columns: width >= 716 ? 4 : width >= 352 ? 2 : 1
        columnSpacing: 12
        rowSpacing: 12
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
            program: ({name: qsTranslate("ManagerView", "All %1 ›").arg(cardRow.allCount), icon: ""})
            hint: qsTranslate("ManagerView", "See the whole set")
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            onClicked: view.showList(cardRow.allList)
        }
        // Keeps a short row's cards at a quarter of the width.
        Repeater {
            model: Math.max(0, 4 - cardRow.rows.length - (cardRow.allCount > 0 ? 1 : 0))
            Item { Layout.fillWidth: true; Layout.preferredWidth: 1; visible: cardRow.columns === 4 }
        }
    }
    // Stays on every AUR list; it cannot be hidden.
    component AurStripe: Rectangle {
        Layout.fillWidth: true
        implicitHeight: stripeText.implicitHeight + 18
        radius: 10
        color: Qt.alpha(Shell.ShellPalette.accent, .13)
        border.color: Qt.alpha(Shell.ShellPalette.accent, .42)
        ManagerLabel {
            id: stripeText
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 14; rightMargin: 14 }
            text: qsTranslate("ManagerView", "AUR — nobody checked these programs.")
            font.pixelSize: 13; font.weight: Font.DemiBold; color: Shell.ShellPalette.accentDeepForeground
        }
    }
    // A package line of the preview: its notes beside the name, or under it in a narrow window.
    component PlanRow: GridLayout {
        Layout.fillWidth: true
        columns: width >= 300 ? 3 : 1
        columnSpacing: 5
        rowSpacing: 0
    }
    component Tag: Rectangle {
        property string text: ""
        implicitWidth: tagText.implicitWidth + 18
        implicitHeight: 24
        radius: 8
        color: "transparent"
        border.color: Qt.alpha(Shell.ShellPalette.managerInk, .2)
        Text { id: tagText; anchors.centerIn: parent; text: parent.text; font.family: "Noto Sans"; font.pixelSize: 12; color: Shell.ShellPalette.managerDim }
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
        // When the tags and buttons would leave the name too little room, they go under it.
        readonly property bool stacked: width - 40 - 2 * rowLine.columnSpacing - 8 - rowEnd.rowWidth < 160
        implicitHeight: stacked ? rowLine.implicitHeight + 20 : 68
        GridLayout {
            id: rowLine
            anchors { left: parent.left; right: parent.right; leftMargin: 4; rightMargin: 4 }
            y: rowItem.stacked ? 10 : 0
            height: rowItem.stacked ? implicitHeight : rowItem.height
            columns: rowItem.stacked ? 2 : 3
            columnSpacing: 14
            rowSpacing: 6
            ProgramIcon { program: rowItem.row; Layout.preferredWidth: 40; Layout.preferredHeight: 40; Layout.row: 0; Layout.column: 0 }
            C.AbstractButton {
                Layout.row: 0; Layout.column: 1
                Layout.fillWidth: true
                // Tall enough for a name, a summary and stars; it keeps its top where a 52 px one sits.
                implicitHeight: Math.max(52, implicitContentHeight)
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: rowItem.stacked ? 0 : 8
                focusPolicy: rowItem.row.self ? Qt.NoFocus : Qt.StrongFocus
                Keys.onReturnPressed: clicked()
                Keys.onEnterPressed: clicked()
                enabled: !rowItem.row.self && (!rowItem.packageRow || !!rowItem.row.programKey)
                onClicked: {
                    if (rowItem.packageRow) {
                        if (view.programs.busy && !view.programs.backgroundBusy) return;
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
                        visible: text !== ""; color: Shell.ShellPalette.managerDim; font.pixelSize: 12; maximumLineCount: 1; elide: Text.ElideRight; wrapMode: Text.NoWrap
                    }
                    Item {
                        // The stars measure the row from outside, so a narrow row drops their count instead of spilling.
                        visible: rowStars.value > 0
                        Layout.fillWidth: true
                        implicitHeight: rowStars.implicitHeight
                        Stars { id: rowStars; objectName: "row-rating"; rating: rowItem.row.rating || null; maximumWidth: parent.width }
                    }
                }
                background: Rectangle { color: "transparent"; radius: 8; border.width: 2; border.color: parent.activeFocus ? Shell.ShellPalette.accent : "transparent" }
            }
            GridLayout {
                // The row's tags and buttons: one line, or one under another when even that line does not fit.
                id: rowEnd
                readonly property bool flags: rowItem.aur && (rowItem.row.orphan || rowItem.row.outOfDate)
                readonly property bool installedTag: !rowItem.installedRow && !!rowItem.row.installed
                readonly property bool removable: !rowItem.partOfEmaki && (rowItem.row.actions || []).includes("remove") && (rowItem.installedRow || rowItem.packageRow && rowItem.row.installed)
                readonly property bool installable: !rowItem.installedRow && !rowItem.row.installed
                readonly property real rowWidth: {
                    const widths = [[flags, flagsLabel], [rowItem.aur, votesTag], [rowItem.partOfEmaki, emakiTag], [installedTag, installedTagItem],
                                    [removable, removeButton], [installable, installButton]].filter(pair => pair[0]).map(pair => pair[1].implicitWidth);
                    return widths.reduce((total, width) => total + width, 0) + Math.max(0, widths.length - 1) * columnSpacing;
                }
                visible: flags || rowItem.aur || rowItem.partOfEmaki || installedTag || removable || installable
                Layout.row: rowItem.stacked ? 1 : 0; Layout.column: rowItem.stacked ? 1 : 2
                Layout.fillWidth: rowItem.stacked
                // Measured from the row, not from itself: its own width follows its columns.
                readonly property real room: rowItem.width - 8 - 40 - rowLine.columnSpacing
                columns: !rowItem.stacked || room >= rowWidth ? 6 : 1
                columnSpacing: 14
                rowSpacing: 6
                ManagerLabel {
                    id: flagsLabel
                    visible: rowEnd.flags
                    text: [rowItem.row.orphan ? qsTranslate("ManagerView", "no maintainer") : "", rowItem.row.outOfDate ? qsTranslate("ManagerView", "out of date") : ""].filter(Boolean).join(" · ")
                    Layout.fillWidth: rowEnd.columns === 1; Layout.maximumWidth: implicitWidth; font.pixelSize: 12; color: Shell.ShellPalette.managerError
                }
                Tag {
                    id: votesTag
                    objectName: "aur-votes"
                    visible: rowItem.aur
                    text: rowItem.row.votes === null || rowItem.row.votes === undefined ? qsTranslate("ManagerView", "Votes unknown") : Shell.Translation.englishPlural(qsTranslate("ManagerView", "%1 vote(s)", "", rowItem.row.votes), rowItem.row.votes).arg(Logic.groupedCount(rowItem.row.votes))
                }
                Tag {
                    id: emakiTag
                    objectName: "part-of-emaki"
                    visible: rowItem.partOfEmaki
                    text: qsTranslate("ManagerView", "Part of Emaki")
                }
                Tag {
                    id: installedTagItem
                    visible: rowEnd.installedTag
                    text: qsTranslate("ManagerView", "Installed")
                }
                ManagerButton {
                    id: removeButton
                    objectName: "remove"
                    // One under another, a button narrower than its label shortens it.
                    Layout.fillWidth: rowEnd.columns === 1; Layout.maximumWidth: implicitWidth; Layout.minimumWidth: rowEnd.columns === 1 ? 0 : implicitWidth
                    squeezed: rowEnd.columns === 1 && rowEnd.room < implicitWidth
                    visible: rowEnd.removable
                    text: qsTranslate("ManagerView", "Remove"); danger: true
                    enabled: !view.programs.busy && !view.programs.externalBusy && (rowItem.row.actions || []).includes("remove")
                    onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "remove") : view.programs.prepareRemove(rowItem.row)
                }
                ManagerButton {
                    id: installButton
                    objectName: "install"
                    Layout.fillWidth: rowEnd.columns === 1; Layout.maximumWidth: implicitWidth; Layout.minimumWidth: rowEnd.columns === 1 ? 0 : implicitWidth
                    squeezed: rowEnd.columns === 1 && rowEnd.room < implicitWidth
                    visible: rowEnd.installable
                    text: rowItem.aur ? qsTranslate("ManagerView", "Build and install") : qsTranslate("ManagerView", "Install")
                    primary: true
                    enabled: (!view.programs.busy || view.programs.backgroundBusy) && !view.programs.externalBusy && !rowItem.aurRefused
                        && !view.programs.refreshPending && ((rowItem.packageRow || rowItem.aur ? rowItem.row : Logic.edition(rowItem.row) || {}).actions || []).includes("install")
                    onClicked: rowItem.packageRow ? view.programs.preparePackage(rowItem.row, "install") : view.installProgram(rowItem.row)
                }
            }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.alpha(Shell.ShellPalette.managerInk, .12) }
    }
    C.Popup {
        id: preview
        objectName: "program-preview"
        parent: view
        anchors.centerIn: parent
        width: Math.min(470, view.width - (view.narrow ? 16 : 40))
        height: Math.min(previewColumn.implicitHeight + 2 * padding, view.height - (view.narrow ? 16 : 40))
        padding: view.narrow ? 16 : 24
        modal: true
        focus: true
        closePolicy: C.Popup.NoAutoClose
        visible: view.programs.previewOpen && !view.programs.mutating
        background: Rectangle { radius: 18; color: Shell.ShellPalette.managerPanel; border.width: 2; border.color: Shell.ShellPalette.accent }
        C.Overlay.modal: Rectangle { color: Qt.alpha(Shell.ShellPalette.managerInk, .26) }
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
            C.ScrollView {
                // A window shorter than the preview scrolls everything above its buttons.
                id: previewBody
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: previewText.implicitHeight
                Layout.minimumHeight: 0
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    id: previewText
                    width: previewBody.availableWidth
                    spacing: 14
                    ManagerLabel {
                        text: previewColumn.action === "remove" ? qsTranslate("ManagerView", "Remove %1?").arg(previewColumn.programName)
                            : previewColumn.action === "finish" ? qsTranslate("ManagerView", "Finish setting up %1?").arg(previewColumn.programName)
                            : previewColumn.action === "undo" ? qsTranslate("ManagerView", "Undo the install of %1?").arg(previewColumn.programName)
                            : previewColumn.aurPlan ? qsTranslate("ManagerView", "Build %1 from the AUR?").arg(previewColumn.programName)
                            : view.programs.selectedEdition?.kind === "arch" ? qsTranslate("ManagerView", "Installing also brings the system up to date.")
                            : qsTranslate("ManagerView", "Install %1?").arg(previewColumn.programName)
                        font.pixelSize: 21; font.weight: Font.DemiBold
                    }
                    ManagerLabel {
                        visible: !!view.programs.planTarget && previewColumn.action === "install"
                        text: qsTranslate("ManagerView", "Installing also brings the system up to date.")
                        color: Shell.ShellPalette.managerDim
                    }
                    ManagerLabel {
                        visible: previewColumn.action === "finish" && !!view.programs.plan
                        text: view.programs.plan?.transaction?.message || ""
                        color: Shell.ShellPalette.managerDim
                    }
                    ManagerLabel {
                        visible: view.programs.planTarget === "docker" && previewColumn.action === "install" && view.programs.dockerAccess
                        text: qsTranslate("ManagerView", "Adding you to the Docker group gives you root-level control of this computer.")
                        color: Shell.ShellPalette.managerError
                    }
                    ManagerLabel {
                        visible: view.programs.planTarget === "docker" && previewColumn.action === "install" && view.programs.dockerAccess
                        text: qsTranslate("ManagerView", "The group takes effect after you sign out and sign back in.")
                    }
                    ManagerLabel { objectName: "program-preview-error"; text: view.programs.message; visible: text !== ""; color: Shell.ShellPalette.managerError }
                    ManagerLabel {
                        visible: previewColumn.aurPlan
                        text: qsTranslate("ManagerView", "Nobody checked how these programs are built, neither Emaki nor Arch. Their build instructions run on this computer while the program is built. Building also brings the system up to date first.")
                        font.pixelSize: 13
                    }
                    ManagerLabel {
                        visible: previewColumn.action !== "finish" && !previewColumn.snapshotUndo && (previewColumn.planning || !!view.programs.plan && !previewColumn.aurPlan)
                        text: previewColumn.planning ? (previewColumn.aurPlan ? qsTranslate("ManagerView", "Reading the build instructions and what they need…") : qsTranslate("ManagerView", "Checking what will change…"))
                            : previewColumn.action === "remove" || previewColumn.action === "undo" ? (view.programs.planRows.length === 1 ? qsTranslate("ManagerView", "This goes:") : qsTranslate("ManagerView", "These %n packages go:", "", view.programs.planRows.length))
                            : view.programs.planRows.length === 1 ? qsTranslate("ManagerView", "This changes:") : qsTranslate("ManagerView", "These %n packages change:", "", view.programs.planRows.length)
                    }
                    ManagerLabel {
                        visible: previewColumn.action === "install" && view.programs.selectedEdition?.kind === "arch" && !!view.programs.plan && !previewColumn.planning
                        text: view.planDownload()
                        color: Shell.ShellPalette.managerDim; font.pixelSize: 13
                    }
                    C.ScrollView {
                        Layout.fillWidth: true
                        visible: previewColumn.action !== "finish" && !previewColumn.snapshotUndo
                        Layout.preferredHeight: Math.min(previewColumn.aurPlan ? 300 : 210, previewRows.implicitHeight)
                        contentWidth: availableWidth
                        clip: true
                        ColumnLayout {
                            id: previewRows
                            width: parent.width
                            spacing: 8
                            Repeater {
                                model: view.previewPlanRows
                                PlanRow {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ManagerLabel { text: parent.modelData.name || ""; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                    ManagerLabel {
                                        visible: view.programs.planAction === "install" && view.isPlanTarget(parent.modelData)
                                        text: parent.modelData.detail || ""
                                        Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12
                                    }
                                    ManagerLabel {
                                        text: view.isPlanTarget(parent.modelData) ? qsTranslate("ManagerView", "the program")
                                            : view.programs.planAction === "undo" ? qsTranslate("ManagerView", "came with it")
                                            : view.programs.planAction === "remove" && view.programs.selectedEdition?.kind !== "flatpak" ? qsTranslate("ManagerView", "nothing else needs it") : parent.modelData.detail || ""
                                        Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12
                                    }
                                }
                            }
                            ManagerLabel { visible: previewColumn.builds.length > 0; text: qsTranslate("ManagerView", "BUILT FROM THE AUR (%1)").arg(previewColumn.builds.length); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
                            Repeater {
                                model: previewColumn.builds
                                ColumnLayout {
                                    id: buildRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 2
                                    PlanRow {
                                        Layout.fillWidth: true
                                        ManagerLabel { text: buildRow.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                        ManagerLabel { text: buildRow.modelData.target ? qsTranslate("ManagerView", "the program") : qsTranslate("ManagerView", "it needs this"); Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                                    }
                                    ManagerLabel {
                                        text: [buildRow.modelData.version, buildRow.modelData.maintainer, buildRow.modelData.outOfDate].filter(Boolean).join(" · ")
                                        color: buildRow.modelData.outOfDate || buildRow.modelData.orphan ? Shell.ShellPalette.managerError : Shell.ShellPalette.managerDim; font.pixelSize: 12
                                    }
                                    ManagerButton {
                                        visible: buildRow.modelData.recipe !== ""
                                        text: qsTranslate("ManagerView", "See how it is built · %1").arg(buildRow.modelData.commit)
                                        // A narrow preview shortens the commit rather than widening the list.
                                        Layout.fillWidth: true; Layout.maximumWidth: implicitWidth
                                        squeezed: buildRow.width < implicitWidth
                                        quiet: true; implicitHeight: 26; leftPadding: 0; font.pixelSize: 12
                                        onClicked: Qt.openUrlExternally(buildRow.modelData.recipe)
                                    }
                                }
                            }
                            ManagerLabel { visible: previewColumn.runPulls.length > 0; text: qsTranslate("ManagerView", "FROM ARCH, STAYS INSTALLED (%1)").arg(previewColumn.runPulls.length); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; Layout.topMargin: 6 }
                            Repeater {
                                model: previewColumn.runPulls
                                PlanRow {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                    ManagerLabel { text: parent.modelData.repository; Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                                }
                            }
                            ManagerLabel { visible: previewColumn.buildPulls.length > 0; text: qsTranslate("ManagerView", "FROM ARCH, ONLY TO BUILD IT (%1) — removed afterwards").arg(previewColumn.buildPulls.length); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; Layout.topMargin: 6 }
                            Repeater {
                                model: previewColumn.buildPulls
                                PlanRow {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                    ManagerLabel { text: parent.modelData.repository; Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                                }
                            }
                            ManagerLabel { visible: previewColumn.kept.length > 0; text: qsTranslate("ManagerView", "THESE STAY"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; Layout.topMargin: 6 }
                            Repeater {
                                model: previewColumn.kept
                                PlanRow {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ManagerLabel { text: parent.modelData.name; font.family: "Iosevka Nerd Font Mono"; font.pixelSize: 13 }
                                    ManagerLabel { text: parent.modelData.detail; Layout.fillWidth: false; color: Shell.ShellPalette.managerDim; font.pixelSize: 12 }
                                }
                            }
                        }
                    }
                    ManagerLabel {
                        visible: previewColumn.action === "remove"
                        text: view.programs.selectedEdition?.kind === "flatpak" ? qsTranslate("ManagerView", "Your files stay. The program’s own settings stay too, so they are back if you install it again.") : qsTranslate("ManagerView", "Your files and the program’s settings in your home folder stay as they are.")
                        color: Shell.ShellPalette.managerDim; font.pixelSize: 13
                    }
                    ManagerLabel {
                        visible: previewColumn.action === "undo" && !previewColumn.planning
                        text: previewColumn.snapshotUndo ? (view.programs.plan?.transaction?.message || "") : [qsTranslate("ManagerView", "Exactly what this install added goes, in one step. Your files and the program’s settings in your home folder stay."),
                               (view.programs.plan?.transaction?.upgraded || []).length ? qsTranslate("ManagerView", "Updates that came with the install stay.") : "",
                               view.programs.plan?.transaction?.snapshot ? qsTranslate("ManagerView", "The system saved before the install is still in Updates, under If something broke.") : ""].filter(Boolean).join(" ")
                        color: Shell.ShellPalette.managerDim; font.pixelSize: 13
                    }
                }
            }
            ManagerActions {
                ManagerButton { text: qsTranslate("ManagerView", "Cancel"); enabled: !view.programs.busy; onClicked: view.programs.cancelPlan() }
                ManagerButton {
                    objectName: "confirm-program-operation"
                    text: previewColumn.action === "finish" ? qsTranslate("ManagerView", "Finish setup") : previewColumn.snapshotUndo ? qsTranslate("ManagerView", "Restore and restart later") : previewColumn.action === "remove" ? qsTranslate("ManagerView", "Remove") : previewColumn.action === "undo" ? qsTranslate("ManagerView", "Undo install") : previewColumn.aurPlan ? qsTranslate("ManagerView", "Build and install") : qsTranslate("ManagerView", "Install")
                    primary: true; danger: previewColumn.action === "remove" || previewColumn.action === "undo"
                    enabled: !view.programs.busy && !view.programs.externalBusy && view.programs.plan?.ok === true && !!view.programs.plan?.token
                    onClicked: {
                        if (!view.programs.planTarget && view.programs.planAction !== "remove" && view.page !== "program") {
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
        // Each time it opens, the warning starts at the top of its text, wherever it was left scrolled.
        onAboutToShow: warningBody.contentItem.contentY = 0
        background: Rectangle { radius: 16; color: Shell.ShellPalette.managerPanel }
        C.Overlay.modal: Rectangle { color: Qt.alpha(Shell.ShellPalette.managerInk, .26) }
        C.ScrollView {
            id: warningBody
            anchors { left: parent.left; right: parent.right; top: parent.top; bottom: view.shortWindow ? parent.bottom : warningFooter.top; bottomMargin: view.shortWindow ? 0 : 16 }
            objectName: "aur-warning-body"
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                id: warningColumn
                width: Math.min(640, aurWarning.width - (view.narrow ? 32 : 80))
                x: (aurWarning.width - width) / 2
                spacing: 16
                Item { implicitHeight: 34 }
                ManagerLabel { text: qsTranslate("ManagerView", "AUR — programs from the community"); font.pixelSize: 28; font.weight: Font.DemiBold }
                ManagerLabel { text: qsTranslate("ManagerView", "WHY IT IS USEFUL"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; font.letterSpacing: .8 }
                ManagerLabel { text: qsTranslate("ManagerView", "Here is what the regular catalog lacks: programs that are not in Arch or Flathub, official builds of some programs, the newest versions, rare tools, drivers and fonts. If a program is nowhere else, it is probably here. Someone from the community published it and maintains it."); lineHeight: 1.35 }
                ManagerLabel { text: qsTranslate("ManagerView", "WHAT YOU RISK"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; font.letterSpacing: .8 }
                ManagerLabel { text: qsTranslate("ManagerView", "Anyone can publish a program here and nobody checks it, neither Emaki nor Arch. A program can fail to work, break your system, or contain malware — for example, code that steals passwords or files. You cannot tell a good one from a bad one in advance here."); lineHeight: 1.35 }
                ManagerLabel { text: qsTranslate("ManagerView", "WHAT EMAKI DOES"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; font.letterSpacing: .8 }
                ManagerLabel {
                    text: qsTranslate("ManagerView", "A system snapshot before every install. If something broke, the install can be undone. A snapshot does not protect against stolen data.")
                    lineHeight: 1.35
                }
                ManagerLabel {
                    objectName: "aur-warning-no-snapshots"
                    visible: !view.programs.aurSnapshots
                    text: view.noSnapshotsText
                    color: Shell.ShellPalette.managerError; font.weight: Font.DemiBold; lineHeight: 1.35
                }
                ManagerLabel { text: qsTranslate("ManagerView", "THE DECISION IS YOURS"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim; font.letterSpacing: .8 }
                ManagerLabel { text: qsTranslate("ManagerView", "Installing from here, you are responsible for the consequences."); lineHeight: 1.35 }
                Item { implicitHeight: 8 }
                Loader {
                    // In a very short window the choice scrolls after the text instead of covering most of it.
                    active: view.shortWindow
                    visible: active
                    Layout.fillWidth: true
                    Layout.bottomMargin: 16
                    sourceComponent: warningChoice
                }
            }
        }
        Loader {
            id: warningFooter
            active: !view.shortWindow
            visible: active
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 24 }
            width: Math.min(640, aurWarning.width - (view.narrow ? 32 : 80))
            sourceComponent: warningChoice
        }
        Component {
            id: warningChoice
            ColumnLayout {
                spacing: 16
                C.CheckBox {
                    id: dontShowBox
                    objectName: "aur-dont-show"
                    text: qsTranslate("ManagerView", "Don’t show this again")
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
                        color: dontShowBox.checked ? Shell.ShellPalette.accent : Shell.ShellPalette.managerBackground
                        border.width: dontShowBox.activeFocus ? 2 : 1
                        border.color: dontShowBox.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .35)
                        Text { anchors.centerIn: parent; visible: dontShowBox.checked; text: "✓"; color: Shell.ShellPalette.accentText; font.pixelSize: 13; font.bold: true }
                    }
                    contentItem: Text {
                        leftPadding: dontShowBox.indicator.width + 10
                        text: dontShowBox.text
                        font: dontShowBox.font
                        color: Shell.ShellPalette.managerInk
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                ManagerActions {
                    ManagerButton { objectName: "aur-not-now"; text: qsTranslate("ManagerView", "Not now"); onClicked: aurWarning.close() }
                    ManagerButton {
                        objectName: "aur-accept"
                        text: qsTranslate("ManagerView", "Got it, open the AUR")
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
}
