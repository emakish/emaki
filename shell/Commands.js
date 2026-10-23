// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// The command menu: every system action in one list. Data, not QML: an action is
//   id        stable identifier (tests, the row's selection key)
//   context   the translation context of its title: a title Emaki already shows elsewhere
//             (a Settings page, the power panel) keeps that place's translation
//   title     English source text, translated at display time
//   keywords  English search words, translated at display time in the "Commands" context
//   icon      a symbolic icon name from the icon theme
//   call      what it does, run by LauncherBody.runCommand():
//               {settings: "<page id>"}          open Settings on that page
//               {mode: "<launcher mode>"}        switch the launcher to that mode
//               {run: ["argv", ...]}             close the launcher, start the program
//               {run: [...], app: "<id>"}        the same as an app (AppLaunch: its own scope)
//               {setting: ["<key>", "<value>"]}  set an Emaki setting (the Settings catalogue)
//               {night: true|false}              turn Night Light on or off
//               {session: "logout"|"reboot"|"poweroff"}  open the power panel's confirmation for
//                                                it, Cancel focused; never runs at once
// Search matches the translated title and keywords and the English ones, so an English word
// finds an action in any language. To add an action, add one entry here; tests/test-launcher-
// commands.py checks that every call resolves to something Emaki has.

function action(id, context, title, keywords, icon, call) {
    return {id: id, context: context, title: title, keywords: keywords, icon: icon, call: call};
}

function page(id, title, keywords, icon) {
    return action("settings-" + id, "Shell", title, keywords, icon, {settings: id});
}

