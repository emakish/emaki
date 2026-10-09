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
        if (item.activeFocusOnTab && item.width > 0 && item.height > 0) {
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

function focusFirst(root) {
    const list = targets(root);
    const target = list.length ? list[0] : root;
    target.forceActiveFocus(Qt.TabFocusReason);
    reveal(target);
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
    list[next].forceActiveFocus(step < 0 ? Qt.BacktabFocusReason : Qt.TabFocusReason);
    reveal(list[next]);
    return true;
}

function handle(root, event) {
    let step = 0;
    if (event.key === Qt.Key_Tab)
        step = event.modifiers & Qt.ShiftModifier ? -1 : 1;
    else if (event.key === Qt.Key_Backtab || event.key === Qt.Key_Left || event.key === Qt.Key_Up)
        step = -1;
    else if (event.key === Qt.Key_Right || event.key === Qt.Key_Down)
        step = 1;
    if (step) {
        move(root, step);
        event.accepted = true;
        return true;
    }
    return false;
}
