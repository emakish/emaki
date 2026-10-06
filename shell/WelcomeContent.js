.pragma library

// Keyboard bindings are checked against niri/default.kdl by test-welcome.py.
var pages = [
    {
        title: "A little space to get comfortable",
        tab: "Start here",
        description: "Your windows sit beside each other on a horizontal strip. Open an app, then move along the strip when you need another window.",
        diagram: "Windows continue to the left and right",
        actions: [
            { title: "Open an app", pointer: "Click the Emaki button at the top left, then choose an app.", keys: "Super + D", keyboard: "Type an app name, then press Enter.", binds: { "Mod+D": "spawn" } },
            { title: "Find another window", pointer: "Click its icon in the dock at the bottom. On a trackpad, swipe sideways with three fingers.", keys: "Super + ← / →", keyboard: "Move to the column on either side.", binds: { "Mod+Left": "focus-column-left", "Mod+Right": "focus-column-right" } }
        ]
    },
    {
        title: "See the bigger picture",
        tab: "Move around",
        description: "A workspace is another strip for another task. Overview lets you see your windows and workspaces together, so you can choose where to go.",
        diagram: "Workspaces sit above and below each other",
        actions: [
            { title: "Look around in overview", pointer: "Move into the very top-left screen corner, or swipe up with four fingers. Click a window to return.", keys: "Super + O", keyboard: "Open or close overview.", binds: { "Mod+O": "toggle-overview" } },
            { title: "Change workspace", pointer: "Click a workspace number in the top bar, or swipe up or down with three fingers.", keys: "Super + 1 … 9 / 0", keyboard: "Go to a workspace by number. 0 opens workspace 10.", binds: { "Mod+1": "focus-workspace 1", "Mod+2": "focus-workspace 2", "Mod+3": "focus-workspace 3", "Mod+4": "focus-workspace 4", "Mod+5": "focus-workspace 5", "Mod+6": "focus-workspace 6", "Mod+7": "focus-workspace 7", "Mod+8": "focus-workspace 8", "Mod+9": "focus-workspace 9", "Mod+0": "focus-workspace 10" } }
        ]
    },
    {
        title: "Make room for your work",
        tab: "Arrange windows",
        description: "New windows start at full width. Make one narrower to work beside another, and move windows to keep related work together.",
        diagram: "Narrower windows can share the view",
        actions: [
            { title: "Move a window", pointer: "In overview, drag a window beside another or onto a different workspace.", keys: "Super + Shift + ← / →", keyboard: "Move the column left or right.", binds: { "Mod+Shift+Left": "move-column-left", "Mod+Shift+Right": "move-column-right" } },
            { title: "Change its width", pointer: "Hold Super and drag with the right mouse button. On a trackpad, use a secondary-click drag.", keys: "Super + R", keyboard: "Cycle through one third, one half, two thirds and full width.", binds: { "Mod+R": "switch-preset-column-width" } }
        ]
    }
];