const actions = [
    // Session and system.
    action("lock", "SystemBody", QT_TRANSLATE_NOOP("SystemBody", "Lock"), QT_TRANSLATE_NOOP("Commands", "lock screen away password"), "system-lock-screen-symbolic", {run: ["emaki-lock"]}),
    action("logout", "SystemBody", QT_TRANSLATE_NOOP("SystemBody", "Log out"), QT_TRANSLATE_NOOP("Commands", "log out sign out leave session exit"), "system-log-out-symbolic", {session: "logout"}),
    action("reboot", "SystemBody", QT_TRANSLATE_NOOP("SystemBody", "Restart"), QT_TRANSLATE_NOOP("Commands", "restart reboot computer"), "system-reboot-symbolic", {session: "reboot"}),
    action("poweroff", "SystemBody", QT_TRANSLATE_NOOP("SystemBody", "Shut down"), QT_TRANSLATE_NOOP("Commands", "shut down power off turn off computer"), "system-shutdown-symbolic", {session: "poweroff"}),
    action("theme-dark", "Commands", QT_TRANSLATE_NOOP("Commands", "Switch to dark appearance"), QT_TRANSLATE_NOOP("Commands", "dark mode theme night appearance"), "weather-clear-night-symbolic", {setting: ["appearance.theme", "dark"]}),
    action("theme-light", "Commands", QT_TRANSLATE_NOOP("Commands", "Switch to light appearance"), QT_TRANSLATE_NOOP("Commands", "light mode theme day appearance"), "weather-clear-symbolic", {setting: ["appearance.theme", "light"]}),
    action("night-on", "Commands", QT_TRANSLATE_NOOP("Commands", "Turn Night Light on"), QT_TRANSLATE_NOOP("Commands", "night light warm screen evening blue"), "night-light-symbolic", {night: true}),
    action("night-off", "Commands", QT_TRANSLATE_NOOP("Commands", "Turn Night Light off"), QT_TRANSLATE_NOOP("Commands", "night light warm screen evening blue"), "night-light-symbolic", {night: false}),
    action("updates", "Commands", QT_TRANSLATE_NOOP("Commands", "Check for updates"), QT_TRANSLATE_NOOP("Commands", "update upgrade programs system new version"), "software-update-available-symbolic", {run: ["emaki-update-manager", "--updates"], app: "emaki-update-manager"}),
    action("install", "Commands", QT_TRANSLATE_NOOP("Commands", "Install a program"), QT_TRANSLATE_NOOP("Commands", "install add get program app software store"), "system-software-install-symbolic", {run: ["emaki-update-manager"], app: "emaki-update-manager"}),
    // Tools.
    action("screenshot", "Commands", QT_TRANSLATE_NOOP("Commands", "Take a screenshot"), QT_TRANSLATE_NOOP("Commands", "screenshot capture picture screen print"), "camera-photo-symbolic", {run: ["niri", "msg", "action", "screenshot"]}),
    action("record", "Commands", QT_TRANSLATE_NOOP("Commands", "Record the screen"), QT_TRANSLATE_NOOP("Commands", "record video screencast capture screen"), "media-record-symbolic", {run: ["emaki-shell", "call", "capture", "record"]}),
    action("capture-text", "Commands", QT_TRANSLATE_NOOP("Commands", "Copy text from screen"), QT_TRANSLATE_NOOP("Commands", "copy text screen recognize ocr clipboard"), "edit-copy-symbolic", {run: ["emaki-shell", "call", "capture", "text"]}),
    action("capture-qr", "Commands", QT_TRANSLATE_NOOP("Commands", "Read QR code"), QT_TRANSLATE_NOOP("Commands", "qr code scan read screen link"), "camera-photo-symbolic", {run: ["emaki-shell", "call", "capture", "qr"]}),
    action("development", "Commands", QT_TRANSLATE_NOOP("Commands", "Development kits"), QT_TRANSLATE_NOOP("Commands", "development programming tools code compiler containers"), "applications-development-symbolic", {run: ["emaki-update-manager"], app: "emaki-update-manager"}),
    action("websites", "Commands", QT_TRANSLATE_NOOP("Commands", "Add a website as an app"), QT_TRANSLATE_NOOP("Commands", "website web app browser shortcut add"), "web-browser-symbolic", {run: ["emaki-update-manager"], app: "emaki-update-manager"}),
    action("clipboard", "Commands", QT_TRANSLATE_NOOP("Commands", "Clipboard history"), QT_TRANSLATE_NOOP("Commands", "clipboard copied paste history"), "edit-paste-symbolic", {mode: "Clipboard"}),
    action("emoji", "Commands", QT_TRANSLATE_NOOP("Commands", "Emoji"), QT_TRANSLATE_NOOP("Commands", "emoji smiley symbols characters"), "face-smile-symbolic", {mode: "Emoji"}),
    action("shortcuts", "Commands", QT_TRANSLATE_NOOP("Commands", "Keyboard shortcuts"), QT_TRANSLATE_NOOP("Commands", "keyboard shortcuts keys hotkeys"), "preferences-desktop-keyboard-shortcuts-symbolic", {run: ["emaki-shell", "call", "keyboard", "open", "shortcuts"]}),
    action("notes", "Commands", QT_TRANSLATE_NOOP("Commands", "Open Notes"), QT_TRANSLATE_NOOP("Commands", "notes note write checklist"), "accessories-text-editor-symbolic", {run: ["emaki-notes"], app: "emaki-notes"}),
    action("report", "Commands", QT_TRANSLATE_NOOP("Commands", "Report a problem"), QT_TRANSLATE_NOOP("Commands", "report problem bug help feedback"), "dialog-warning-symbolic", {run: ["emaki-shell", "call", "report", "open", "other"]}),
    action("reset", "Commands", QT_TRANSLATE_NOOP("Commands", "Reset Emaki settings"), QT_TRANSLATE_NOOP("Commands", "reset defaults restore settings"), "edit-undo-symbolic", {settings: "updates"}),
    // Settings pages (shell/SettingsPageRegistry.qml ids, titles of shell/settings/SettingsIndex.js).
    page("wifi", QT_TRANSLATE_NOOP("Shell", "Wi-Fi"), QT_TRANSLATE_NOOP("Commands", "wifi wireless network internet"), "network-wireless-symbolic"),
    page("bluetooth", QT_TRANSLATE_NOOP("Shell", "Bluetooth"), QT_TRANSLATE_NOOP("Commands", "bluetooth headphones devices pair"), "bluetooth-symbolic"),
    page("network", QT_TRANSLATE_NOOP("Shell", "Network"), QT_TRANSLATE_NOOP("Commands", "ethernet cable wired vpn dns internet"), "network-wired-symbolic"),
    page("appearance", QT_TRANSLATE_NOOP("Shell", "Appearance"), QT_TRANSLATE_NOOP("Commands", "theme light dark accent colour color"), "preferences-desktop-theme-symbolic"),
    page("wallpaper", QT_TRANSLATE_NOOP("Shell", "Wallpaper"), QT_TRANSLATE_NOOP("Commands", "background picture image desktop"), "preferences-desktop-wallpaper-symbolic"),
    page("panel", QT_TRANSLATE_NOOP("Shell", "Panel & Dock"), QT_TRANSLATE_NOOP("Commands", "bar panel dock taskbar hide"), "preferences-system-symbolic"),
    page("windows", QT_TRANSLATE_NOOP("Shell", "Windows & Workspaces"), QT_TRANSLATE_NOOP("Commands", "windows workspaces gaps width focus columns"), "preferences-system-windows-symbolic"),
    page("displays", QT_TRANSLATE_NOOP("Shell", "Displays"), QT_TRANSLATE_NOOP("Commands", "monitor screen scale resolution"), "video-display-symbolic"),
    page("sound", QT_TRANSLATE_NOOP("Shell", "Sound"), QT_TRANSLATE_NOOP("Commands", "audio volume speakers microphone"), "audio-volume-high-symbolic"),
    page("keyboard", QT_TRANSLATE_NOOP("Shell", "Keyboard"), QT_TRANSLATE_NOOP("Commands", "keyboard layout typing repeat"), "input-keyboard-symbolic"),
    page("mouse", QT_TRANSLATE_NOOP("Shell", "Mouse & Trackpad"), QT_TRANSLATE_NOOP("Commands", "mouse touchpad trackpad pointer scroll"), "input-mouse-symbolic"),
    page("notifications", QT_TRANSLATE_NOOP("Shell", "Notifications"), QT_TRANSLATE_NOOP("Commands", "notifications alerts do not disturb"), "preferences-system-notifications-symbolic"),
    page("battery", QT_TRANSLATE_NOOP("Shell", "Battery & Power"), QT_TRANSLATE_NOOP("Commands", "battery power sleep energy"), "battery-symbolic"),
    page("lock", QT_TRANSLATE_NOOP("Shell", "Lock & Login"), QT_TRANSLATE_NOOP("Commands", "lock login password screen"), "system-lock-screen-symbolic"),
    page("apps", QT_TRANSLATE_NOOP("Shell", "Apps"), QT_TRANSLATE_NOOP("Commands", "default apps browser mail"), "preferences-desktop-apps-symbolic"),
    page("updates", QT_TRANSLATE_NOOP("Shell", "Updates & Recovery"), QT_TRANSLATE_NOOP("Commands", "updates recovery snapshots maintenance reset"), "software-update-available-symbolic"),
    page("privacy", QT_TRANSLATE_NOOP("Shell", "Privacy & Security"), QT_TRANSLATE_NOOP("Commands", "privacy security encryption permissions"), "security-high-symbolic"),
    page("region", QT_TRANSLATE_NOOP("Shell", "Region & Time"), QT_TRANSLATE_NOOP("Commands", "language region time date format"), "preferences-desktop-locale-symbolic"),
    page("users", QT_TRANSLATE_NOOP("Shell", "Users"), QT_TRANSLATE_NOOP("Commands", "users accounts password picture"), "system-users-symbolic"),
    page("storage", QT_TRANSLATE_NOOP("Shell", "Storage"), QT_TRANSLATE_NOOP("Commands", "disk space storage free"), "drive-harddisk-symbolic"),
    page("about", QT_TRANSLATE_NOOP("Shell", "About"), QT_TRANSLATE_NOOP("Commands", "about version system information"), "help-about-symbolic"),
    page("assistants", QT_TRANSLATE_NOOP("Shell", "AI Assistants"), QT_TRANSLATE_NOOP("Commands", "sudo keys"), "dialog-password-symbolic")
];

