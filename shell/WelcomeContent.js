.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// The first-run tour: one idea per page. Every key shown here names the bindings it stands for,
// and tests/test-welcome.py checks each of them against niri/default.kdl.
var pages = [
    {
        id: "welcome",
        title: "Welcome to Emaki",
        text: "Emaki is a ready-to-use desktop where your windows sit side by side on one long strip.",
        note: "This short tour shows the basics. It takes about a minute.",
        keys: []
    },
    {
        id: "super",
        title: "Meet the Super key",
        text: "Most shortcuts start with Super. It is the Windows key on a PC keyboard and Command on a Mac keyboard.",
        note: "",
        keyboards: [
            { kind: "pc", keyboard: "PC keyboard", key: "Windows key" },
            { kind: "mac", keyboard: "Mac keyboard", key: "Command" }
        ],
        keys: []
    },
    {
        id: "move",
        title: "Move around",
        text: "Windows line up from left to right. Workspaces stack above and below, one for each task.",
        note: "On a trackpad, swipe sideways with three fingers to move along the strip and up or down to change workspace. Swipe up with four fingers, or touch the top-left screen corner, for the overview.",
        keys: [
            { keys: ["Super", "←", "→"], label: "Windows", binds: { "Mod+Left": "focus-column-left", "Mod+Right": "focus-column-right" } },
            { keys: ["Super", "↑", "↓"], label: "Workspaces", binds: { "Mod+Up": "focus-window-or-workspace-up", "Mod+Down": "focus-window-or-workspace-down" } },
            { keys: ["Super", "O"], label: "Overview", binds: { "Mod+O": "toggle-overview" } }
        ]
    },
    {
        id: "system",
        title: "Everything has its place",
        text: "Install programs in one place and change everything else in Settings.",
        note: "Open the launcher with Super + D or the Emaki button at the top left, then type a name.",
        places: [
            { kind: "programs", name: "Programs & Updates", what: "Install programs and keep the whole system up to date.", where: "In the dock and the launcher" },
            { kind: "settings", name: "Settings", what: "Wi-Fi, displays, sound, the look and everything else.", where: "The gear in the launcher" },
            { kind: "report", name: "Report a problem", what: "If something breaks, describe it and see what is sent first.", where: "The launcher, or Settings › About" }
        ],
        keys: [],
        binds: { "Mod+D": "spawn \"emaki-shell\" \"call\" \"launcher\" \"toggle\"" }
    },
    {
        id: "keys",
        title: "Shortcuts worth knowing",
        text: "A few to start with. Press Super + / at any time to search the full list.",
        note: "Shortcuts you set yourself take priority over these. Open this tour again from the launcher or with Super + Ctrl + H.",
        shortcuts: [
            { keys: ["Super", "Enter"], label: "Terminal", binds: { "Mod+Return": "spawn \"emaki-terminal\"" } },
            { keys: ["Super", "E"], label: "Files", binds: { "Mod+E": "spawn" } },
            { keys: ["Super", "D"], label: "Launcher", binds: { "Mod+D": "spawn \"emaki-shell\" \"call\" \"launcher\" \"toggle\"" } },
            { keys: ["Super", "Q"], label: "Close window", binds: { "Mod+Q": "close-window" } },
            { keys: ["Super", "F"], label: "Full screen", binds: { "Mod+F": "fullscreen-window" } },
            { keys: ["Super", "R"], label: "Window width", binds: { "Mod+R": "switch-preset-column-width" } },
            { keys: ["Super", "L"], label: "Lock screen", binds: { "Mod+L": "spawn \"emaki-lock\"" } },
            { keys: ["Super", "/"], label: "All shortcuts", binds: { "Mod+Slash": "spawn \"emaki-shell\" \"call\" \"keyboard\" \"open\" \"shortcuts\"" } }
        ],
        keys: [],
        binds: { "Mod+Ctrl+H": "spawn \"emaki-shell\" \"call\" \"welcome\" \"present\"" }
    }
];

// Every binding a page names, for the contract test.
function binds(page) {
    const found = Object.assign({}, page.binds || {});
    for (const row of (page.keys || []).concat(page.shortcuts || []))
        Object.assign(found, row.binds);
    return found;
}
