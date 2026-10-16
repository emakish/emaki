// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import "AppearanceLogic.js" as Logic

QtObject {
    id: appearance
    readonly property string theme: Logic.setting(SettingsBridge.values, "appearance.theme", "light")
    // Unset: tokens.toml [defaults] accent (tests/test-theme-palette.py keeps the two equal).
    readonly property string accent: Logic.setting(SettingsBridge.values, "appearance.accent", "rose")
    readonly property string lightTime: Logic.setting(SettingsBridge.values, "appearance.light_time", "07:00")
    readonly property string darkTime: Logic.setting(SettingsBridge.values, "appearance.dark_time", "19:00")
    property date now: new Date()
    readonly property bool dark: Logic.isDark(theme, lightTime, darkTime, now)
    // The palette exposes the swatches; this object only consumes core settings.
    function refreshClock(): void {
        Date.timeZoneUpdated();
        now = new Date();
    }
    onThemeChanged: if (theme === "automatic")
        refreshClock()
    // SystemClock arms to the wall-clock minute. After a clock jump or resume,
    // the next tick evaluates fresh local time rather than adding elapsed ticks.
    readonly property SystemClock clock: SystemClock {
        precision: SystemClock.Minutes
        enabled: appearance.theme === "automatic"
        onDateChanged: appearance.refreshClock()
    }
    readonly property FileView zoneChanges: FileView {
        // Watch the directory so replacing the timezone symlink is observed.
        path: "/etc/."
        preload: false
        watchChanges: true
        printErrors: false
        onFileChanged: appearance.refreshClock()
    }
}
