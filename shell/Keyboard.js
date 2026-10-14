// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// A boundary is an open surface. Walk visual children so rebuilt lists and disabled
// controls participate immediately, without a second, stale list of controls.
function boundary(item) {
    for (let parent = item; parent; parent = parent.parent) {
        if (parent.keyboardBoundary === true)
            return parent;
    }
    return null;
}

function targets(root) {
    const result = [];
    function visit(item) {
        if (!item || !item.visible || !item.enabled)
            return;
        if (item !== root && item.keyboardBoundary === true)
            return;
        if (item.activeFocusOnTab && reachable(item, root)) {
            if (item.keyboardFirst === true)
                result.unshift(item);
            else
                result.push(item);
        }
        for (const child of item.children || [])
            visit(child);
    }
    visit(root);
    return result;
}

// A scroll view can reveal its children. A plain clipping item cannot: do not
// send focus behind its edge, even when the child's visible property is true.
function reachable(item, root) {
    if (item.width <= 0 || item.height <= 0)
        return false;
    let visibleItem = item;
    for (let parent = item.parent; parent; parent = parent.parent) {
        if (!parent.visible || !parent.enabled)
            return false;
        if (parent.clip) {
            const rect = visibleItem.mapToItem(parent, 0, 0, visibleItem.width, visibleItem.height);
            const scrolls = parent.contentItem !== undefined && parent.contentY !== undefined;
            if (!scrolls && (rect.x + rect.width <= 0 || rect.y + rect.height <= 0
                    || rect.x >= parent.width || rect.y >= parent.height))
                return false;
            // Outer clipping applies to the scroll viewport, not to a child's
            // current offscreen position inside that viewport.
            if (scrolls)
                visibleItem = parent;
        }
        if (parent === root)
            break;
    }
    return true;
}

function active(root) {
    if (!root)
        return null;
    for (const child of root.children || []) {
        const found = active(child);
        if (found)
            return found;
    }
    return root.activeFocus ? root : null;
}

function reveal(item) {
    if (!item)
        return;
    for (let parent = item.parent; parent; parent = parent.parent) {
        if (parent.contentItem !== undefined && parent.contentY !== undefined
                && parent.contentHeight !== undefined) {
            const rect = item.mapToItem(parent.contentItem, 0, 0, item.width, item.height);
            const bottom = rect.y + rect.height;
            let next = parent.contentY;
            if (rect.y < next)
                next = rect.y;
            else if (bottom > next + parent.height)
                next = bottom - parent.height;
            parent.contentY = Math.max(0, Math.min(next, parent.contentHeight - parent.height));
            if (parent.contentX !== undefined && parent.contentWidth > parent.width) {
                let x = parent.contentX;
                if (rect.x < x)
                    x = rect.x;
                else if (rect.x + rect.width > x + parent.width)
                    x = rect.x + rect.width - parent.width;
                parent.contentX = Math.max(0, Math.min(x, parent.contentWidth - parent.width));
            }
        }
    }
}

// What a target is called: its label, else its key (system cells), for status reports.
function name(item) {
    return item ? item.label || item.key || "" : "";
}

function focusFirst(root) {
    const list = targets(root);
    const target = list.length ? list[0] : root;
    reveal(target);
    target.forceActiveFocus(Qt.TabFocusReason);
    return target;
}

function move(root, step) {
    if (!root)
        return false;
    if (root.keyboardMode !== undefined)
        root.keyboardMode = true;
    const list = targets(root);
    if (!list.length) {
        root.forceActiveFocus();
        return true;
    }
    const index = list.findIndex(item => item.activeFocus);
    const next = index < 0 ? (step < 0 ? list.length - 1 : 0)
        : (index + step + list.length) % list.length;
    reveal(list[next]);
    list[next].forceActiveFocus(step < 0 ? Qt.BacktabFocusReason : Qt.TabFocusReason);
    return true;
}

function moveDirection(root, key) {
    if (!root)
        return false;
    if (root.keyboardMode !== undefined)
        root.keyboardMode = true;
    const list = targets(root);
    const current = list.find(item => item.activeFocus);
    if (!current) {
        focusFirst(root);
        return true;
    }
    const horizontal = key === Qt.Key_Left || key === Qt.Key_Right;
    const sign = key === Qt.Key_Left || key === Qt.Key_Up ? -1 : 1;
    const from = current.mapToItem(root, 0, 0, current.width, current.height);
    const main = horizontal ? from.x + from.width / 2 : from.y + from.height / 2;
    const crossStart = horizontal ? from.y : from.x;
    const crossEnd = crossStart + (horizontal ? from.height : from.width);
    let next = null;
    let best = Infinity;
    for (const candidate of list) {
        if (candidate === current)
            continue;
        const rect = candidate.mapToItem(root, 0, 0, candidate.width, candidate.height);
        const distance = sign * ((horizontal ? rect.x + rect.width / 2 : rect.y + rect.height / 2) - main);
        if (distance <= 0.5)
            continue;
        const start = horizontal ? rect.y : rect.x;
        const end = start + (horizontal ? rect.height : rect.width);
        const gap = Math.max(0, start - crossEnd, crossStart - end);
        const offset = Math.abs((start + end - crossStart - crossEnd) / 2);
        // Prefer the same row/column, then the nearest diagonal. Tree order is
        // only a tie breaker; it has no relation to an arrow's direction.
        const score = (gap > 0 ? 1000000 : 0) + distance + gap * 4 + offset * 0.25;
        if (score < best) {
            next = candidate;
            best = score;
        }
    }
    if (next) {
        reveal(next);
        next.forceActiveFocus(Qt.OtherFocusReason);
    }
    return true;
}

function handle(root, event) {
    let step = 0;
    if (event.key === Qt.Key_Tab)
        step = event.modifiers & Qt.ShiftModifier ? -1 : 1;
    else if (event.key === Qt.Key_Backtab)
        step = -1;
    else if ([Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down].includes(event.key)) {
        event.accepted = moveDirection(root, event.key);
        return event.accepted;
    }
    if (step) {
        event.accepted = move(root, step);
        return event.accepted;
    }
    return false;
}
