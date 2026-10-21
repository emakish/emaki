// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// The privacy pill's screen half means one thing: another program is seeing the screen
// right now. These pure functions decide which compositor casts say so and name them.

// casts: the casts the core reports. readers: PipeWire streams reading a node,
// [{source: node id, name, binary}]. ownNodes: nodes the shell's own recorder streams to.
// lookup(word) gives a program's display name for a process or application name, or "".
function shares(casts, readers, ownNodes, lookup) {
    const own = new Set((ownNodes || []).filter(n => n !== null && n !== undefined && n >= 0));
    const rows = [];
    for (const c of casts || []) {
        // Paused or idle sessions, a cast with nothing to show, and the shell's own
        // captures (its glass, its recording) are not someone else seeing the screen.
        if (!c || !c.is_active || c.target === "nothing" || c.by_parent)
            continue;
        const node = c.pw_node_id ?? null;
        if (node !== null && own.has(node))
            continue;
        rows.push({
            key: c.stream_id,
            name: programName(c, readers, lookup),
            window: c.target === "window"
        });
    }
    return rows;
}

// The program behind a cast: the screencopy client's process (from the core), or the
// stream reading the cast's PipeWire node (a portal or D-Bus screencast has no PID).
function programName(c, readers, lookup) {
    const node = c.pw_node_id ?? null;
    const words = [];
    if (c.client)
        words.push(c.client);
    if (node !== null)
        for (const r of readers || [])
            if (r && r.source === node)
                words.push(r.binary, r.name);
    for (const word of words) {
        const known = word ? lookup(word) : "";
        if (known)
            return known;
    }
    return words.find(word => !!word) || qsTranslate("Shell", "An app");
}

// PipeWire node ids that casts stream to: a video stream reading one of them is that
// program watching the screen, not a camera.
function castNodes(casts) {
    return (casts || []).map(c => c ? c.pw_node_id ?? null : null).filter(n => n !== null);
}
