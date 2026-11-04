.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Adwaita "*-symbolic" names for what the system island and its panel show
// (docs/mockups/liquid-glass/system.js cells() and buildView()), from plain values. The
// SymbolIcon that draws a name falls back to Icon.qml's own shapes when the theme lacks it.

// Signal strength 0..1 (0..100 accepted) → the Wi-Fi bars, GNOME's thresholds (80/55/30/5).
function signal(strength) {
    const s = Number(strength);
    const v = s > 1 ? s / 100 : s;
    const level = v > .8 ? "excellent" : v > .55 ? "good" : v > .3 ? "ok" : v > .05 ? "weak" : "none";
    return "network-wireless-signal-" + level + "-symbolic";
}
// system.js soundIcon(): muted or silent, above half, else medium.
function sound(ready, volume, muted) {
    if (!ready || muted || volume <= 0)
        return "audio-volume-muted-symbolic";
    return volume > .5 ? "audio-volume-high-symbolic" : "audio-volume-medium-symbolic";
}
// Battery by tens, with the charging variant; no battery: the power symbol (the cell is
// then the power menu only).
function battery(percent, charging) {
    if (percent < 0)
        return "system-shutdown-symbolic";
    if (charging && percent >= 100)
        return "battery-level-100-charged-symbolic";
    const level = Math.max(0, Math.min(100, Math.round(percent / 10) * 10));
    return "battery-level-" + level + (charging ? "-charging" : "") + "-symbolic";
}
// BlueZ's device icon ("audio-headphones", "input-keyboard", "phone") as a symbolic name.
function bluetoothDevice(icon) {
    return icon ? String(icon) + "-symbolic" : "bluetooth-active-symbolic";
}
// A PipeWire device's own icon name (device.icon_name: "audio-speakers",
// "audio-input-microphone", "audio-headphones") as a symbolic name.
function audioDevice(icon, isSink) {
    return icon ? String(icon) + "-symbolic" : isSink ? "audio-speakers-symbolic" : "audio-input-microphone-symbolic";
}

function isSettings(name) {
    return ["preferences-system", "preferences-system-symbolic", "emaki-settings", "org.emaki.Settings"].indexOf(String(name)) >= 0;
}

// The Settings gear: a pixel gear around a square hole, filled in one ink and with no tile
// behind it. The launcher draws it in the grey of its mode labels; elsewhere it takes the
// ink of the surface it sits on.
function settingsSvg(ink) {
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path ' + svgFill(ink) + ' fill-rule="evenodd" d="M10 4h4v3h3V6h2v4h1v4h-3v3h1v2h-4v1h-4v-3H7v1H5v-4H4v-4h3V7H6V5h4zM10 10v4h4v-4z"/></svg>');
}

// A QML colour with alpha prints as #AARRGGBB, while CSS colour syntax reads eight hex digits
// as #RRGGBBAA: give SVG the opaque colour and the alpha as fill-opacity, which both read the
// same way (the launcher's grey label ink is 55 % ink in light).
function svgFill(ink) {
    if (ink !== null && typeof ink === "object" && typeof ink.a === "number") {
        const hex = value => ("0" + Math.round(value * 255).toString(16)).slice(-2);
        return 'fill="#' + hex(ink.r) + hex(ink.g) + hex(ink.b) + '" fill-opacity="' + Number(ink.a.toFixed(3)) + '"';
    }
    return 'fill="' + ink + '"';
}

function programsSvg(base, strong, soft, shade, ink) {
    // The installed app icon's geometry, also used for its own Installed row.
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 32 32" shape-rendering="crispEdges"><path fill="' + strong + '" d="M4 1h24v2h2v2h1v22h-2v2h-2v2H5v-2H3v-2H1V5h2V3h1z"/><path fill="' + base + '" d="M5 2h22v2h2v23h-2v2H5v-2H3V5h2z"/><path fill="' + soft + '" d="M5 2h22v2H5zM3 4h2v22H3z"/><path fill="' + shade + '" d="M9 16h14v2h2v9H7v-9h2z"/><path fill="' + ink + '" d="M8 15h5v2H9v7h14v-7h-4v-2h5v2h2v9H6v-9h2zM14 6h4v8h4v2h-2v2h-2v2h-4v-2h-2v-2h-2v-2h4z"/></svg>');
}

