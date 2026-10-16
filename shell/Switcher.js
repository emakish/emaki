// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// The window switcher (Alt+Tab): which windows it lists, in which order, where the selection
// goes, and which windows can show a live picture cut out of the screen. No QML here, so the
// rules are tested on their own (tests/test-switcher-logic.mjs).

// Qt.KeyboardModifier flags. Shift alone never keeps the switcher open: Alt+Shift+Tab is
// released Shift first as often as not.
const ALT = 0x08000000;
const CONTROL = 0x04000000;
const META = 0x10000000;

// Whether a chord's modifier is still held. -1 (unknown: the Quickshell in use cannot tell)
// is "not held", so the switcher falls back to switching at once, as Alt+Tab did before it.
function held(modifiers) {
    return Number.isInteger(modifiers) && modifiers > 0 && (modifiers & (ALT | CONTROL | META)) !== 0;
}

// Most recent first: the focused window, then by the time each last held the focus, then the
// newest window. focusTimes maps a window ID to milliseconds; a window never focused has none.
// focusedId (when 0 or more) names the focused window instead of niri's is_focused: the window
// the switcher has just asked niri to focus, before niri reports it.
function focusedIn(w, focusedId) {
    return focusedId !== undefined && focusedId >= 0 ? w.id === focusedId : !!w.is_focused;
}

function order(windows, focusTimes, focusedId) {
    const times = focusTimes || {};
    const time = w => (Number.isFinite(times[w.id]) ? times[w.id] : -Infinity);
    return windows.slice().sort((a, b) => {
        const fa = focusedIn(a, focusedId), fb = focusedIn(b, focusedId);
        if (fa !== fb)
            return fa ? -1 : 1;
        const ta = time(a), tb = time(b);
        if (ta !== tb)
            return tb > ta ? 1 : -1;
        return b.id - a.id;
    });
}

// The rows the switcher shows, frozen when it opens. `labels` maps an ID to {title, appId}
// (the private label channel); without it a row has only its number. appOnly keeps the windows
// of the focused window's application (Alt+`); with no focused application it lists them all.
function entries(windows, labels, focusTimes, appOnly, focusedId) {
    const names = labels || {};
    let list = order(windows.filter(w => Number.isSafeInteger(w.id)), focusTimes, focusedId);
    if (appOnly) {
        const focused = list.find(w => focusedIn(w, focusedId));
        const app = focused ? (names[focused.id] || {}).appId || "" : "";
        if (app)
            list = list.filter(w => ((names[w.id] || {}).appId || "") === app);
    }
    return list.map(w => {
        const label = names[w.id] || {};
        return {
            id: w.id,
            appId: String(label.appId || ""),
            title: String(label.title || "") || "Window #" + w.id,
            focused: focusedIn(w, focusedId),
            urgent: !!w.is_urgent,
            workspaceId: w.workspace_id === undefined ? null : w.workspace_id
        };
    });
}

// The first selection: the window before the focused one (next) or the oldest (previous). A
// list whose first row is not focused (focus on a panel or nothing) starts on that first row.
function start(list, delta) {
    const count = list.length;
    if (count === 0)
        return -1;
    const offset = list[0].focused ? 1 : 0;
    if (count <= offset)
        return 0;
    return delta < 0 ? count - 1 : offset;
}

function step(index, count, delta) {
    if (count <= 0)
        return -1;
    return ((Math.max(0, index) + delta) % count + count) % count;
}

function overlaps(a, b) {
    return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

// Where a window's tile is in its workspace view: niri's own position (floating windows, and a
// fullscreen tile under the shell's inset marker), else Emaki's niri's position of a tiled
// window (`emaki_tile_pos_in_view`, collected by WindowLabels; stock niri has none).
function windowRect(w, positions) {
    const layout = w.layout;
    if (!positions || positions[w.id] === undefined || positions[w.id] === false)
        return null;
    const tile = (layout && layout.tile_pos_in_workspace_view) || (positions && positions[w.id]);
    const offset = layout && layout.window_offset_in_tile;
    const size = layout && layout.window_size;
    if (!tile || !offset || !size)
        return null;
    const rect = {
        x: tile[0] + offset[0],
        y: tile[1] + offset[1],
        width: size[0],
        height: size[1]
    };
    return [rect.x, rect.y, rect.width, rect.height].every(Number.isFinite) && rect.width >= 1 && rect.height >= 1 ? rect : null;
}

// Windows whose pixels are on this output's screen right now, so a capture of the output
// shows them: on the output's active workspace, wholly inside the screen, covered by no
// floating window (one lies above the tiles; the order among floating windows is unknown), and
// for tiles sharing a place (the tabs of a column) only the workspace's active window, the one
// drawn. Not while the overview zooms the workspaces out.
// Returns {id: {x, y, width, height}} in the output's logical pixels.
function visible(windows, workspaces, outputName, width, height, overviewOpen, positions) {
    const result = {};
    if (overviewOpen || !outputName || !(width > 0) || !(height > 0))
        return result;
    const active = workspaces.find(w => w.output === outputName && w.is_active);
    if (!active)
        return result;
    // A transition can move an occluder as well as the requested tile. Keep the previous
    // picture (or icon) for this opening until a fresh, settled opening is requested.
    if (windows.some(w => w.workspace_id === active.id
            && (!positions || positions[w.id] === undefined || positions[w.id] === false)))
        return result;
    const shown = windows.filter(w => w.workspace_id === active.id).map(w => ({
        window: w,
        rect: windowRect(w, positions)
    })).filter(item => item.rect !== null);
    for (const item of shown) {
        const r = item.rect;
        if (r.x < -.5 || r.y < -.5 || r.x + r.width > width + .5 || r.y + r.height > height + .5)
            continue;
        const covered = shown.some(other => other !== item && overlaps(r, other.rect) && (other.window.is_floating || (!item.window.is_floating && item.window.id !== active.active_window_id)));
        if (!covered)
            result[item.window.id] = r;
    }
    return result;
}

// The rows and columns of cards for `count` windows inside the space a screen leaves.
function grid(count, cellWidth, cellHeight, spaceWidth, spaceHeight) {
    const columns = Math.max(1, Math.min(count, Math.floor(spaceWidth / cellWidth)));
    const rowsWanted = Math.max(1, Math.ceil(count / columns));
    const rows = Math.max(1, Math.min(rowsWanted, Math.floor(spaceHeight / cellHeight)));
    return {
        columns: columns,
        rows: rows,
        scrolls: rowsWanted > rows
    };
}
