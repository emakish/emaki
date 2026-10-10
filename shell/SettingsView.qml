// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"
import "settings/SettingsIndex.js" as Index

FocusScope {
    id: root
    required property var catalog
    required property SystemService service
    required property NiriService niri
    property var notificationStore: null
    property var displays: null
    required property var availablePages
    property string page: "panel"
    property bool embedded: false
    property real surfaceRadius: 16
    property string wallpaperTexture: ""
    property real dpr: 1
    property bool historyShown: false
    readonly property alias search: search
    readonly property alias sidebar: sidebar
    readonly property alias scroller: scroll
    readonly property var section: Index.section(page)
    readonly property var coreKeys: pageRegistry.coreKeys
    readonly property var quickRows: pageRegistry.searchAnchors
    readonly property var results: Index.search(search.text, availablePages, coreKeys.concat(Object.keys(quickRows))).map(result => ({
                id: result.id,
                title: result.title,
                explanation: result.explanation || "",
                page: result.page,
                pageTitle: result.pageTitle,
                key: result.key,
                enabled: result.enabled,
                target: result.key || quickRows[result.id] || ""
            }))
    readonly property var resultGroups: Index.sections.map(section => ({
                title: section.title,
                rows: results.filter(result => result.page === section.id)
            })).filter(group => group.rows.length)
    function highlighted(value): string {
        const text = String(value || "");
        const words = search.text.trim().toLowerCase().split(/\s+/).filter(word => word.length);
        let result = "";
        for (let i = 0; i < text.length; ) {
            const word = words.find(word => text.slice(i, i + word.length).toLowerCase() === word);
            const count = word ? word.length : 1;
            const escaped = text.slice(i, i + count).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            result += word ? "<b>" + escaped + "</b>" : escaped;
            i += count;
        }
        return result;
    }
    readonly property bool searching: search.text.trim() !== ""
    onPageChanged: Qt.callLater(revealSidebar)
    onSearchingChanged: Qt.callLater(revealSidebar)
    onVisibleChanged: Qt.callLater(revealSidebar)
    Component.onCompleted: Qt.callLater(revealSidebar)
    readonly property string accountName: service.accountName
    readonly property var pageKeys: pageRegistry.definition(page)?.resetKeys || []
    function canReset(key): bool {
        const row = catalog.row(key);
        return !!row && row.editable !== false && row.value !== undefined && row.default !== undefined && JSON.stringify(row.value) !== JSON.stringify(row.default);
    }
    readonly property bool pageModified: pageKeys.some(key => canReset(key))
    signal closeRequested
    function flushPending(): void {
        for (const item of descendants(pageColumn))
            if (typeof item.flushPending === "function")
                item.flushPending();
    }
    function handleEscape(): void {
        if (search.text !== "") {
            search.text = "";
            search.forceActiveFocus();
        } else {
            flushPending();
            closeRequested();
        }
    }
    function openFirstResult(): void {
        const result = results.find(item => item.enabled);
        if (result)
            navigate(result.page, result.target);
    }
    function takeFocus(): void {
        search.forceActiveFocus();
    }
    property string revealKey: ""
    Timer {
        id: revealTimer
        interval: 0
        onTriggered: root.reveal(root.revealKey)
    }
    function navigate(id: string, key: string): bool {
        if (!availablePages.includes(id))
            return false;
        page = id;
        historyShown = false;
        search.text = "";
        scroll.contentY = 0;
        revealTimer.stop();
        revealKey = key || "";
        if (revealKey)
            revealTimer.start();
        return true;
    }
    function descendants(item): var {
        let found = [item];
        for (const child of item.children || [])
            found = found.concat(descendants(child));
        return found;
    }
    function reveal(key): void {
        const row = descendants(pageColumn).find(item => item.objectName === (key.startsWith("settings-") ? key : "setting-" + key));
        if (!row)
            return;
        const point = row.mapToItem(pageColumn, 0, 0);
        scroll.contentY = Math.max(0, Math.min(point.y - 24, scroll.contentHeight - scroll.height));
        const control = descendants(row).find(item => item.activeFocusOnTab && item.enabled && item.visible);
        control?.forceActiveFocus(Qt.TabFocusReason);
    }
    function keepFocusVisible(): void {
        const item = root.Window.window?.activeFocusItem;
        if (!item)
            return;
        let ancestor = item;
        while (ancestor && ancestor !== pageColumn && ancestor !== navColumn)
            ancestor = ancestor.parent;
        if (!ancestor)
            return;
        const viewport = ancestor === pageColumn ? scroll : nav;
        const point = item.mapToItem(ancestor, 0, 0);
        const top = point.y + (ancestor === pageColumn ? pageColumn.y : 0);
        if (top < viewport.contentY)
            viewport.contentY = Math.max(0, top - 12);
        else if (top + item.height > viewport.contentY + viewport.height)
            viewport.contentY = Math.max(0, Math.min(viewport.contentHeight - viewport.height, top + item.height - viewport.height + 12));
    }
    Connections {
        target: root.Window.window
        function onActiveFocusItemChanged(): void {
            root.keepFocusVisible();
        }
    }
    function sidebarButton(entry: var): Button {
        return entry?.button as Button;
    }
    function revealSidebar(): void {
        if (searching || !visible)
            return;
        for (let i = 0; i < sidebar.count; ++i) {
            const button = sidebarButton(sidebar.itemAt(i));
            if (button?.objectName !== "section-" + page)
                continue;
            const top = button.mapToItem(navColumn, 0, 0).y;
            const bottom = top + button.height;
            const maximum = Math.max(0, nav.contentHeight - nav.height);
            if (top < nav.contentY)
                nav.contentY = Math.max(0, top);
            else if (bottom > nav.contentY + nav.height)
                nav.contentY = Math.min(maximum, bottom - nav.height);
            return;
        }
    }
    function loadedFooter(loadedPage: var): Component {
        return loadedPage && "footerContent" in loadedPage ? loadedPage.footerContent as Component : null;
    }
    function sidebarStep(current, step): void {
        const targets = [];
        for (let i = 0; i < sidebar.count; ++i) {
            const button = sidebarButton(sidebar.itemAt(i));
            if (button?.enabled && button.visible)
                targets.push(button);
        }
        if (!targets.length)
            return;
        const index = targets.indexOf(current);
        const next = targets[(index + step + targets.length) % targets.length];
        next.forceActiveFocus(Qt.TabFocusReason);
        const point = next.mapToItem(navColumn, 0, 0);
        if (point.y < nav.contentY)
            nav.contentY = point.y;
        else if (point.y + next.height > nav.contentY + nav.height)
            nav.contentY = Math.min(nav.contentHeight - nav.height, point.y + next.height - nav.height);
    }
    function historyValue(value): string {
        if (value === undefined || value === null)
            return "Default";
        if (typeof value === "boolean")
            return value ? "On" : "Off";
        return typeof value === "string" ? value : JSON.stringify(value);
    }
    function wasUndone(id): bool {
        return root.catalog.history.some(entry => entry.undo_of === id);
    }
    function settingTitle(key): string {
        for (const section of Index.sections) {
            const item = section.items.find(item => item.key === key);
            if (item)
                return item.title;
        }
        return "Setting";
    }
    Keys.priority: Keys.BeforeItem
    Keys.onEscapePressed: root.handleEscape()
    Keys.onPressed: event => {
        if ((event.key === Qt.Key_F && (event.modifiers & Qt.ControlModifier)) || (event.key === Qt.Key_Slash && !search.activeFocus)) {
            search.forceActiveFocus();
            event.accepted = true;
        }
    }
    SettingsGlass {
        visible: !root.embedded
        anchors.fill: parent
        wallpaperTexture: root.wallpaperTexture
        dpr: root.dpr
    }
    Rectangle {
        visible: root.embedded
        anchors.fill: parent
        bottomLeftRadius: root.surfaceRadius
        bottomRightRadius: root.surfaceRadius
        color: Qt.rgba(1, 246 / 255, 240 / 255, .58)
    }
    Rectangle {
        id: side
        x: 2
        y: 2
        width: root.width < 980 ? 248 : 286
        height: parent.height - 4
        radius: 14
        topLeftRadius: root.embedded ? 0 : radius
        topRightRadius: root.embedded ? 0 : radius
        bottomLeftRadius: root.embedded ? Math.max(0, root.surfaceRadius - 2) : radius
        antialiasing: true
        color: Qt.rgba(1, 246 / 255, 240 / 255, .22)
        Rectangle {
            anchors.right: parent.right
            width: 1
            height: parent.height
            color: SettingsTheme.rim
        }
        Item {
            id: account
            x: 26
            y: 30
            width: parent.width - 52
            height: 46
            Rectangle {
                width: 42
                height: 42
                radius: 21
                antialiasing: true
                border.width: 1
                border.color: Qt.rgba(1, 1, 1, .35)
                gradient: Gradient {
                    GradientStop {
                        position: 0
                        color: "#f2cc7e"
                    }
                    GradientStop {
                        position: .55
                        color: SettingsTheme.accent
                    }
                    GradientStop {
                        position: 1
                        color: "#e05a4f"
                    }
                }
                Text {
                    anchors.centerIn: parent
                    text: root.accountName.charAt(0).toUpperCase()
                    color: "#fff8f3"
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                }
            }
            Column {
                x: 54
                width: parent.width - 54
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Text {
                    width: parent.width
                    text: root.accountName
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    color: SettingsTheme.ink
                }
                Text {
                    width: parent.width
                    text: root.service.accountAdministrator ? "Administrator · this computer" : "This computer"
                    elide: Text.ElideRight
                    font.family: SettingsTheme.fontFamily
                    font.pixelSize: 12
                    color: SettingsTheme.dim
                }
            }
        }
        TextField {
            id: search
            objectName: "settings-search"
            x: 16
            y: 94
            width: parent.width - 32
            height: 36
            placeholderText: "Search settings"
            color: SettingsTheme.ink
            placeholderTextColor: SettingsTheme.faint
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 14
            selectByMouse: true
            leftPadding: 34
            rightPadding: clear.visible ? 34 : 12
            background: Rectangle {
                radius: 12
                antialiasing: true
                color: SettingsTheme.field
                border.width: search.activeFocus ? 2 : 1
                border.color: search.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
            }
            Keys.onReturnPressed: root.openFirstResult()
            Keys.onEnterPressed: root.openFirstResult()
            Keys.onDownPressed: root.sidebarStep(null, 1)
            Keys.onEscapePressed: root.handleEscape()
            Item {
                x: 12
                anchors.verticalCenter: parent.verticalCenter
                width: 14
                height: 14
                Rectangle {
                    width: 9
                    height: 9
                    radius: 4.5
                    antialiasing: true
                    color: "transparent"
                    border.width: 1
                    border.color: SettingsTheme.dim
                }
                Rectangle {
                    x: 8
                    y: 8
                    width: 6
                    height: 1
                    rotation: 45
                    transformOrigin: Item.Left
                    antialiasing: true
                    color: SettingsTheme.dim
                }
            }
            SettingsButton {
                id: clear
                visible: search.text !== ""
                width: 28
                height: 28
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                text: "×"
                Accessible.name: "Clear search"
                onClicked: {
                    search.text = "";
                    search.forceActiveFocus();
                }
            }
        }
        Flickable {
            id: nav
            objectName: "settings-sidebar-scroll"
            x: 12
            y: 140
            width: parent.width - 24
            height: parent.height - y - 14
            contentWidth: width
            contentHeight: navColumn.implicitHeight
            onContentHeightChanged: Qt.callLater(root.revealSidebar)
            onHeightChanged: Qt.callLater(root.revealSidebar)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
            }
            Column {
                id: navColumn
                width: nav.width - 6
                Repeater {
                    id: sidebar
                    model: Index.sections
                    delegate: Column {
                        id: entry
                        required property var modelData
                        required property int index
                        readonly property alias button: sectionButton
                        readonly property int matches: root.results.filter(result => result.page === modelData.id).length
                        visible: !root.searching || matches > 0
                        width: navColumn.width
                        Text {
                            visible: !root.searching && (entry.index === 0 || Index.sections[entry.index - 1].group !== entry.modelData.group)
                            text: entry.modelData.group.toUpperCase()
                            color: SettingsTheme.faint
                            font.family: SettingsTheme.fontFamily
                            font.pixelSize: 11
                            font.letterSpacing: .88
                            font.weight: Font.DemiBold
                            topPadding: 14
                            bottomPadding: 5
                            leftPadding: 10
                        }
                        Button {
                            id: sectionButton
                            objectName: "section-" + entry.modelData.id
                            width: parent.width
                            height: 36
                            enabled: root.availablePages.includes(entry.modelData.id)
                            focusPolicy: enabled ? Qt.StrongFocus : Qt.NoFocus
                            hoverEnabled: enabled
                            padding: 0
                            opacity: enabled ? 1 : .38
                            Accessible.name: entry.modelData.title
                            Keys.onReturnPressed: clicked()
                            Keys.onEnterPressed: clicked()
                            onClicked: root.navigate(entry.modelData.id)
                            Keys.onUpPressed: root.sidebarStep(sectionButton, -1)
                            Keys.onDownPressed: root.sidebarStep(sectionButton, 1)
                            Keys.onEscapePressed: root.handleEscape()
                            background: Rectangle {
                                readonly property bool selected: root.page === entry.modelData.id && !root.searching && !root.historyShown
                                radius: 11
                                antialiasing: true
                                color: !sectionButton.enabled ? "transparent" : selected ? SettingsTheme.dropHover : sectionButton.hovered ? SettingsTheme.drop : "transparent"
                                border.width: sectionButton.activeFocus ? 2 : selected && sectionButton.enabled ? 1 : 0
                                border.color: sectionButton.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
                            }
                            contentItem: Item {
                                Rectangle {
                                    x: 6
                                    width: 26
                                    height: 26
                                    radius: 8
                                    antialiasing: true
                                    border.width: 1
                                    border.color: Qt.rgba(1, 1, 1, .24)
                                    anchors.verticalCenter: parent.verticalCenter
                                    gradient: Gradient {
                                        GradientStop {
                                            position: 0
                                            color: entry.modelData.colors[0]
                                        }
                                        GradientStop {
                                            position: 1
                                            color: entry.modelData.colors[1]
                                        }
                                    }
                                    Image {
                                        anchors.centerIn: parent
                                        width: 16
                                        height: 16
                                        source: Qt.resolvedUrl("settings/icons/" + entry.modelData.id + ".svg")
                                        sourceSize: Qt.size(24, 24)
                                    }
                                }
                                Text {
                                    x: 43
                                    width: parent.width - x - (root.searching ? 35 : 6)
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: entry.modelData.title
                                    textFormat: Text.PlainText
                                    color: SettingsTheme.ink
                                    font.family: SettingsTheme.fontFamily
                                    font.pixelSize: 14
                                    font.weight: root.page === entry.modelData.id ? Font.DemiBold : Font.Normal
                                    elide: Text.ElideRight
                                }
                                Text {
                                    anchors.right: parent.right
                                    anchors.rightMargin: 10
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: root.searching
                                    text: entry.matches
                                    color: SettingsTheme.accentInk
                                    font.family: SettingsTheme.fontFamily
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Item {
        id: main
        x: side.width + 2
        y: 2
        width: root.width - x - 2
        height: root.height - 4
        Flickable {
            id: scroll
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: pageFooter.top
            contentWidth: width
            contentHeight: pageColumn.implicitHeight + 72
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {
                policy: ScrollBar.AsNeeded
            }
            Column {
                id: pageColumn
                x: root.width < 980 ? 24 : 40
                y: 30
                width: Math.min(820, scroll.width - 2 * x)
                spacing: 26
                Item {
                    width: parent.width
                    implicitHeight: Math.max(52, heading.implicitHeight, resetPage.visible ? resetPage.height : 0)
                    Rectangle {
                        visible: !root.searching && !root.historyShown && !!root.section
                        width: 52
                        height: 52
                        anchors.verticalCenter: parent.verticalCenter
                        radius: 15
                        antialiasing: true
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, .35)
                        gradient: Gradient {
                            GradientStop {
                                position: 0
                                color: root.section?.colors[0] ?? SettingsTheme.accent
                            }
                            GradientStop {
                                position: 1
                                color: root.section?.colors[1] ?? SettingsTheme.accent
                            }
                        }
                        Image {
                            anchors.centerIn: parent
                            width: 30
                            height: 30
                            source: root.section ? Qt.resolvedUrl("settings/icons/" + root.section.id + ".svg") : ""
                            sourceSize: Qt.size(48, 48)
                        }
                    }
                    SettingsPageHeader {
                        id: heading
                        x: root.searching || root.historyShown ? 0 : 68
                        width: parent.width - x - (resetPage.visible ? resetPage.width + 18 : 26)
                        title: root.searching ? root.results.length + (root.results.length === 1 ? " result for “" : " results for “") + search.text.trim() + "”" : root.historyShown ? "History" : root.section?.title ?? "Settings"
                        explanation: root.searching ? "Find a setting by its name or what it does." : root.historyShown ? "Changes to your settings can be undone here." : pageRegistry.definition(root.page)?.description || root.section?.description || ""
                    }
                    SettingsButton {
                        id: resetPage
                        objectName: "settings-reset-page"
                        anchors.right: parent.right
                        y: 4
                        visible: !root.searching && !root.historyShown && root.pageModified
                        enabled: !root.catalog.writing
                        text: "Reset this page"
                        onClicked: {
                            for (const key of root.pageKeys) {
                                if (root.canReset(key))
                                    root.catalog.reset(key);
                            }
                        }
                    }
                }
                Column {
                    visible: root.searching
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: root.resultGroups
                        delegate: Column {
                            id: resultGroup
                            required property var modelData
                            width: parent.width
                            spacing: 8
                            Text {
                                text: resultGroup.modelData.title
                                color: SettingsTheme.dim
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                                topPadding: 8
                            }
                            Rectangle {
                                width: parent.width
                                height: resultRows.implicitHeight
                                radius: 12
                                color: SettingsTheme.card
                                border.width: 1
                                border.color: SettingsTheme.rim
                                Column {
                                    id: resultRows
                                    width: parent.width
                                    Repeater {
                                        model: resultGroup.modelData.rows
                                        delegate: Button {
                                            id: result
                                            required property var modelData
                                            required property int index
                                            width: resultRows.width
                                            height: 66
                                            leftPadding: 16
                                            rightPadding: 16
                                            enabled: modelData.enabled
                                            focusPolicy: enabled ? Qt.StrongFocus : Qt.NoFocus
                                            hoverEnabled: enabled
                                            Accessible.name: modelData.title + ", " + modelData.pageTitle
                                            Keys.onReturnPressed: clicked()
                                            Keys.onEnterPressed: clicked()
                                            onClicked: root.navigate(modelData.page, modelData.target)
                                            background: Rectangle {
                                                radius: 12
                                                color: result.hovered ? SettingsTheme.drop : "transparent"
                                                border.width: result.activeFocus ? 2 : 0
                                                border.color: SettingsTheme.accent
                                                Rectangle {
                                                    visible: result.index > 0
                                                    width: parent.width
                                                    height: 1
                                                    color: SettingsTheme.rim
                                                }
                                            }
                                            contentItem: Item {
                                                Text {
                                                    id: destination
                                                    anchors.right: parent.right
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    text: result.modelData.pageTitle + " ›"
                                                    color: SettingsTheme.faint
                                                    font.family: SettingsTheme.fontFamily
                                                    font.pixelSize: 12
                                                }
                                                Column {
                                                    width: parent.width - destination.width - 20
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    spacing: 3
                                                    Text {
                                                        width: parent.width
                                                        text: root.highlighted(result.modelData.title)
                                                        textFormat: Text.RichText
                                                        elide: Text.ElideRight
                                                        color: SettingsTheme.ink
                                                        font.family: SettingsTheme.fontFamily
                                                        font.pixelSize: 14
                                                    }
                                                    Text {
                                                        width: parent.width
                                                        text: result.modelData.explanation || ""
                                                        textFormat: Text.PlainText
                                                        elide: Text.ElideRight
                                                        color: SettingsTheme.dim
                                                        font.family: SettingsTheme.fontFamily
                                                        font.pixelSize: 12
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Text {
                        visible: root.results.length === 0
                        width: parent.width
                        text: "No settings found. Try another word."
                        color: SettingsTheme.dim
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }
                }
                Loader {
                    id: pageLoader
                    width: parent.width
                    onLoaded: {
                        if (item && "footerHosted" in item)
                            item.footerHosted = true;
                    }
                    active: !root.searching && !root.historyShown
                    sourceComponent: pageRegistry.definition(root.page)?.component || pageRegistry.fallbackComponent
                }
                Column {
                    visible: root.historyShown && !root.searching
                    width: parent.width
                    spacing: 12
                    Text {
                        visible: root.catalog.history.length === 0
                        text: "No changes yet."
                        color: SettingsTheme.dim
                        font.family: SettingsTheme.fontFamily
                        font.pixelSize: 14
                    }
                    Repeater {
                        model: root.catalog.history.slice().reverse().slice(0, 30)
                        delegate: SettingsCard {
                            id: historyEntry
                            required property var modelData
                            width: parent.width
                            title: modelData.kind === "undo" ? "Undone change" : "Settings change"
                            Text {
                                width: parent.width
                                text: historyEntry.modelData?.changes?.map(change => root.settingTitle(change.key) + ": " + root.historyValue(change.before) + " → " + root.historyValue(change.after)).join("\n") || "Settings recorded"
                                color: SettingsTheme.ink
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                visible: !!historyEntry.modelData.observed_at_unix_ms
                                text: new Date(historyEntry.modelData.observed_at_unix_ms || 0).toLocaleString(Qt.locale(), Locale.ShortFormat)
                                color: SettingsTheme.dim
                                font.family: SettingsTheme.fontFamily
                                font.pixelSize: 12
                            }
                            SettingsButton {
                                property var entry: historyEntry.modelData
                                text: root.wasUndone(entry.id) ? "Undone" : "Undo"
                                enabled: !root.wasUndone(entry.id) && !root.catalog.writing && entry?.changes?.length > 0 && ["set", "undo", "manual"].includes(entry?.kind)
                                onClicked: root.catalog.undo(entry.id)
                            }
                        }
                    }
                }
            }
        }
        Loader {
            id: pageFooter
            anchors.bottom: footer.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 24
            anchors.rightMargin: 24
            height: (item as Item) ? (item as Item).implicitHeight + 16 : 0
            sourceComponent: root.loadedFooter(pageLoader.item)
        }
        Rectangle {
            id: footer
            anchors.bottom: parent.bottom
            width: parent.width
            height: 58
            bottomRightRadius: root.embedded ? Math.max(0, root.surfaceRadius - 2) : 0
            color: Qt.rgba(1, 246 / 255, 240 / 255, .22)
            Rectangle {
                width: parent.width
                height: 1
                color: SettingsTheme.rim
            }
            Text {
                x: 24
                width: parent.width - 148
                anchors.verticalCenter: parent.verticalCenter
                text: root.catalog.writing ? "Saving…" : root.catalog.lastStatus === "rejected" ? (root.catalog.lastAction === "undo_conflict" ? "This setting changed again. Undo was not applied." : "Could not change the setting. Your previous value is kept.") : root.catalog.lastStatus === "unconfirmed" ? "Could not confirm the change. Reopen Settings to check." : root.catalog.lastStatus === "committed_history_pending" ? "Saved. History recovery is pending." : root.catalog.lastStatus === "committed" ? (root.catalog.sessionApplied ? "Applied · Undo in History" : "Saved to the isolated profile") : "Changes apply as you make them."
                color: root.catalog.lastStatus === "rejected" ? "#a01b45" : SettingsTheme.dim
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            SettingsButton {
                objectName: "settings-history"
                anchors.right: parent.right
                anchors.rightMargin: 24
                anchors.verticalCenter: parent.verticalCenter
                text: root.historyShown ? "Back" : "History"
                onClicked: {
                    root.historyShown = !root.historyShown;
                    search.text = "";
                    scroll.contentY = 0;
                }
            }
        }
    }
    SettingsNetworkService {
        id: networkSettingsService
        enabled: root.service.helpersEnabled && !root.searching && !root.historyShown && ["wifi", "network"].includes(root.page)
    }
    SettingsPageRegistry {
        id: pageRegistry
        catalog: root.catalog
        service: root.service
        niri: root.niri
        notificationStore: root.notificationStore
        displays: root.displays
        networkSettings: networkSettingsService
        page: root.page
        onNavigateRequested: (page, key) => root.navigate(page, key)
        onRevealRequested: key => {
            root.revealKey = key;
            revealTimer.restart();
        }
    }
    Rectangle {
        visible: !root.embedded
        anchors.fill: parent
        color: "transparent"
        radius: 16
        antialiasing: true
        border.width: 2
        border.color: SettingsTheme.edge
    }
}
