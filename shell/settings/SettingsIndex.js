// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// Search explanations omit live values and use one coherent state per row.
// Search metadata is checked against visible page text by test-settings-index.mjs.
function sections() {
    return [
    {
        id: "wifi",
        title: qsTranslate("Shell", "Wi-Fi"),
        group: qsTranslate("Shell", "Network"),
        description: qsTranslate("Shell", "Join networks, see saved ones, forget them, show a password."),
        colors: ["accentSoft","accent"],
        items: [
            {"id":"wifi-radio","title":qsTranslate("Shell", "Wi-Fi"),"keywords":qsTranslate("Shell", "Wi-Fi wireless radio on off connection"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Network status is unavailable. The wireless radio is blocked by the system. On. Emaki joins saved networks by itself. Off. Turn Wi-Fi on to see nearby networks."),"searchExplanation":qsTranslate("Shell", "Emaki joins saved networks by itself.")},
            {"id":"wifi-networks","title":qsTranslate("Shell", "Other networks"),"keywords":qsTranslate("Shell", "Networks"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Nearby wireless networks appear here.")},
            {"id":"wifi-saved-networks","title":qsTranslate("Shell", "Saved networks"),"keywords":qsTranslate("Shell", "Saved networks"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Forget the network or show its saved password.")},
            {"id":"wifi-forget-a-network","title":qsTranslate("Shell", "Saved networks"),"keywords":qsTranslate("Shell", "Forget a network"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Forget the network or show its saved password.")},
            {"id":"wifi-show-password","title":qsTranslate("Shell", "Saved networks"),"keywords":qsTranslate("Shell", "Show password"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Forget the network or show its saved password.")},
            {"id":"wifi-restart-wi-fi","title":qsTranslate("Shell", "Restart Wi-Fi"),"keywords":qsTranslate("Shell", "Restart Wi-Fi"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "Turns Wi-Fi off and on again. Try it when networks stop showing up or a connection hangs.")},
            {"id":"wifi-hidden-network","title":qsTranslate("Shell", "Join a hidden network…"),"keywords":qsTranslate("Shell", "hidden ssid wireless"),"source":"SettingsWifiPage.qml","explanation":qsTranslate("Shell", "For a network that does not show its name."),"staticExplanation":true}
        ]
    },
    {
        id: "bluetooth",
        title: qsTranslate("Shell", "Bluetooth"),
        group: qsTranslate("Shell", "Network"),
        description: qsTranslate("Shell", "Pair headphones, mice and other devices and see their battery."),
        colors: ["sectionBlueStart","sectionBlueEnd"],
        items: [
            {"id":"bluetooth-radio","title":qsTranslate("Shell", "Bluetooth"),"keywords":qsTranslate("Shell", "Bluetooth wireless adapter on off"),"source":"SettingsBluetoothPage.qml","explanation":qsTranslate("Shell", "On. Paired devices can connect. Off. Turn Bluetooth on to connect devices."),"searchExplanation":qsTranslate("Shell", "Paired devices can connect.")},
            {"id":"bluetooth-pairing","title":qsTranslate("Shell", "Nearby devices"),"keywords":qsTranslate("Shell", "Pairing"),"source":"SettingsBluetoothPage.qml","explanation":qsTranslate("Shell", "Searching… Put a device in pairing mode, then press Search."),"searchExplanation":qsTranslate("Shell", "Put a device in pairing mode, then press Search.")},
            {"id":"bluetooth-devices","title":qsTranslate("Shell", "My devices"),"keywords":qsTranslate("Shell", "Devices"),"source":"SettingsBluetoothPage.qml","explanation":qsTranslate("Shell", "Pair a device to keep it in this list.")},
            {"id":"bluetooth-battery-level-of-headphones-and-mice","title":qsTranslate("Shell", "My devices"),"keywords":qsTranslate("Shell", "Battery level of headphones and mice"),"source":"SettingsBluetoothPage.qml","explanation":qsTranslate("Shell", "Pair a device to keep it in this list.")}
        ]
    },
    {
        id: "network",
        title: qsTranslate("Shell", "Network"),
        group: qsTranslate("Shell", "Network"),
        description: qsTranslate("Shell", "Wired connections, VPN and DNS."),
        colors: ["sectionGreenStart","sectionGreenEnd"],
        items: [
            {"id":"network-wired","title":qsTranslate("Shell", "Wired"),"keywords":qsTranslate("Shell", "Wired"),"source":"SettingsNetworkPage.qml","explanation":""},
            {"id":"network-vpn-import-from-a-file","title":qsTranslate("Shell", "Import VPN from a file"),"keywords":qsTranslate("Shell", "VPN — import from a file"),"source":"SettingsNetworkPage.qml","explanation":qsTranslate("Shell", "Add a saved connection using your provider’s configuration file."),"staticExplanation":true},
            {"id":"network-dns","title":qsTranslate("Shell", "DNS servers"),"keywords":"DNS","source":"SettingsNetworkPage.qml","explanation":qsTranslate("Shell", "Separate server addresses with commas. Empty uses automatic DNS."),"staticExplanation":true},
            {"id":"network-connection","title":qsTranslate("Shell", "Connection"),"keywords":qsTranslate("Shell", "saved profile DNS"),"source":"SettingsNetworkPage.qml","explanation":""},
            {"id":"network-vpn-type","title":qsTranslate("Shell", "VPN type"),"keywords":qsTranslate("Shell", "format plugin tunnel"),"source":"SettingsNetworkPage.qml","explanation":qsTranslate("Shell", "Available formats depend on the installed NetworkManager plugins."),"staticExplanation":true}
        ]
    },
    {
        id: "appearance",
        title: qsTranslate("Shell", "Appearance"),
        group: qsTranslate("Shell", "Personal"),
        description: qsTranslate("Shell", "Choose light or dark, automatic hours and an accent colour."),
        colors: ["sectionAmberStart","sectionAmberEnd"],
        items: [
            {id: "appearance-theme", title: qsTranslate("Shell", "Appearance"), keywords: qsTranslate("Shell", "theme light dark automatic"), key: "appearance.theme", source: "SettingsAppearancePage.qml", explanation: qsTranslate("Shell", "Choose light, dark or your own hours."), staticExplanation: true},
            {id: "appearance-accent", title: qsTranslate("Shell", "Accent colour"), keywords: qsTranslate("Shell", "orange coral rose amber green teal blue violet color"), key: "appearance.accent", source: "SettingsAppearancePage.qml", explanation: qsTranslate("Shell", "Colour for icons, highlights and controls."), staticExplanation: true},
            {id: "appearance-light-time", title: qsTranslate("Shell", "Light starts"), keywords: qsTranslate("Shell", "automatic schedule morning hours"), key: "appearance.light_time", source: "SettingsAppearancePage.qml", explanation: qsTranslate("Shell", "Use local time in 24-hour format (HH:MM)."), staticExplanation: true},
            {id: "appearance-dark-time", title: qsTranslate("Shell", "Dark starts"), keywords: qsTranslate("Shell", "automatic schedule evening hours"), key: "appearance.dark_time", source: "SettingsAppearancePage.qml", explanation: qsTranslate("Shell", "Use local time in 24-hour format (HH:MM)."), staticExplanation: true}
        ]
    },
    {
        id: "wallpaper",
        title: qsTranslate("Shell", "Wallpaper"),
        group: qsTranslate("Shell", "Personal"),
        description: qsTranslate("Shell", "Choose an Emaki wallpaper or your own picture."),
        colors: ["sectionRedStart","sectionRedEnd"],
        items: [
            {"id":"wallpaper-emaki-wallpapers","title":qsTranslate("Shell", "Emaki wallpapers"),"keywords":qsTranslate("Shell", "Emaki wallpapers background picture"),"source":"SettingsWallpaperPage.qml","explanation":""},
            {"id":"wallpaper-your-own-picture","title":qsTranslate("Shell", "Your own picture"),"keywords":qsTranslate("Shell", "Your own picture image file"),"key":"appearance.wallpaper","source":"SettingsWallpaperPage.qml","explanation":qsTranslate("Shell", "Choose a picture for every display. Reset restores your inherited wallpaper."),"staticExplanation":true}
        ]
    },
    {
        id: "panel",
        title: qsTranslate("Shell", "Panel & Dock"),
        group: qsTranslate("Shell", "Personal"),
        description: qsTranslate("Shell", "Choose when the panel and dock appear."),
        colors: ["sectionPinkStart","sectionPinkEnd"],
        items: [
            {"id":"panel-panel-auto-hide","title":qsTranslate("Shell", "Hide the panel automatically"),"keywords":qsTranslate("Shell", "Panel auto-hide bar hide"),"key":"bar.autohide_mode","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Choose when the panel hides. Move to the top edge to show it."),"searchExplanation":qsTranslate("Shell", "Move to the top edge to show it.")},
            {"id":"panel-workspaces-in-overview","title":qsTranslate("Shell", "Show workspaces in the overview"),"keywords":qsTranslate("Shell", "Workspaces in overview panel overview"),"key":"bar.overview_workspaces","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Workspace numbers stay visible in the overview. Workspace numbers hide in the overview."),"searchExplanation":qsTranslate("Shell", "Workspace numbers stay visible in the overview.")},
            {"id":"panel-show-dock","title":qsTranslate("Shell", "Show the dock"),"keywords":qsTranslate("Shell", "Show dock dock enabled"),"key":"dock.on","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Your pinned and running apps appear in the dock. The dock is hidden."),"searchExplanation":qsTranslate("Shell", "Your pinned and running apps appear in the dock.")},
            {"id":"panel-dock-auto-hide","title":qsTranslate("Shell", "Hide the dock automatically"),"keywords":qsTranslate("Shell", "Dock auto-hide hide dock"),"key":"dock.auto_hide","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Move to the bottom edge to show your apps. The dock stays visible beside your windows."),"searchExplanation":qsTranslate("Shell", "Move to the bottom edge to show your apps.")}
        ]
    },
    {
        id: "windows",
        title: qsTranslate("Shell", "Windows & Workspaces"),
        group: qsTranslate("Shell", "Personal"),
        description: qsTranslate("Shell", "Choose window spacing, initial width and focus behavior."),
        colors: ["accentSoft","accent"],
        items: [
            {"id":"windows-window-gaps","title":qsTranslate("Shell", "Gaps"),"keywords":qsTranslate("Shell", "Window gaps spacing between windows"),"key":"appearance.gaps","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "px of wallpaper between windows."),"searchExplanation":qsTranslate("Shell", "Wallpaper between windows, in pixels.")},
            {"id":"windows-new-window-width","title":qsTranslate("Shell", "Width of a new window"),"keywords":qsTranslate("Shell", "New window width default column size"),"key":"windows.default_column_width","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Choose how much of the screen a new column uses."),"staticExplanation":true},
            {"id":"windows-focus-follows-mouse","title":qsTranslate("Shell", "Focus follows the mouse"),"keywords":qsTranslate("Shell", "Focus follows mouse pointer hover"),"key":"windows.focus_follows_mouse","source":"SettingsCorePage.qml","explanation":qsTranslate("Shell", "Use your existing focus behavior, or focus windows under the pointer."),"staticExplanation":true}
        ]
    },
    {
        id: "displays",
        title: qsTranslate("Shell", "Displays"),
        group: qsTranslate("Shell", "Devices"),
        description: qsTranslate("Shell", "Arrange your screens and set their resolution, scale and refresh rate."),
        colors: ["sectionBlueStart","sectionBlueEnd"],
        items: [
            {"id":"displays-brightness","title":qsTranslate("Shell", "Brightness"),"keywords":qsTranslate("Shell", "Brightness screen backlight dim"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "ready % of the built-in backlight. No adjustable backlight is available."),"searchExplanation":qsTranslate("Shell", "Built-in backlight level.")},
            {"id":"displays-resolution","title":qsTranslate("Shell", "Resolution"),"keywords":qsTranslate("Shell", "Resolution screen pixels"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Choose how many pixels the screen shows."),"staticExplanation":true},
            {"id":"displays-scale","title":qsTranslate("Shell", "Scale"),"keywords":qsTranslate("Shell", "Scale text size zoom"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Make text and controls larger or smaller."),"staticExplanation":true},
            {"id":"displays-refresh-rate","title":qsTranslate("Shell", "Refresh rate"),"keywords":qsTranslate("Shell", "Refresh rate hz frequency"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Choose how often the screen updates each second."),"staticExplanation":true},
            {"id":"displays-arrangement","title":qsTranslate("Shell", "Arrangement"),"keywords":qsTranslate("Shell", "Arrangement monitor position"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Drag the screens to match your desk. The top bar marks the main screen. Use arrow keys to move a focused screen.")},
            {"id":"displays-main-display","title":qsTranslate("Shell", "Main screen"),"keywords":qsTranslate("Shell", "Main display primary monitor"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Choose the display focused now and at sign-in."),"staticExplanation":true},
            {"id":"displays-rotation","title":qsTranslate("Shell", "Rotation"),"keywords":qsTranslate("Shell", "Rotation orientation"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Match the orientation of your screen."),"staticExplanation":true},
            {"id":"displays-night-light","title":qsTranslate("Shell", "Night Light"),"keywords":qsTranslate("Shell", "Night Light warm colors blue light"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Use warmer display colours for evening light."),"staticExplanation":true},
            {"id":"displays-night-light-schedule","title":qsTranslate("Shell", "Schedule"),"keywords":qsTranslate("Shell", "Night Light schedule sunset sunrise time"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Keep Night Light on or use your own hours."),"staticExplanation":true},
            {"id":"displays-night-light-temperature","title":qsTranslate("Shell", "Warmth"),"keywords":qsTranslate("Shell", "Night Light temperature warmth kelvin"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "K. Choose how warm Night Light makes the display."),"searchExplanation":qsTranslate("Shell", "Choose how warm Night Light makes the display.")},
            {"id":"displays-enabled","title":qsTranslate("Shell", "Use this display"),"keywords":qsTranslate("Shell", "screen on off enabled"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Turn this screen on or off for this session. At least one screen must stay on."),"staticExplanation":true},
            {"id":"displays-night-light-start","title":qsTranslate("Shell", "Start time"),"keywords":qsTranslate("Shell", "night light custom hours evening"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Use local time in 24-hour format (HH:MM).")},
            {"id":"displays-night-light-end","title":qsTranslate("Shell", "End time"),"keywords":qsTranslate("Shell", "night light custom hours morning"),"source":"DisplaysSettingsPage.qml","explanation":qsTranslate("Shell", "Use local time in 24-hour format (HH:MM).")}
        ]
    },
    {
        id: "sound",
        title: qsTranslate("Shell", "Sound"),
        group: qsTranslate("Shell", "Devices"),
        description: qsTranslate("Shell", "Speakers, headphones and microphones, volume per app, system sounds."),
        colors: ["sectionPinkStart","sectionPinkEnd"],
        items: [
            {"id":"sound-mute","title":qsTranslate("Shell", "Mute output"),"keywords":qsTranslate("Shell", "Mute silence audio output"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "Silence your speakers or headphones."),"staticExplanation":true},
            {"id":"sound-volume","title":qsTranslate("Shell", "Volume"),"keywords":qsTranslate("Shell", "Volume loudness audio speakers headphones"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "% of the output level. Reset restores 100%."),"searchExplanation":qsTranslate("Shell", "Output level. Reset restores 100%.")},
            {"id":"sound-output","title":qsTranslate("Shell", "Output device"),"keywords":qsTranslate("Shell", "Output"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "Sound plays through  ."),"searchExplanation":qsTranslate("Shell", "The device used to play sound.")},
            {"id":"sound-input","title":qsTranslate("Shell", "Input device"),"keywords":qsTranslate("Shell", "Input microphone device recording"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "Record through  ."),"searchExplanation":qsTranslate("Shell", "The device used to record sound.")},
            {"id":"sound-input-volume","title":qsTranslate("Shell", "Input volume"),"keywords":qsTranslate("Shell", "Input volume microphone recording gain"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "% of the input level. Reset restores 100%."),"searchExplanation":qsTranslate("Shell", "Input level. Reset restores 100%.")},
            {"id":"sound-input-mute","title":qsTranslate("Shell", "Mute microphone"),"keywords":qsTranslate("Shell", "Mute microphone silence input recording"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "Stop sound from the selected microphone."),"staticExplanation":true},
            {"id":"sound-input-level","title":qsTranslate("Shell", "Input level"),"keywords":qsTranslate("Shell", "Input level microphone test meter"),"source":"SoundSettingsPage.qml","explanation":qsTranslate("Shell", "Speak to test your microphone while this page is open."),"staticExplanation":true},
            {"id":"sound-volume-per-app","title":qsTranslate("Shell", "Volume per app"),"keywords":qsTranslate("Shell", "Volume per app"),"source":"SoundSettingsPage.qml","explanation":""}
        ]
    },
    {
        id: "keyboard",
        title: qsTranslate("Shell", "Keyboard"),
        group: qsTranslate("Shell", "Devices"),
        description: qsTranslate("Shell", "Layouts and how to switch them, key repeat, and every shortcut."),
        colors: ["sectionBrownStart","sectionBrownEnd"],
        items: [
            {"id":"keyboard-keyboard-layouts","title":qsTranslate("Shell", "Layouts"),"keywords":qsTranslate("Shell", "Keyboard layouts language input"),"key":"keyboard.layouts","source":"KeyboardSettingsPage.qml","explanation":""},
            {"id":"keyboard-switch-layout-shortcut","title":qsTranslate("Shell", "Switch layouts with"),"keywords":qsTranslate("Shell", "Switch layout shortcut language key"),"key":"keyboard.switch_key","source":"KeyboardSettingsPage.qml","explanation":qsTranslate("Shell", "Go to the next language in your layout list."),"staticExplanation":true},
            {"id":"keyboard-key-repeat-delay","title":qsTranslate("Shell", "Delay before repeat"),"keywords":qsTranslate("Shell", "Key repeat delay hold keyboard"),"key":"keyboard.repeat_delay","source":"KeyboardSettingsPage.qml","explanation":qsTranslate("Shell", "ms before a held key starts repeating."),"searchExplanation":qsTranslate("Shell", "Time before a held key starts repeating.")},
            {"id":"keyboard-key-repeat-rate","title":qsTranslate("Shell", "Repeat speed"),"keywords":qsTranslate("Shell", "Key repeat rate speed characters"),"key":"keyboard.repeat_rate","source":"KeyboardSettingsPage.qml","explanation":qsTranslate("Shell", "characters per second while a key is held."),"searchExplanation":qsTranslate("Shell", "Characters per second while a key is held.")},
            {"id":"keyboard-keyboard-shortcuts","title":qsTranslate("Shell", "Shortcuts"),"keywords":qsTranslate("Shell", "Keyboard shortcuts bindings hotkeys"),"source":"KeyboardSettingsPage.qml","explanation":""},
            {"id":"keyboard-toggle-floating-window-shortcut","title":qsTranslate("Shell", "Toggle floating window"),"keywords":qsTranslate("Shell", "Toggle floating window shortcut float window keybinding"),"key":"keybindings.toggle_window_floating","source":"KeyboardSettingsPage.qml","explanation":qsTranslate("Shell", "keybindings.toggle_window_floating Switch the focused window between floating and tiled."),"searchExplanation":qsTranslate("Shell", "Switch the focused window between floating and tiled.")}
        ]
    },
    {
        id: "mouse",
        title: qsTranslate("Shell", "Mouse & Trackpad"),
        group: qsTranslate("Shell", "Devices"),
        description: qsTranslate("Shell", "Pointer speed, scrolling direction, tap to click and gestures."),
        colors: ["sectionBrownStart","sectionBrownEnd"],
        items: [
            {"id":"mouse-pointer-speed","title":qsTranslate("Shell", "Pointer speed"),"keywords":qsTranslate("Shell", "Mouse pointer speed acceleration"),"key":"mouse.speed","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Pointer speed:  % of the adjustment range."),"searchExplanation":qsTranslate("Shell", "Pointer speed as a percentage of the adjustment range.")},
            {"id":"mouse-natural-scrolling","title":qsTranslate("Shell", "Natural scrolling"),"keywords":qsTranslate("Shell", "Mouse natural scrolling direction"),"key":"mouse.natural_scroll","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Content moves in the direction you scroll. Content moves against the direction you scroll."),"searchExplanation":qsTranslate("Shell", "Content moves in the direction you scroll.")},
            {"id":"mouse-trackpad-speed","title":qsTranslate("Shell", "Pointer speed"),"keywords":qsTranslate("Shell", "Trackpad pointer speed acceleration"),"key":"touchpad.speed","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Pointer speed:  % of the adjustment range."),"searchExplanation":qsTranslate("Shell", "Pointer speed as a percentage of the adjustment range.")},
            {"id":"mouse-trackpad-scrolling","title":qsTranslate("Shell", "Natural scrolling"),"keywords":qsTranslate("Shell", "Trackpad natural scrolling direction fingers"),"key":"touchpad.natural_scroll","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Content follows your fingers. Content moves against your fingers."),"searchExplanation":qsTranslate("Shell", "Content follows your fingers.")},
            {"id":"mouse-tap-to-click","title":qsTranslate("Shell", "Tap to click"),"keywords":qsTranslate("Shell", "Tap to click trackpad"),"key":"touchpad.tap","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Tap the trackpad to click. Press the trackpad to click."),"searchExplanation":qsTranslate("Shell", "Tap the trackpad to click.")},
            {"id":"mouse-two-finger-right-click","title":qsTranslate("Shell", "Two-finger right click"),"keywords":qsTranslate("Shell", "Two-finger right click trackpad"),"key":"touchpad.two_finger_right_click","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Click with two fingers to right-click.")},
            {"id":"mouse-disable-while-typing","title":qsTranslate("Shell", "Disable while typing"),"keywords":qsTranslate("Shell", "Disable while typing trackpad keyboard"),"key":"touchpad.disable_while_typing","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "The trackpad pauses while you type. The trackpad stays active while you type."),"searchExplanation":qsTranslate("Shell", "The trackpad pauses while you type.")},
            {"id":"mouse-gestures","title":qsTranslate("Shell", "Scroll while dragging"),"keywords":qsTranslate("Shell", "Scroll while dragging gestures edges"),"key":"gestures.dnd_edge_view_scroll","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "Drag to the left or right edge to scroll through windows. Dragging to the left or right edge does not scroll."),"searchExplanation":qsTranslate("Shell", "Drag to the left or right edge to scroll through windows.")},
            {"id":"mouse-workspace-gestures","title":qsTranslate("Shell", "Switch workspaces while dragging in the overview"),"keywords":qsTranslate("Shell", "Switch workspaces while dragging in the overview gestures edges"),"key":"gestures.dnd_edge_workspace_switch","source":"MouseSettingsPage.qml","explanation":qsTranslate("Shell", "In the overview, drag to the top or bottom edge to switch workspaces. In the overview, dragging to the top or bottom edge keeps this workspace."),"searchExplanation":qsTranslate("Shell", "In the overview, drag to the top or bottom edge to switch workspaces.")}
        ]
    },
    {
        id: "notifications",
        title: qsTranslate("Shell", "Notifications"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Which apps may notify you, and quiet hours with Do Not Disturb."),
        colors: ["sectionRedStart","sectionRedEnd"],
        items: [
            {"id":"notifications-apps-that-may-notify","title":qsTranslate("Shell", "Applications"),"keywords":qsTranslate("Shell", "Apps that may notify allow silent off rules"),"key":"notifications.rules","source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "Allow popups, keep only in history, or block notifications."),"staticExplanation":true},
            {"id":"notifications-do-not-disturb","title":qsTranslate("Shell", "Do not disturb now"),"keywords":qsTranslate("Shell", "Do Not Disturb quiet now"),"key":"notifications.dnd","source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "Keep notifications in history without showing popups."),"staticExplanation":true},
            {"id":"notifications-until","title":qsTranslate("Shell", "Pause notifications until"),"keywords":qsTranslate("Shell", "Quiet until do not disturb timer"),"key":"notifications.until","source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "Paused until  . Pause popups for a limited time."),"searchExplanation":qsTranslate("Shell", "Pause popups for a limited time.")},
            {"id":"notifications-do-not-disturb-on-a-schedule","title":qsTranslate("Shell", "Use a daily schedule"),"keywords":qsTranslate("Shell", "Do Not Disturb on a schedule quiet hours daily"),"key":"notifications.schedule","source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "Pause popups between these local times; matching times pause all day."),"staticExplanation":true},
            {"id":"notifications-start","title":qsTranslate("Shell", "Start time"),"keywords":qsTranslate("Shell", "quiet hours daily do not disturb"),"source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "Begin the daily pause at this time (HH:MM)."),"staticExplanation":true},
            {"id":"notifications-end","title":qsTranslate("Shell", "End time"),"keywords":qsTranslate("Shell", "quiet hours daily do not disturb"),"source":"NotificationsSettingsPage.qml","explanation":qsTranslate("Shell", "End the daily pause at this time (HH:MM)."),"staticExplanation":true}
        ]
    },
    {
        id: "battery",
        title: qsTranslate("Shell", "Battery & Power"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Sleep, the lid, power modes, a charge limit and battery health."),
        colors: ["sectionLimeStart","sectionLimeEnd"],
        items: [
            {"id":"battery-blank-on-battery","title":qsTranslate("Shell", "Blank the screen on battery"),"keywords":qsTranslate("Shell", "Blank the screen on battery inactivity screen off"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Turn off the screen after this much inactivity on battery power."),"staticExplanation":true},
            {"id":"battery-blank-on-power","title":qsTranslate("Shell", "Blank the screen when plugged in"),"keywords":qsTranslate("Shell", "Blank the screen when plugged in inactivity screen off power"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Turn off the screen after this much inactivity on external power."),"staticExplanation":true},
            {"id":"battery-power-mode","title":qsTranslate("Shell", "Power mode"),"keywords":qsTranslate("Shell", "Power mode"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Balance energy use and performance."),"staticExplanation":true},
            {"id":"battery-charge-limit","title":qsTranslate("Shell", "Charge limit"),"keywords":qsTranslate("Shell", "Charge limit"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Stop charging at this percentage. The limit is restored at startup."),"staticExplanation":true},
            {"id":"battery-battery-health","title":qsTranslate("Shell", "Battery health"),"keywords":qsTranslate("Shell", "Battery health"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Battery health is not reported by this hardware. % of the original full-charge capacity."),"searchExplanation":qsTranslate("Shell", "Percentage of the original full-charge capacity.")}
        ]
    },
    {
        id: "lock",
        title: qsTranslate("Shell", "Lock & Login"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Change your password and choose when to lock."),
        colors: ["sectionBrownStart","sectionBrownEnd"],
        items: [
            {"id":"lock-password","title":qsTranslate("Shell", "Change password"),"keywords":qsTranslate("Shell", "Change password login wallet terminal"),"source":"SettingsMaintenancePage.qml","explanation":qsTranslate("Shell", "Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately."),"staticExplanation":true},
            {"id":"lock-when-to-lock","title":qsTranslate("Shell", "Lock after inactivity"),"keywords":qsTranslate("Shell", "When to lock"),"source":"SettingsPowerPage.qml","explanation":qsTranslate("Shell", "Require your password after this much time without input."),"staticExplanation":true}
        ]
    },
    {
        id: "apps",
        title: qsTranslate("Shell", "Apps"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Default apps, apps that start with the session, and installed programs."),
        colors: ["sectionAmberStart","sectionAmberEnd"],
        items: [
            {"id":"apps-default-terminal","title":qsTranslate("Shell", "Terminal"),"keywords":qsTranslate("Shell", "Default terminal console application"),"key":"defaults.terminal","source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Open a terminal with this app."),"staticExplanation":true},
            {"id":"apps-default-browser","title":qsTranslate("Shell", "Web browser"),"keywords":qsTranslate("Shell", "Default browser web links"),"key":"defaults.browser","source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Open web links with this app."),"staticExplanation":true},
            {"id":"apps-default-file-manager","title":qsTranslate("Shell", "Files"),"keywords":qsTranslate("Shell", "Default file manager folders files"),"key":"defaults.files","source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Open folders with this app."),"staticExplanation":true},
            {"id":"apps-default-mail","title":qsTranslate("Shell", "Mail"),"keywords":qsTranslate("Shell", "Default mail app email messages"),"key":"defaults.mail","source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Open email links with this app."),"staticExplanation":true},
            {"id":"apps-default-editor","title":qsTranslate("Shell", "Text editor"),"keywords":qsTranslate("Shell", "Default text editor text documents"),"key":"defaults.editor","source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Open plain text files with this app."),"staticExplanation":true},
            {"id":"apps-startup-apps","title":qsTranslate("Shell", "Startup apps"),"keywords":qsTranslate("Shell", "Startup apps autostart login session"),"source":"SettingsAppsPage.qml","explanation":qsTranslate("Shell", "Start this app when you sign in.")}
        ]
    },
    {
        id: "updates",
        title: qsTranslate("Shell", "Updates & Recovery"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Choose an update channel and review snapshot recovery."),
        colors: ["accentSoft","accent"],
        items: [
            {"id":"updates-update-channel","title":qsTranslate("Shell", "Channel"),"keywords":qsTranslate("Shell", "Update channel"),"source":"SettingsMaintenancePage.qml","explanation":qsTranslate("Shell", "Choose which releases your next update uses."),"staticExplanation":true},
            {"id":"updates-reset-emaki-settings","title":qsTranslate("Shell", "Reset Emaki settings"),"keywords":qsTranslate("Shell", "Reset Emaki settings defaults restore undo"),"source":"SettingsMaintenancePage.qml","explanation":qsTranslate("Shell", "Return Emaki's own settings to their defaults. Your files and app settings are not touched."),"searchExplanation":qsTranslate("Shell", "Your files and app settings are not touched.")}
        ]
    },
    {
        id: "privacy",
        title: qsTranslate("Shell", "Privacy & Security"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Review recovery, encryption, sudo keys and remembered portal permissions."),
        colors: ["sectionBlueStart","sectionBlueEnd"],
        items: [
            {"id":"privacy-recovery","title":qsTranslate("Shell", "Snapshots and rollback"),"keywords":qsTranslate("Shell", "snapshot rollback recovery boot menu"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "Snapshots and rollback")},
            {"id":"privacy-encryption","title":qsTranslate("Shell", "Disk encryption"),"keywords":qsTranslate("Shell", "disk encryption home storage"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "System and home storage")},
            {"id":"privacy-sudo-key","title":qsTranslate("Shell", "Sudo key"),"keywords":qsTranslate("Shell", "sudo key installed active revoke"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "Select a key to revoke it.")},
            {"id":"privacy-portals","title":qsTranslate("Shell", "Portal permissions"),"keywords":qsTranslate("Shell", "apps screen capture camera microphone permissions revoke"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "These are remembered portal grants, not a complete list of application access or active screen captures. Revoking a grant does not stop active sessions.")},
            {"id":"privacy-lock","title":qsTranslate("Shell", "Lock & Login"),"keywords":qsTranslate("Shell", "lock delay inactivity"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "Lock screen delay")},
            {"id":"privacy-reports","title":qsTranslate("Shell", "Crash and report data"),"keywords":qsTranslate("Shell", "crash reports logs diagnostics"),"source":"SettingsPrivacyPage.qml","explanation":qsTranslate("Shell", "Saved reports")}
        ]
    },
    {
        id: "region",
        title: qsTranslate("Shell", "Region & Time"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Language, formats, time zone and the 24-hour clock."),
        colors: ["sectionGreenStart","sectionGreenEnd"],
        items: [
            {"id":"region-language","title":qsTranslate("Shell", "Language"),"keywords":qsTranslate("Shell", "Language installed sign out apps"),"source":"SettingsRegionPage.qml","explanation":qsTranslate("Shell", "Choose an installed language. Sign out to use it in all apps."),"staticExplanation":true},
            {"id":"region-formats","title":qsTranslate("Shell", "Formats"),"keywords":qsTranslate("Shell", "Formats installed locale dates numbers"),"source":"SettingsRegionPage.qml","explanation":qsTranslate("Shell", "Choose an installed locale for dates and numbers."),"staticExplanation":true},
            {"id":"region-time-zone","title":qsTranslate("Shell", "Time zone"),"keywords":qsTranslate("Shell", "Time zone installed computer"),"source":"SettingsRegionPage.qml","explanation":qsTranslate("Shell", "Choose a time zone installed on this computer."),"staticExplanation":true},
            {"id":"region-automatic-time","title":qsTranslate("Shell", "Automatic time"),"keywords":qsTranslate("Shell", "Automatic time clock synchronization"),"source":"SettingsRegionPage.qml","explanation":qsTranslate("Shell", "Keep the clock in sync with an internet time service."),"staticExplanation":true}
        ]
    },
    {
        id: "users",
        title: qsTranslate("Shell", "Users"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Your account, picture and password, and other people on this computer."),
        colors: ["sectionPinkStart","sectionPinkEnd"],
        items: [
            {"id":"users-your-account","title":qsTranslate("Shell", "Your account"),"keywords":qsTranslate("Shell", "Your account name administrator user"),"source":"SettingsUsersPage.qml","explanation":""},
            {"id":"users-password","title":qsTranslate("Shell", "Password"),"keywords":qsTranslate("Shell", "Password change account"),"source":"SettingsUsersPage.qml","explanation":qsTranslate("Shell", "Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately."),"staticExplanation":true},
            {"id":"users-other-users","title":qsTranslate("Shell", "Other users"),"keywords":qsTranslate("Shell", "Other users accounts people administrator remove"),"source":"SettingsUsersPage.qml","explanation":""},
            {"id":"users-add-user","title":qsTranslate("Shell", "Add user"),"keywords":qsTranslate("Shell", "Add user new account person"),"source":"SettingsUsersPage.qml","explanation":qsTranslate("Shell", "Create an account with its own files and settings."),"staticExplanation":true}
        ]
    },
    {
        id: "storage",
        title: qsTranslate("Shell", "Storage"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Inspect disk use and preview safe cleanup."),
        colors: ["sectionBrownStart","sectionBrownEnd"],
        items: [
            {"id": "storage-disks", "title": qsTranslate("Shell", "System and home"), "keywords": qsTranslate("Shell", "disk usage filesystem btrfs"), "source": "SettingsStoragePage.qml", "explanation": ""},
            {"id": "storage-snapshots", "title": qsTranslate("Shell", "Snapshots"), "keywords": qsTranslate("Shell", "snapshot disk usage"), "source": "SettingsStoragePage.qml", "explanation": ""},
            {"id": "storage-cache", "title": qsTranslate("Shell", "Package cache"), "keywords": qsTranslate("Shell", "package cleanup cache"), "source": "SettingsStoragePage.qml", "explanation": ""},
            {"id": "storage-logs", "title": qsTranslate("Shell", "Logs"), "keywords": qsTranslate("Shell", "old logs cleanup"), "source": "SettingsStoragePage.qml", "explanation": ""},
            {"id": "storage-trash", "title": qsTranslate("Shell", "Trash"), "keywords": qsTranslate("Shell", "trash cleanup"), "source": "SettingsStoragePage.qml", "explanation": ""}
        ]
    },
    {
        id: "about",
        title: qsTranslate("Shell", "About"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "This Emaki version and this computer’s hardware."),
        colors: ["accentSoft","accent"],
        items: [
            {"id":"about-emaki-version","title":qsTranslate("Shell", "Version"),"keywords":qsTranslate("Shell", "Emaki version release version"),"source":"SettingsAboutPage.qml","explanation":""},
            {"id":"about-hardware","title":qsTranslate("Shell", "This computer"),"keywords":qsTranslate("Shell", "Hardware processor memory computer"),"source":"SettingsAboutPage.qml","explanation":""},
            {"id":"about-report-a-problem","title":qsTranslate("Shell", "Report a problem"),"keywords":qsTranslate("Shell", "Report a problem bug issue GitHub"),"source":"SettingsAboutPage.qml","explanation":qsTranslate("Shell", "Describe the problem and review exactly what will be sent. Nothing leaves this computer until you choose Send, which opens the issue form on GitHub."),"staticExplanation":true},
            {"id":"about-diagnostics-file","title":qsTranslate("Shell", "Diagnostics file"),"keywords":qsTranslate("Shell", "Diagnostics logs report file"),"source":"SettingsAboutPage.qml","explanation":qsTranslate("Shell", "Collect logs and a short system summary into a file you can review. Add to a report only what you choose to share."),"staticExplanation":true}
        ]
    },
    {
        id: "assistants",
        title: qsTranslate("Shell", "AI Assistants"),
        group: qsTranslate("Shell", "System"),
        description: qsTranslate("Shell", "Protection and permissions for installed assistants."),
        colors: ["sectionBlueStart","sectionBlueEnd"],
        items: [

        ]
    }
    ];
}

function section(id) {
    var entries = sections();
    for (var i = 0; i < entries.length; ++i)
        if (entries[i].id === id) return entries[i];
    return null;
}

function normalized(value) {
    return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function search(query, availablePages, availableTargets) {
    var entries = sections();
    var words = normalized(query).split(" ");
    if (!words[0]) return [];
    var available = Array.isArray(availablePages) ? availablePages : [];
    var targets = Array.isArray(availableTargets) ? availableTargets : null;
    var results = [];
    function matches(text) {
        var haystack = normalized(text);
        return words.every(word => haystack.indexOf(word) !== -1);
    }
    for (var i = 0; i < entries.length; ++i) {
        var page = entries[i];
        if (available.indexOf(page.id) === -1) continue;
        if (matches(page.title)) results.push({id: page.id, title: page.title,
            page: page.id, pageTitle: page.title, key: "", explanation: page.description,
            section: true, enabled: true});
        for (var j = 0; j < page.items.length; ++j) {
            var item = page.items[j];
            if (targets && targets.indexOf(item.key || item.id) === -1) continue;
            if (!matches(item.title + " " + item.explanation + " "
                    + item.keywords + " " + (item.key || ""))) continue;
            if (results.some(result => !result.section && result.page === page.id
                    && result.title === item.title && result.key === (item.key || ""))) continue;
            results.push({id: item.id, title: item.title,
                page: page.id, pageTitle: page.title, key: item.key || "",
                explanation: item.searchExplanation !== undefined ? item.searchExplanation : item.explanation,
                section: false, enabled: true});
        }
    }
    return results;
}