function welcomeSvg(base, strong, soft, ink) {
    // Welcome to Emaki: the app tile of Programs & Updates with two pixel sparkles.
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 32 32" shape-rendering="crispEdges"><path fill="' + strong + '" d="M4 1h24v2h2v2h1v22h-2v2h-2v2H5v-2H3v-2H1V5h2V3h1z"/><path fill="' + base + '" d="M5 2h22v2h2v23h-2v2H5v-2H3V5h2z"/><path fill="' + soft + '" d="M5 2h22v2H5zM3 4h2v22H3zM8 8h1v1H8zM25 22h1v1H25zM9 24h1v1H9z"/><path fill="' + ink + '" d="M22 4h1v1H22zM22 5h1v1H22zM21 6h3v1H21zM20 7h5v1H20zM14 8h1v1H14zM18 8h9v1H18zM14 9h1v1H14zM20 9h5v1H20zM14 10h1v1H14zM21 10h3v1H21zM13 11h3v1H13zM22 11h1v1H22zM13 12h3v1H13zM22 12h1v1H22zM12 13h5v1H12zM11 14h7v1H11zM10 15h9v1H10zM8 16h13v1H8zM5 17h19v1H5zM8 18h13v1H8zM10 19h9v1H10zM11 20h7v1H11zM12 21h5v1H12zM13 22h3v1H13zM13 23h3v1H13zM14 24h1v1H14zM14 25h1v1H14zM14 26h1v1H14z"/></svg>');
}

function notesSvg(base, strong, soft, shade, ink) {
    return "data:image/svg+xml;utf8," + encodeURIComponent("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"64\" height=\"64\" viewBox=\"0 0 32 32\" shape-rendering=\"crispEdges\">\n  <path fill=\"" + strong + "\" d=\"M4 1h24v2h2v2h1v22h-2v2h-2v2H5v-2H3v-2H1V5h2V3h1z\"/>\n  <path fill=\"" + base + "\" d=\"M5 2h22v2h2v23h-2v2H5v-2H3V5h2z\"/>\n  <path fill=\"" + soft + "\" d=\"M5 2h22v2H5zM3 4h2v22H3z\"/>\n  <path fill=\"" + shade + "\" d=\"M8 7h17v19H8z\"/>\n  <path fill=\"" + ink + "\" d=\"M7 6h17v19H7z\"/>\n  <path fill=\"" + strong + "\" d=\"M10 9h11v2H10zM10 14h2v2h-2zM14 14h7v2h-7zM10 19h2v2h-2zM14 19h7v2h-7z\"/>\n</svg>");
}

