.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// The first-run tour: one idea per page. Every key shown here names the bindings it stands for,
// and tests/test-welcome.py checks each of them against niri/default.kdl.
function pages() {
    return [
    {
        id: "welcome",
        title: qsTranslate("Shell", "Welcome to Emaki"),
        text: qsTranslate("Shell", "Emaki is a ready-to-use desktop where your windows sit side by side on one long strip."),
        note: qsTranslate("Shell", "This short tour shows the basics. It takes about a minute."),
        keys: []
    },
    {
        id: "super",
        title: qsTranslate("Shell", "Meet the Super key"),
        text: qsTranslate("Shell", "Most shortcuts start with Super. It is the Windows key on a PC keyboard and Command on a Mac keyboard."),
        note: "",
        keyboards: [
            { kind: "pc", keyboard: qsTranslate("Shell", "PC keyboard"), key: qsTranslate("Shell", "Windows key") },
            { kind: "mac", keyboard: qsTranslate("Shell", "Mac keyboard"), key: qsTranslate("KeyboardKey", "Command") }
        ],
        keys: []
    },
    {
        id: "move",
        title: qsTranslate("Shell", "Move around"),
        text: qsTranslate("Shell", "Windows line up from left to right. Workspaces stack above and below, one for each task."),
        note: qsTranslate("Shell", "On a trackpad, swipe sideways with three fingers to move along the strip and up or down to change workspace. Swipe up with four fingers, or touch the top-left screen corner, for the overview."),
        keys: [
            { keys: ["Super", "←", "→"], label: qsTranslate("Shell", "Windows"), binds: { "Mod+Left": "focus-column-left", "Mod+Right": "focus-column-right" } },
            { keys: ["Super", "↑", "↓"], label: qsTranslate("Shell", "Workspaces"), binds: { "Mod+Up": "focus-window-or-workspace-up", "Mod+Down": "focus-window-or-workspace-down" } },
            { keys: ["Super", "O"], label: qsTranslate("Shell", "Overview"), binds: { "Mod+O": "toggle-overview" } }
        ]
    },
    {
        id: "system",
        title: qsTranslate("Shell", "Everything has its place"),
        text: qsTranslate("Shell", "Install programs in one place and change everything else in Settings."),
        note: qsTranslate("Shell", "Open the launcher with Super + D or the Emaki button at the top left, then type a name."),
        places: [
            { kind: "programs", name: qsTranslate("Shell", "Programs & Updates"), what: qsTranslate("Shell", "Install programs and keep the whole system up to date."), where: qsTranslate("Shell", "In the dock and the launcher") },
            { kind: "settings", name: qsTranslate("Shell", "Settings"), what: qsTranslate("Shell", "Wi-Fi, displays, sound, the look and everything else."), where: qsTranslate("Shell", "The gear in the launcher") },
            { kind: "report", name: qsTranslate("Shell", "Report a problem"), what: qsTranslate("Shell", "If something breaks, describe it and see what is sent first."), where: qsTranslate("Shell", "The launcher, or Settings › About") }
        ],
        keys: [],
        binds: { "Mod+D": "spawn \"emaki-shell\" \"call\" \"launcher\" \"toggle\"" }
    },
    {
        id: "keys",
        title: qsTranslate("Shell", "Shortcuts worth knowing"),
        text: qsTranslate("Shell", "A few to start with. Press Super + / at any time to search the full list."),
        note: qsTranslate("Shell", "Shortcuts you set yourself take priority over these. Open this tour again from the launcher or with Super + Ctrl + H."),
        shortcuts: [
            { keys: ["Super", "Enter"], label: qsTranslate("Shell", "Terminal"), binds: { "Mod+Return": "spawn \"emaki-terminal\"" } },
            { keys: ["Super", "E"], label: qsTranslate("Shell", "Files"), binds: { "Mod+E": "spawn" } },
            { keys: ["Super", "D"], label: qsTranslate("Shell", "Launcher"), binds: { "Mod+D": "spawn \"emaki-shell\" \"call\" \"launcher\" \"toggle\"" } },
            { keys: ["Super", "Q"], label: qsTranslate("Shell", "Close window"), binds: { "Mod+Q": "close-window" } },
            { keys: ["Super", "F"], label: qsTranslate("Shell", "Full screen"), binds: { "Mod+F": "fullscreen-window" } },
            { keys: ["Super", "R"], label: qsTranslate("Shell", "Window width"), binds: { "Mod+R": "switch-preset-column-width" } },
            { keys: ["Super", "L"], label: qsTranslate("Shell", "Lock screen"), binds: { "Mod+L": "spawn \"emaki-lock\"" } },
            { keys: ["Super", "/"], label: qsTranslate("Shell", "All shortcuts"), binds: { "Mod+Slash": "spawn \"emaki-shell\" \"call\" \"keyboard\" \"open\" \"shortcuts\"" } }
        ],
        keys: [],
        binds: { "Mod+Ctrl+H": "spawn \"emaki-shell\" \"call\" \"welcome\" \"present\"" }
    }
    ];
}

// Every binding a page names, for the contract test.
function binds(page) {
    const found = Object.assign({}, page.binds || {});
    for (const row of (page.keys || []).concat(page.shortcuts || []))
        Object.assign(found, row.binds);
    return found;
}