// How well one typed word hits a text: start of the text > start of a word > anywhere.
function hit(text, word) {
    if (!text.includes(word))
        return 0;
    if (text.startsWith(word))
        return 3;
    return text.split(/[\s\-_./()&]+/).some(part => part.startsWith(word)) ? 2 : 1;
}

// The rows for a query: every typed word must hit the title or the keywords, in the person's
// language or in English; titles weigh more than keywords. An empty query lists them all.
function fold(text) {
    return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase();
}

function search(list, text) {
    const typed = fold(text.trim()).split(/\s+/).filter(word => word.length > 0);
    const found = [];
    for (const entry of list) {
        const title = qsTranslate(entry.context, entry.title);
        const primary = fold(title + " " + entry.title);
        // A Settings page is also found by the word "Settings" itself.
        const settings = entry.call.settings ? " " + qsTranslate("LauncherBody", "Settings") + " settings" : "";
        const secondary = fold(qsTranslate("Commands", entry.keywords) + " " + entry.keywords + settings);
        let score = 0;
        for (const word of typed) {
            const s = Math.max(hit(primary, word) * 10, hit(secondary, word) * 3);
            if (!s) {
                score = -1;
                break;
            }
            score += s;
        }
        if (score >= 0)
            found.push({entry: entry, title: title, score: score});
    }
    if (typed.length)
        found.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
    return found;
}