// Launcher symbols use the same rounded strokes as Settings and the system controls.
// They remain available when no external icon theme is installed.
function launcherSvg(name, ink) {
    if (isSettings(name))
        return settingsSvg(ink);
    const aliases = {
        "All": "all",
        "Apps": "apps",
        "Windows": "windows",
        "Files": "file",
        "Clipboard": "clipboard",
        "Commands": "commands",
        "Emoji": "emoji",
        "system-lock-screen-symbolic": "lock",
        "system-log-out-symbolic": "logout",
        "system-reboot-symbolic": "restart",
        "system-shutdown-symbolic": "power",
        "weather-clear-night-symbolic": "moon",
        "weather-clear-symbolic": "sun",
        "night-light-symbolic": "moon",
        "software-update-available-symbolic": "update",
        "system-software-install-symbolic": "install",
        "camera-photo-symbolic": "camera",
        "media-record-symbolic": "record",
        "edit-copy-symbolic": "copy",
        "applications-development-symbolic": "commands",
        "web-browser-symbolic": "web",
        "edit-paste-symbolic": "clipboard",
        "face-smile-symbolic": "emoji",
        "preferences-desktop-keyboard-shortcuts-symbolic": "keyboard",
        "accessories-text-editor-symbolic": "note",
        "dialog-warning-symbolic": "warning",
        "edit-undo-symbolic": "reset",
        "network-wireless-symbolic": "wifi",
        "bluetooth-symbolic": "bluetooth",
        "network-wired-symbolic": "network",
        "preferences-desktop-theme-symbolic": "appearance",
        "preferences-desktop-wallpaper-symbolic": "wallpaper",
        "preferences-system-windows-symbolic": "windows",
        "video-display-symbolic": "displays",
        "audio-volume-high-symbolic": "sound",
        "input-keyboard-symbolic": "keyboard",
        "input-mouse-symbolic": "mouse",
        "preferences-system-notifications-symbolic": "notifications",
        "battery-symbolic": "battery",
        "preferences-desktop-apps-symbolic": "apps",
        "security-high-symbolic": "privacy",
        "preferences-desktop-locale-symbolic": "region",
        "system-users-symbolic": "users",
        "drive-harddisk-symbolic": "storage",
        "help-about-symbolic": "about",
        "dialog-password-symbolic": "key"
};
    const paths = {
        "search": "<circle cx=\"11\" cy=\"11\" r=\"7\"/><path d=\"M20 20l-4-4\"/>",
        "lock": "<rect x=\"5\" y=\"10.5\" width=\"14\" height=\"10\" rx=\"2.5\"/><path d=\"M8 10.5V8a4 4 0 0 1 8 0v2.5\"/><path d=\"M12 14.5v2\"/>",
        "apps": "<rect x=\"4\" y=\"4\" width=\"6.5\" height=\"6.5\" rx=\"1.8\"/><rect x=\"13.5\" y=\"4\" width=\"6.5\" height=\"6.5\" rx=\"1.8\"/><rect x=\"4\" y=\"13.5\" width=\"6.5\" height=\"6.5\" rx=\"1.8\"/><rect x=\"13.5\" y=\"13.5\" width=\"6.5\" height=\"6.5\" rx=\"1.8\"/>",
        "windows": "<rect x=\"2.5\" y=\"5\" width=\"5\" height=\"14\" rx=\"1.5\"/><rect x=\"9.5\" y=\"5\" width=\"9\" height=\"14\" rx=\"1.5\"/><path d=\"M21 5v14\"/>",
        "keyboard": "<rect x=\"2.5\" y=\"6\" width=\"19\" height=\"12\" rx=\"2.5\"/><path d=\"M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M8 14.5h8\"/>",
        "mouse": "<rect x=\"6.5\" y=\"3\" width=\"11\" height=\"18\" rx=\"5.5\"/><path d=\"M12 7v3.5\"/>",
        "notifications": "<path d=\"M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z\"/><path d=\"M10 20.5a2 2 0 0 0 4 0\"/>",
        "battery": "<rect x=\"2.5\" y=\"7\" width=\"17\" height=\"10\" rx=\"2.5\"/><path d=\"M21.5 10.5v3\"/><path d=\"M11.5 9l-2 3h3l-2 3\"/>",
        "network": "<rect x=\"9\" y=\"3\" width=\"6\" height=\"5\" rx=\"1.2\"/><rect x=\"3\" y=\"16\" width=\"6\" height=\"5\" rx=\"1.2\"/><rect x=\"15\" y=\"16\" width=\"6\" height=\"5\" rx=\"1.2\"/><path d=\"M12 8v4M6 16v-4h12v4\"/>",
        "wifi": "<path d=\"M2.5 9a14 14 0 0 1 19 0\"/><path d=\"M5.5 12.5a9.5 9.5 0 0 1 13 0\"/><path d=\"M8.6 16a5 5 0 0 1 6.8 0\"/><circle cx=\"12\" cy=\"19.3\" r=\"1.1\" fill=\"#fffaf5\" stroke=\"none\"/>",
        "bluetooth": "<path d=\"M7 7.5l10 9-5 4.5V3l5 4.5-10 9\"/>",
        "wallpaper": "<rect x=\"3\" y=\"4.5\" width=\"18\" height=\"15\" rx=\"2.5\"/><path d=\"M3.5 17l5-5.5 4 4 2.5-2.5 5.5 5\"/><circle cx=\"15.5\" cy=\"9\" r=\"1.6\"/>",
        "appearance": "<circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 0 1 0 17z\" fill=\"#fffaf5\"/>",
        "displays": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"12\" rx=\"2\"/><path d=\"M9 20h6M12 16v4\"/>",
        "sound": "<path d=\"M4 9.5h3.5L12 5.5v13l-4.5-4H4z\"/><path d=\"M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11\"/>",
        "privacy": "<path d=\"M12 3l7.5 3v5.5c0 4.5-3.2 8.2-7.5 9.5-4.3-1.3-7.5-5-7.5-9.5V6z\"/><path d=\"M9 12l2 2 4-4\"/>",
        "region": "<circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M3.5 12h17M12 3.5c2.5 2.5 3.5 5.5 3.5 8.5s-1 6-3.5 8.5c-2.5-2.5-3.5-5.5-3.5-8.5s1-6 3.5-8.5z\"/>",
        "users": "<circle cx=\"9\" cy=\"8.5\" r=\"3.5\"/><path d=\"M2.5 19.5c.8-3.3 3.4-5 6.5-5s5.7 1.7 6.5 5\"/><circle cx=\"17\" cy=\"9\" r=\"2.6\"/><path d=\"M17.5 14.3c2.2.3 3.7 1.9 4.2 4.7\"/>",
        "storage": "<ellipse cx=\"12\" cy=\"6\" rx=\"7.5\" ry=\"3\"/><path d=\"M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3\"/>",
        "about": "<path d=\"M3 22L7.161 22L12.896 2L8.735 2ZM5.735 4L20 4L24 0L9.735 0ZM2.867 14L17.133 14L21.133 10L6.867 10ZM0 24L14.265 24L18.265 20L4 20Z\" fill=\"#fffaf5\" stroke=\"none\"/>",
        "reset": "<path d=\"M4.5 12a7.5 7.5 0 1 0 2.2-5.3\"/><path d=\"M4.5 3.5v4h4\"/>",
        "panel": "<rect x=\"3\" y=\"3.5\" width=\"18\" height=\"17\" rx=\"2.5\"/><path d=\"M3 8.5h18\"/><rect x=\"8\" y=\"15\" width=\"8\" height=\"2.6\" rx=\"1.3\"/>",
        "all": "<path d=\"m12 3-10 5 10 5 10-5-10-5zM2 12l10 5 10-5M2 16l10 5 10-5\"/>",
        "file": "<path d=\"M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6\"/>",
        "clipboard": "<rect x=\"5\" y=\"5\" width=\"14\" height=\"16\" rx=\"2\"/><rect x=\"9\" y=\"3\" width=\"6\" height=\"4\" rx=\"1\"/><path d=\"M9 12h6M9 16h6\"/>",
        "commands": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"m7 8 4 4-4 4M13 16h4\"/>",
        "emoji": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M8 9h.01M16 9h.01M7.5 14a5 5 0 0 0 9 0\"/>",
        "logout": "<path d=\"M10 3H4v18h6M10 12h11M17 8l4 4-4 4\"/>",
        "restart": "<path d=\"M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5\"/>",
        "power": "<path d=\"M12 2v10M18.36 6.64a9 9 0 1 1-12.73 0\"/>",
        "moon": "<path d=\"M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z\"/>",
        "sun": "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2M12 20v2M5 5l1 1M18 18l1 1M2 12h2M20 12h2M5 19l1-1M18 6l1-1\"/>",
        "update": "<path d=\"M4 9a8 8 0 0 1 14-3l2 3M20 4v5h-5M20 15a8 8 0 0 1-14 3l-2-3M4 20v-5h5\"/>",
        "install": "<rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"3\"/><path d=\"M12 8v8M8 12h8\"/>",
        "camera": "<path d=\"M3 7h4l2-3h6l2 3h4v13H3z\"/><circle cx=\"12\" cy=\"13\" r=\"4\"/>",
        "record": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"13\" rx=\"2\"/><circle cx=\"12\" cy=\"10.5\" r=\"3\"/><path d=\"M8 21h8M12 17v4\"/>",
        "copy": "<rect x=\"8\" y=\"8\" width=\"12\" height=\"13\" rx=\"2\"/><path d=\"M16 8V3H3v13h5\"/>",
        "web": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><ellipse cx=\"12\" cy=\"12\" rx=\"4\" ry=\"9\"/><path d=\"M3 12h18M5 7h14M5 17h14\"/>",
        "note": "<path d=\"M5 3h14v14l-4 4H5zM15 21v-4h4M8 8h8M8 12h8\"/>",
        "warning": "<path d=\"m12 3 10 18H2zM12 9v5M12 17h.01\"/>",
        "key": "<circle cx=\"8\" cy=\"9\" r=\"5\"/><path d=\"m12 13 8 8M16 17l3-3M18 19l3-3\"/>"
};
    const path = paths[aliases[name] || name];
    if (!path)
        return "";
    const stroke = svgFill(ink).replace('fill=', 'stroke=').replace('fill-opacity=', 'stroke-opacity=');
    const painted = path.replace(/fill="#fffaf5"/g, svgFill(ink));
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' + stroke + ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + painted + '</svg>');
}
