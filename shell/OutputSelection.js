.pragma library
// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

// Preserve explicit output selection. Otherwise prefer the laptop panel, including
// when an external display is enumerated first or outputs are hotplugged.
function select(screens, requested) {
    if (requested)
        return screens.find(screen => screen.name === requested) ?? null;
    return screens.find(screen => /^(eDP-|LVDS|DSI-)/.test(screen.name)) ?? screens[0] ?? null;
}
