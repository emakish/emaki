// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// Search explanations omit live values and use one coherent state per row.
// Search metadata is checked against visible page text by test-settings-index.mjs.
var sections = [
    {
        id: "wifi",
        title: "Wi-Fi",
        group: "Network",
        description: "Join networks, see saved ones, forget them, show a password.",
        colors: ["#f39a5f","#e2733f"],
        items: [
            {"id":"wifi-radio","title":"Wi-Fi","keywords":"Wi-Fi wireless radio on off connection","source":"SettingsWifiPage.qml","explanation":"Network status is unavailable. The wireless radio is blocked by the system. On. Emaki joins saved networks by itself. Off. Turn Wi-Fi on to see nearby networks.","searchExplanation":"Emaki joins saved networks by itself."},
            {"id":"wifi-networks","title":"Other networks","keywords":"Networks","source":"SettingsWifiPage.qml","explanation":"Nearby wireless networks appear here."},
            {"id":"wifi-saved-networks","title":"Saved networks","keywords":"Saved networks","source":"SettingsWifiPage.qml","explanation":"Forget the network or show its saved password."},
            {"id":"wifi-forget-a-network","title":"Saved networks","keywords":"Forget a network","source":"SettingsWifiPage.qml","explanation":"Forget the network or show its saved password."},
            {"id":"wifi-show-password","title":"Saved networks","keywords":"Show password","source":"SettingsWifiPage.qml","explanation":"Forget the network or show its saved password."},
            {"id":"wifi-restart-wi-fi","title":"Restart Wi-Fi","keywords":"Restart Wi-Fi","source":"SettingsWifiPage.qml","explanation":"Turns Wi-Fi off and on again. Try it when networks stop showing up or a connection hangs."},
            {"id":"wifi-hidden-network","title":"Join a hidden network…","keywords":"hidden ssid wireless","source":"SettingsWifiPage.qml","explanation":"For a network that does not show its name.","staticExplanation":true}
        ]
    },
    {
        id: "bluetooth",
        title: "Bluetooth",
        group: "Network",
        description: "Pair headphones, mice and other devices and see their battery.",
        colors: ["#9db6d0","#6f8cab"],
        items: [
            {"id":"bluetooth-radio","title":"Bluetooth","keywords":"Bluetooth wireless adapter on off","source":"SettingsBluetoothPage.qml","explanation":"On. Paired devices can connect. Off. Turn Bluetooth on to connect devices.","searchExplanation":"Paired devices can connect."},
            {"id":"bluetooth-pairing","title":"Nearby devices","keywords":"Pairing","source":"SettingsBluetoothPage.qml","explanation":"Searching… Put a device in pairing mode, then press Search.","searchExplanation":"Put a device in pairing mode, then press Search."},
            {"id":"bluetooth-devices","title":"My devices","keywords":"Devices","source":"SettingsBluetoothPage.qml","explanation":"Pair a device to keep it in this list."},
            {"id":"bluetooth-battery-level-of-headphones-and-mice","title":"My devices","keywords":"Battery level of headphones and mice","source":"SettingsBluetoothPage.qml","explanation":"Pair a device to keep it in this list."}
        ]
    },
    {
        id: "network",
        title: "Network",
        group: "Network",
        description: "Wired connections, VPN and DNS.",
        colors: ["#a8cfc0","#78a796"],
        items: [
            {"id":"network-wired","title":"Wired","keywords":"Wired","source":"SettingsNetworkPage.qml","explanation":""},
            {"id":"network-vpn-import-from-a-file","title":"Import VPN from a file","keywords":"VPN — import from a file","source":"SettingsNetworkPage.qml","explanation":"Add a saved connection using your provider’s configuration file.","staticExplanation":true},
            {"id":"network-dns","title":"DNS servers","keywords":"DNS","source":"SettingsNetworkPage.qml","explanation":"Separate server addresses with commas. Empty uses automatic DNS.","staticExplanation":true},
            {"id":"network-connection","title":"Connection","keywords":"saved profile DNS","source":"SettingsNetworkPage.qml","explanation":""},
            {"id":"network-vpn-type","title":"VPN type","keywords":"format plugin tunnel","source":"SettingsNetworkPage.qml","explanation":"Available formats depend on the installed NetworkManager plugins.","staticExplanation":true}
        ]
    },
    {
        id: "appearance",
        title: "Appearance",
        group: "Personal",
        description: "Light or dark, the accent colour, the shell style, fonts and the cursor.",
        colors: ["#f2cc7e","#e3a243"],
        items: [

        ]
    },
    {
        id: "wallpaper",
        title: "Wallpaper",
        group: "Personal",
        description: "Choose an Emaki wallpaper or your own picture.",
        colors: ["#f07d6e","#d9534a"],
        items: [
            {"id":"wallpaper-emaki-wallpapers","title":"Emaki wallpapers","keywords":"Emaki wallpapers background picture","source":"SettingsWallpaperPage.qml","explanation":""},
            {"id":"wallpaper-your-own-picture","title":"Your own picture","keywords":"Your own picture image file","key":"appearance.wallpaper","source":"SettingsWallpaperPage.qml","explanation":"Choose a picture for every display. Reset restores your inherited wallpaper.","staticExplanation":true}
        ]
    },
    {
        id: "panel",
        title: "Panel & Dock",
        group: "Personal",
        description: "Choose when the panel and dock appear.",
        colors: ["#e3a0b6","#c96f8f"],
        items: [
            {"id":"panel-panel-auto-hide","title":"Hide the panel automatically","keywords":"Panel auto-hide bar hide","key":"bar.autohide","source":"SettingsCorePage.qml","explanation":"Move to the top edge to show the panel. The panel stays visible above your windows.","searchExplanation":"Move to the top edge to show the panel."},
            {"id":"panel-workspaces-in-overview","title":"Show workspaces in the overview","keywords":"Workspaces in overview panel overview","key":"bar.overview_workspaces","source":"SettingsCorePage.qml","explanation":"Workspace numbers stay visible in the overview. Workspace numbers hide in the overview.","searchExplanation":"Workspace numbers stay visible in the overview."},
            {"id":"panel-show-dock","title":"Show the dock","keywords":"Show dock dock enabled","key":"dock.on","source":"SettingsCorePage.qml","explanation":"Your pinned and running apps appear in the dock. The dock is hidden.","searchExplanation":"Your pinned and running apps appear in the dock."},
            {"id":"panel-dock-auto-hide","title":"Hide the dock automatically","keywords":"Dock auto-hide hide dock","key":"dock.auto_hide","source":"SettingsCorePage.qml","explanation":"Move to the bottom edge to show your apps. The dock stays visible beside your windows.","searchExplanation":"Move to the bottom edge to show your apps."}
        ]
    },
    {
        id: "windows",
        title: "Windows & Workspaces",
        group: "Personal",
        description: "Choose window spacing, initial width and focus behavior.",
        colors: ["#f39a5f","#e2733f"],
        items: [
            {"id":"windows-window-gaps","title":"Gaps","keywords":"Window gaps spacing between windows","key":"appearance.gaps","source":"SettingsCorePage.qml","explanation":"px of wallpaper between windows.","searchExplanation":"Wallpaper between windows, in pixels."},
            {"id":"windows-new-window-width","title":"Width of a new window","keywords":"New window width default column size","key":"windows.default_column_width","source":"SettingsCorePage.qml","explanation":"Choose how much of the screen a new column uses.","staticExplanation":true},
            {"id":"windows-focus-follows-mouse","title":"Focus follows the mouse","keywords":"Focus follows mouse pointer hover","key":"windows.focus_follows_mouse","source":"SettingsCorePage.qml","explanation":"Use your existing focus behavior, or focus windows under the pointer.","staticExplanation":true}
        ]
    },
    {
        id: "displays",
        title: "Displays",
        group: "Devices",
        description: "Arrange your screens and set their resolution, scale and refresh rate.",
        colors: ["#9db6d0","#6f8cab"],
        items: [
            {"id":"displays-brightness","title":"Brightness","keywords":"Brightness screen backlight dim","source":"DisplaysSettingsPage.qml","explanation":"ready % of the built-in backlight. No adjustable backlight is available.","searchExplanation":"Built-in backlight level."},
            {"id":"displays-resolution","title":"Resolution","keywords":"Resolution screen pixels","source":"DisplaysSettingsPage.qml","explanation":"Choose how many pixels the screen shows.","staticExplanation":true},
            {"id":"displays-scale","title":"Scale","keywords":"Scale text size zoom","source":"DisplaysSettingsPage.qml","explanation":"Make text and controls larger or smaller.","staticExplanation":true},
            {"id":"displays-refresh-rate","title":"Refresh rate","keywords":"Refresh rate hz frequency","source":"DisplaysSettingsPage.qml","explanation":"Choose how often the screen updates each second.","staticExplanation":true},
            {"id":"displays-arrangement","title":"Arrangement","keywords":"Arrangement monitor position","source":"DisplaysSettingsPage.qml","explanation":"Drag the screens to match your desk. The top bar marks the main screen. Use arrow keys to move a focused screen."},
            {"id":"displays-main-display","title":"Main screen","keywords":"Main display primary monitor","source":"DisplaysSettingsPage.qml","explanation":"Choose the display focused now and at sign-in.","staticExplanation":true},
            {"id":"displays-rotation","title":"Rotation","keywords":"Rotation orientation","source":"DisplaysSettingsPage.qml","explanation":"Match the orientation of your screen.","staticExplanation":true},
            {"id":"displays-night-light","title":"Night Light","keywords":"Night Light warm colors blue light","source":"DisplaysSettingsPage.qml","explanation":"Use warmer display colours for evening light.","staticExplanation":true},
            {"id":"displays-night-light-schedule","title":"Schedule","keywords":"Night Light schedule sunset sunrise time","source":"DisplaysSettingsPage.qml","explanation":"Keep Night Light on or use your own hours.","staticExplanation":true},
            {"id":"displays-night-light-temperature","title":"Warmth","keywords":"Night Light temperature warmth kelvin","source":"DisplaysSettingsPage.qml","explanation":"K. Choose how warm Night Light makes the display.","searchExplanation":"Choose how warm Night Light makes the display."},
            {"id":"displays-enabled","title":"Use this display","keywords":"screen on off enabled","source":"DisplaysSettingsPage.qml","explanation":"Turn this screen on or off for this session. At least one screen must stay on.","staticExplanation":true},
            {"id":"displays-night-light-start","title":"Start time","keywords":"night light custom hours evening","source":"DisplaysSettingsPage.qml","explanation":"Use local time in 24-hour format (HH:MM)."},
            {"id":"displays-night-light-end","title":"End time","keywords":"night light custom hours morning","source":"DisplaysSettingsPage.qml","explanation":"Use local time in 24-hour format (HH:MM)."}
        ]
    },
    {
        id: "sound",
        title: "Sound",
        group: "Devices",
        description: "Speakers, headphones and microphones, volume per app, system sounds.",
        colors: ["#e3a0b6","#c96f8f"],
        items: [
            {"id":"sound-mute","title":"Mute output","keywords":"Mute silence audio output","source":"SoundSettingsPage.qml","explanation":"Silence your speakers or headphones.","staticExplanation":true},
            {"id":"sound-volume","title":"Volume","keywords":"Volume loudness audio speakers headphones","source":"SoundSettingsPage.qml","explanation":"% of the output level. Reset restores 100%.","searchExplanation":"Output level. Reset restores 100%."},
            {"id":"sound-output","title":"Output device","keywords":"Output","source":"SoundSettingsPage.qml","explanation":"Sound plays through  .","searchExplanation":"The device used to play sound."},
            {"id":"sound-input","title":"Input device","keywords":"Input microphone device recording","source":"SoundSettingsPage.qml","explanation":"Record through  .","searchExplanation":"The device used to record sound."},
            {"id":"sound-input-volume","title":"Input volume","keywords":"Input volume microphone recording gain","source":"SoundSettingsPage.qml","explanation":"% of the input level. Reset restores 100%.","searchExplanation":"Input level. Reset restores 100%."},
            {"id":"sound-input-mute","title":"Mute microphone","keywords":"Mute microphone silence input recording","source":"SoundSettingsPage.qml","explanation":"Stop sound from the selected microphone.","staticExplanation":true},
            {"id":"sound-input-level","title":"Input level","keywords":"Input level microphone test meter","source":"SoundSettingsPage.qml","explanation":"Speak to test your microphone while this page is open.","staticExplanation":true},
            {"id":"sound-volume-per-app","title":"Volume per app","keywords":"Volume per app","source":"SoundSettingsPage.qml","explanation":""}
        ]
    },
    {
        id: "keyboard",
        title: "Keyboard",
        group: "Devices",
        description: "Layouts and how to switch them, key repeat, and every shortcut.",
        colors: ["#8f7f70","#5e5045"],
        items: [
            {"id":"keyboard-keyboard-layouts","title":"Layouts","keywords":"Keyboard layouts language input","key":"keyboard.layouts","source":"KeyboardSettingsPage.qml","explanation":""},
            {"id":"keyboard-switch-layout-shortcut","title":"Switch layouts with","keywords":"Switch layout shortcut language key","key":"keyboard.switch_key","source":"KeyboardSettingsPage.qml","explanation":"Go to the next language in your layout list.","staticExplanation":true},
            {"id":"keyboard-key-repeat-delay","title":"Delay before repeat","keywords":"Key repeat delay hold keyboard","key":"keyboard.repeat_delay","source":"KeyboardSettingsPage.qml","explanation":"ms before a held key starts repeating.","searchExplanation":"Time before a held key starts repeating."},
            {"id":"keyboard-key-repeat-rate","title":"Repeat speed","keywords":"Key repeat rate speed characters","key":"keyboard.repeat_rate","source":"KeyboardSettingsPage.qml","explanation":"characters per second while a key is held.","searchExplanation":"Characters per second while a key is held."},
            {"id":"keyboard-keyboard-shortcuts","title":"Shortcuts","keywords":"Keyboard shortcuts bindings hotkeys","source":"KeyboardSettingsPage.qml","explanation":""},
            {"id":"keyboard-toggle-floating-window-shortcut","title":"Toggle floating window","keywords":"Toggle floating window shortcut float window keybinding","key":"keybindings.toggle_window_floating","source":"KeyboardSettingsPage.qml","explanation":"keybindings.toggle_window_floating Switch the focused window between floating and tiled.","searchExplanation":"Switch the focused window between floating and tiled."}
        ]
    },
    {
        id: "mouse",
        title: "Mouse & Trackpad",
        group: "Devices",
        description: "Pointer speed, scrolling direction, tap to click and gestures.",
        colors: ["#8f7f70","#5e5045"],
        items: [
            {"id":"mouse-pointer-speed","title":"Pointer speed","keywords":"Mouse pointer speed acceleration","key":"mouse.speed","source":"MouseSettingsPage.qml","explanation":"Pointer speed:  % of the adjustment range.","searchExplanation":"Pointer speed as a percentage of the adjustment range."},
            {"id":"mouse-natural-scrolling","title":"Natural scrolling","keywords":"Mouse natural scrolling direction","key":"mouse.natural_scroll","source":"MouseSettingsPage.qml","explanation":"Content moves in the direction you scroll. Content moves against the direction you scroll.","searchExplanation":"Content moves in the direction you scroll."},
            {"id":"mouse-trackpad-speed","title":"Pointer speed","keywords":"Trackpad pointer speed acceleration","key":"touchpad.speed","source":"MouseSettingsPage.qml","explanation":"Pointer speed:  % of the adjustment range.","searchExplanation":"Pointer speed as a percentage of the adjustment range."},
            {"id":"mouse-trackpad-scrolling","title":"Natural scrolling","keywords":"Trackpad natural scrolling direction fingers","key":"touchpad.natural_scroll","source":"MouseSettingsPage.qml","explanation":"Content follows your fingers. Content moves against your fingers.","searchExplanation":"Content follows your fingers."},
            {"id":"mouse-tap-to-click","title":"Tap to click","keywords":"Tap to click trackpad","key":"touchpad.tap","source":"MouseSettingsPage.qml","explanation":"Tap the trackpad to click. Press the trackpad to click.","searchExplanation":"Tap the trackpad to click."},
            {"id":"mouse-two-finger-right-click","title":"Two-finger right click","keywords":"Two-finger right click trackpad","key":"touchpad.two_finger_right_click","source":"MouseSettingsPage.qml","explanation":"Click with two fingers to right-click."},
            {"id":"mouse-disable-while-typing","title":"Disable while typing","keywords":"Disable while typing trackpad keyboard","key":"touchpad.disable_while_typing","source":"MouseSettingsPage.qml","explanation":"The trackpad pauses while you type. The trackpad stays active while you type.","searchExplanation":"The trackpad pauses while you type."},
            {"id":"mouse-gestures","title":"Scroll while dragging","keywords":"Scroll while dragging gestures edges","key":"gestures.dnd_edge_view_scroll","source":"MouseSettingsPage.qml","explanation":"Drag to the left or right edge to scroll through windows. Dragging to the left or right edge does not scroll.","searchExplanation":"Drag to the left or right edge to scroll through windows."},
            {"id":"mouse-workspace-gestures","title":"Switch workspaces while dragging in the overview","keywords":"Switch workspaces while dragging in the overview gestures edges","key":"gestures.dnd_edge_workspace_switch","source":"MouseSettingsPage.qml","explanation":"In the overview, drag to the top or bottom edge to switch workspaces. In the overview, dragging to the top or bottom edge keeps this workspace.","searchExplanation":"In the overview, drag to the top or bottom edge to switch workspaces."}
        ]
    },
    {
        id: "notifications",
        title: "Notifications",
        group: "System",
        description: "Which apps may notify you, and quiet hours with Do Not Disturb.",
        colors: ["#f07d6e","#d9534a"],
        items: [
            {"id":"notifications-apps-that-may-notify","title":"Applications","keywords":"Apps that may notify allow silent off rules","key":"notifications.rules","source":"NotificationsSettingsPage.qml","explanation":"Allow popups, keep only in history, or block notifications.","staticExplanation":true},
            {"id":"notifications-do-not-disturb","title":"Do not disturb now","keywords":"Do Not Disturb quiet now","key":"notifications.dnd","source":"NotificationsSettingsPage.qml","explanation":"Keep notifications in history without showing popups.","staticExplanation":true},
            {"id":"notifications-until","title":"Pause notifications until","keywords":"Quiet until do not disturb timer","key":"notifications.until","source":"NotificationsSettingsPage.qml","explanation":"Paused until  ddd HH:mm . Pause popups for a limited time.","searchExplanation":"Pause popups for a limited time."},
            {"id":"notifications-do-not-disturb-on-a-schedule","title":"Use a daily schedule","keywords":"Do Not Disturb on a schedule quiet hours daily","key":"notifications.schedule","source":"NotificationsSettingsPage.qml","explanation":"Pause popups between these local times; matching times pause all day.","staticExplanation":true},
            {"id":"notifications-start","title":"Start time","keywords":"quiet hours daily do not disturb","source":"NotificationsSettingsPage.qml","explanation":"Begin the daily pause at this time (HH:MM).","staticExplanation":true},
            {"id":"notifications-end","title":"End time","keywords":"quiet hours daily do not disturb","source":"NotificationsSettingsPage.qml","explanation":"End the daily pause at this time (HH:MM).","staticExplanation":true}
        ]
    },
    {
        id: "battery",
        title: "Battery & Power",
        group: "System",
        description: "Sleep, the lid, power modes, a charge limit and battery health.",
        colors: ["#c2cf8a","#93a65a"],
        items: [
            {"id":"battery-blank-on-battery","title":"Blank the screen on battery","keywords":"Blank the screen on battery inactivity screen off","source":"SettingsPowerPage.qml","explanation":"Turn off the screen after this much inactivity on battery power.","staticExplanation":true},
            {"id":"battery-blank-on-power","title":"Blank the screen when plugged in","keywords":"Blank the screen when plugged in inactivity screen off power","source":"SettingsPowerPage.qml","explanation":"Turn off the screen after this much inactivity on external power.","staticExplanation":true},
            {"id":"battery-power-mode","title":"Power mode","keywords":"Power mode","source":"SettingsPowerPage.qml","explanation":"Balance energy use and performance.","staticExplanation":true},
            {"id":"battery-charge-limit","title":"Charge limit","keywords":"Charge limit","source":"SettingsPowerPage.qml","explanation":"Stop charging at this percentage. The limit is restored at startup.","staticExplanation":true},
            {"id":"battery-battery-health","title":"Battery health","keywords":"Battery health","source":"SettingsPowerPage.qml","explanation":"Battery health is not reported by this hardware. % of the original full-charge capacity.","searchExplanation":"Percentage of the original full-charge capacity."}
        ]
    },
    {
        id: "lock",
        title: "Lock & Login",
        group: "System",
        description: "Your password, fingerprint or face, when to lock, and the lock screen.",
        colors: ["#8f7f70","#5e5045"],
        items: [
            {"id":"lock-password","title":"Change password","keywords":"Change password login wallet terminal","source":"SettingsMaintenancePage.qml","explanation":"Open the system password prompt in your terminal. Your login wallet may still use your previous password; update its password separately.","staticExplanation":true},
            {"id":"lock-when-to-lock","title":"Lock after inactivity","keywords":"When to lock","source":"SettingsPowerPage.qml","explanation":"Require your password after this much time without input.","staticExplanation":true}
        ]
    },
    {
        id: "apps",
        title: "Apps",
        group: "System",
        description: "Default apps, apps that start with the session, and installed programs.",
        colors: ["#f2cc7e","#e3a243"],
        items: [
            {"id":"apps-default-terminal","title":"Terminal","keywords":"Default terminal console application","key":"defaults.terminal","source":"SettingsAppsPage.qml","explanation":"Open a terminal with this app.","staticExplanation":true},
            {"id":"apps-default-browser","title":"Web browser","keywords":"Default browser web links","key":"defaults.browser","source":"SettingsAppsPage.qml","explanation":"Open web links with this app.","staticExplanation":true},
            {"id":"apps-default-file-manager","title":"Files","keywords":"Default file manager folders files","key":"defaults.files","source":"SettingsAppsPage.qml","explanation":"Open folders with this app.","staticExplanation":true},
            {"id":"apps-default-mail","title":"Mail","keywords":"Default mail app email messages","key":"defaults.mail","source":"SettingsAppsPage.qml","explanation":"Open email links with this app.","staticExplanation":true},
            {"id":"apps-default-editor","title":"Text editor","keywords":"Default text editor text documents","key":"defaults.editor","source":"SettingsAppsPage.qml","explanation":"Open plain text files with this app.","staticExplanation":true},
            {"id":"apps-startup-apps","title":"Startup apps","keywords":"Startup apps autostart login session","source":"SettingsAppsPage.qml","explanation":"Start this app when you sign in."}
        ]
    },
    {
        id: "updates",
        title: "Updates & Recovery",
        group: "System",
        description: "Update channel and checks, snapshots, and going back to before an update.",
        colors: ["#f39a5f","#e2733f"],
        items: [
            {"id":"updates-update-channel","title":"Channel","keywords":"Update channel","source":"SettingsMaintenancePage.qml","explanation":"Choose which releases your next update uses.","staticExplanation":true}
        ]
    },
    {
        id: "privacy",
        title: "Privacy & Security",
        group: "System",
        description: "Which apps may use the camera, microphone and screen; firewall; disk encryption.",
        colors: ["#9db6d0","#6f8cab"],
        items: [

        ]
    },
    {
        id: "region",
        title: "Region & Time",
        group: "System",
        description: "Language, formats, time zone and the 24-hour clock.",
        colors: ["#a8cfc0","#78a796"],
        items: [
            {"id":"region-language","title":"Language","keywords":"Language installed sign out apps","source":"SettingsRegionPage.qml","explanation":"Choose an installed language. Sign out to use it in all apps.","staticExplanation":true},
            {"id":"region-formats","title":"Formats","keywords":"Formats installed locale dates numbers","source":"SettingsRegionPage.qml","explanation":"Choose an installed locale for dates and numbers.","staticExplanation":true},
            {"id":"region-time-zone","title":"Time zone","keywords":"Time zone installed computer","source":"SettingsRegionPage.qml","explanation":"Choose a time zone installed on this computer.","staticExplanation":true},
            {"id":"region-automatic-time","title":"Automatic time","keywords":"Automatic time clock synchronization","source":"SettingsRegionPage.qml","explanation":"Keep the clock in sync with an internet time service.","staticExplanation":true}
        ]
    },
    {
        id: "users",
        title: "Users",
        group: "System",
        description: "Your account, picture and password, and other people on this computer.",
        colors: ["#e3a0b6","#c96f8f"],
        items: [

        ]
    },
    {
        id: "storage",
        title: "Storage",
        group: "System",
        description: "What takes space, and cleaning the package cache and old snapshots.",
        colors: ["#8f7f70","#5e5045"],
        items: [

        ]
    },
    {
        id: "accessibility",
        title: "Accessibility",
        group: "System",
        description: "Larger text, more contrast, less motion, zoom and sticky keys.",
        colors: ["#9db6d0","#6f8cab"],
        items: [

        ]
    },
    {
        id: "about",
        title: "About",
        group: "System",
        description: "This Emaki version and this computer’s hardware.",
        colors: ["#f39a5f","#e2733f"],
        items: [
            {"id":"about-emaki-version","title":"Version","keywords":"Emaki version release version","source":"SettingsAboutPage.qml","explanation":""},
            {"id":"about-hardware","title":"This computer","keywords":"Hardware processor memory computer","source":"SettingsAboutPage.qml","explanation":""},
            {"id":"about-report-a-problem","title":"Report a problem","keywords":"Report a problem bug diagnostics","source":"SettingsAboutPage.qml","explanation":"Collect logs and a short system summary into a file you can review before sharing.","staticExplanation":true}
        ]
    },
    {
        id: "assistants",
        title: "AI Assistants",
        group: "System",
        description: "Protection and permissions for installed assistants.",
        colors: ["#9db6d0","#6f8cab"],
        items: [

        ]
    }
];

function section(id) {
    for (var i = 0; i < sections.length; ++i)
        if (sections[i].id === id) return sections[i];
    return null;
}

function normalized(value) {
    return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function search(query, availablePages, availableTargets) {
    var words = normalized(query).split(" ");
    if (!words[0]) return [];
    var available = Array.isArray(availablePages) ? availablePages : [];
    var targets = Array.isArray(availableTargets) ? availableTargets : null;
    var results = [];
    function matches(text) {
        var haystack = normalized(text);
        return words.every(word => haystack.indexOf(word) !== -1);
    }
    for (var i = 0; i < sections.length; ++i) {
        var page = sections[i];
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
