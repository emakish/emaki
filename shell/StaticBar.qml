// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Io
import "Keyboard.js" as Keyboard

Item {
    id: bar
    y: policy.barVisible ? 0 : -60
    Behavior on y {
        enabled: !bar.skipIntro
        NumberAnimation {
            duration: bar.policy.slideDurationMs
            easing.type: Easing.OutCubic
        }
    }
    property bool keyboardBoundary: false
    property Item privacyItem: null
    signal privacyClicked
    signal leaveKeyboard
    // The cell whose panel Escape just closed (ShellScene.keyboardBack). The bar's surface is
    // activated only after that panel has gone, and Surfaces calls takeFocus() again then:
    // until the cell really holds the keyboard there, every takeFocus() lands on it. While it
    // is hidden (the logo under the closing launcher) the first cell holds the keyboard and
    // hands it on once the cell is back. Moving away from that stand-in, or returnUntil
    // (ms since epoch) passing, ends the wait.
    property Item returnItem: null
    property real returnUntil: 0
    property Item returnHeld: null
    function takeFocus(): void {
        if (returnItem && Keyboard.targets(bar).includes(returnItem)) {
            Keyboard.reveal(returnItem);
            returnItem.forceActiveFocus(Qt.TabFocusReason);
            returnHeld = returnItem;
        } else {
            returnHeld = Keyboard.focusFirst(bar);
        }
    }
    // The focused cell's label (a system cell: its key), for status().
    function keyboardName(): string {
        return Keyboard.name(keyboardItem);
    }
    // ShellScene's frame timer asks while returnItem is set; false once nothing is left to do.
    function settleReturn(): bool {
        if (!returnItem)
            return false;
        const window = bar.Window.window;
        const item = keyboardItem;
        if (!keyboardBoundary || Date.now() > returnUntil || item === returnItem || (item && item !== returnHeld)) {
            returnItem = null;
            returnHeld = null;
            return false;
        }
        if (window && window.active && returnHeld !== returnItem && Keyboard.targets(bar).includes(returnItem))
            takeFocus();
        return true;
    }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            leaveKeyboard();
            event.accepted = true;
        } else {
            Keyboard.handle(bar, event);
            event.accepted = true;
        }
    }
    readonly property Item keyboardItem: {
        const item = Window.window ? Window.window.activeFocusItem : null;
        return keyboardBoundary && item && Keyboard.boundary(item) === bar ? item : null;
    }
    // The privacy pill draws its own hover drop for the keyboard (PrivacyPill.keyboardFocused):
    // this ring, square on a round pill and under it, showed only as dark corner arcs.
    readonly property bool privacyFocused: keyboardItem !== null && keyboardItem === privacyTarget
    FocusRing {
        anchors.fill: undefined
        shown: bar.keyboardItem !== null && !bar.privacyFocused
        readonly property rect targetRect: bar.keyboardItem ? bar.keyboardItem.parent.mapToItem(bar, bar.keyboardItem.x, bar.keyboardItem.y, bar.keyboardItem.width, bar.keyboardItem.height) : Qt.rect(0, 0, 0, 0)
        x: targetRect.x
        y: targetRect.y
        width: targetRect.width
        height: targetRect.height
    }
    property bool skipIntro: false
    required property BarPolicy policy
    required property HoverTip tip
    required property string borderMode
    required property bool launcherOpen
    required property bool launcherPresent
    // Right edge of the launcher panel while it is present (0 otherwise).
    property real launcherRight: 0
    // 0..1: how much of an island [x, x+width] the launcher panel covers.
    function coveredBy(x: real, width: real): real {
        return Math.max(0, Math.min(1, (launcherRight - x) / width));
    }
    property WallpaperSource wallpaper
    property bool wallpaperExposed: false
    required property NiriService niri
    required property string outputName
    readonly property alias workspaceStrip: leftIslands.strip
    readonly property alias leftIslands: leftIslands
    // What the left islands' glass refracts (Surfaces sets it; headless: flat stand-in).
    property DockBackdrop backdrop: null
    // Morph of the launcher panel (0..1): the workspaces island fades as it arrives.
    property real launcherExpansion: 0
    // Keep second-level wake/time-change detection, but publish only changed
    // minutes to the calendar and glass. SystemClock has no resume hook.
    readonly property double minuteTime: Math.floor(time.date.getTime() / 60000) * 60000
    property date dateTime: new Date(minuteTime)
    function refreshTimeZone(): void {
        Date.timeZoneUpdated();
        dateTimeChanged();
    }
    readonly property string clockTime: Qt.formatDateTime(dateTime, (SettingsBridge.values.find(row => row.key === "bar.clock_24_hour")?.value ?? true) ? "HH:mm" : "h:mm AP")
    // The island's short date and the panel's long one (clock.js shortDate/longDate). English
    // names from fixed lists: the island's width is measured on them (ClockCompactRow).
    readonly property var dayNames: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    readonly property var monthNames: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    readonly property string clockDate: dayNames[dateTime.getDay()].slice(0, 3) + " " + dateTime.getDate() + " " + monthNames[dateTime.getMonth()].slice(0, 3)
    readonly property string clockLongDate: dayNames[dateTime.getDay()] + ", " + dateTime.getDate() + " " + monthNames[dateTime.getMonth()]
    property bool clockPresent: false
    property int notificationCount: 0
    property bool dnd: false
    required property SystemService services
    property bool systemPresent: false
    signal systemClicked(string page)
    signal clockClicked
    signal launch
    readonly property alias logo: leftIslands.logo
    readonly property alias workspaces: leftIslands.workspaces
    // The clock island's own rectangle (input mask, status, tests); the island itself below.
    readonly property alias clock: clockIsland.hit
    readonly property alias clockIsland: clockIsland

    // Overview (mockup .screen.overview .i-logo/.i-clock/.i-sys): the islands slide up
    // 70 px and fade over BarPolicy.overviewShift; only the workspaces island stays unless
    // overview_workspaces is off. `visible` follows the fade so masks/blur drop with it.
    readonly property real shift: policy.overviewShift
    readonly property real lift: 70 * shift
    // The launcher button and the workspaces on liquid glass (liquid-glass/launcher.html).
    LeftIslands {
        id: leftIslands
        skipIntro: bar.skipIntro
        width: bar.width
        height: bar.height
        niri: bar.niri
        outputName: bar.outputName
        policy: bar.policy
        tip: bar.tip
        backdrop: bar.backdrop
        screenOrigin: Qt.point(bar.x, bar.y)
        launcherPresent: bar.launcherPresent
        launcherRight: bar.launcherRight
        launcherExpansion: bar.launcherExpansion
        shift: bar.shift
        workspaceShift: bar.policy.overviewWorkspaces ? 0 : bar.shift
        onLaunch: bar.launch()
    }
    SystemClock {
        id: time
        precision: SystemClock.Seconds
    }

    FileView {
        // Watch the directory: replacing the localtime symlink does not change
        // its old zoneinfo target. No file contents or periodic reads are needed.
        // "/etc/." because FileView also watches the path's parent, and the parent
        // of "/etc" is an empty path to Quickshell (a warning at every start).
        path: "/etc/."
        preload: false
        watchChanges: true
        printErrors: false
        onFileChanged: bar.refreshTimeZone()
    }

    // The clock island on liquid glass (liquid-glass/clock.html); ClockPanel grows out of it.
    ClockIsland {
        id: clockIsland
        width: bar.width
        height: bar.height
        time: bar.clockTime
        date: bar.clockDate
        dnd: bar.dnd
        notificationCount: bar.notificationCount
        tip: bar.tip
        backdrop: bar.backdrop
        screenOrigin: Qt.point(bar.x, bar.y)
        panelPresent: bar.clockPresent
        shift: bar.shift
        covered: bar.coveredBy(clockIsland.islandRect.x, clockIsland.islandRect.width)
        onClicked: bar.clockClicked()
    }
    readonly property alias dateLabel: clockIsland.dateLabel
    KeyboardTarget {
        parent: clockIsland.hit
        label: "Calendar and notifications"
        onActivated: bar.clockClicked()
    }
    // The system island on liquid glass (liquid-glass/system.html); SystemPanel grows out of it.
    // `systemIsland` is its rectangle (input mask, status, the privacy pill's anchor).
    readonly property alias systemIsland: systemGlass.hit
    readonly property alias systemGlass: systemGlass
    SystemIsland {
        id: systemGlass
        width: bar.width
        height: bar.height
        niri: bar.niri
        services: bar.services
        tip: bar.tip
        backdrop: bar.backdrop
        screenOrigin: Qt.point(bar.x, bar.y)
        panelPresent: bar.systemPresent
        shift: bar.shift
        onClicked: page => bar.systemClicked(page)
    }
    KeyboardTarget {
        id: privacyTarget
        anchors.fill: undefined
        ring: false
        visible: bar.privacyItem !== null && bar.privacyItem.visible
        // The pill and bar share the scene parent; keep layout changes reactive.
        x: bar.privacyItem ? bar.privacyItem.x - bar.x : 0
        y: bar.privacyItem ? bar.privacyItem.y - bar.y : 0
        width: bar.privacyItem ? bar.privacyItem.width : 0
        height: bar.privacyItem ? bar.privacyItem.height : 0
        label: "Privacy"
        onActivated: bar.privacyClicked()
    }
    GlassText {
        liquid: bar.wallpaper?.ready === true
        x: Metrics.logoX
        y: 49
        visible: !bar.launcherPresent && bar.policy.normalIslands && ["rejected", "unconfirmed", "error"].includes(bar.niri.actionState)
        text: "The last window action didn’t go through."
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        fallbackColor: ShellPalette.muted
        glassColor: LiquidPalette.secondary
    }
}
