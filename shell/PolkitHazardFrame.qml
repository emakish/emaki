// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Canvas {
    id: tape
    property bool reducedMotion: false
    property color critical: ShellPalette.critical
    property color hazard: LiquidPalette.polkitHazard
    onHazardChanged: requestPaint()
    property real phase: 0
    onPhaseChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onCriticalChanged: requestPaint()
    NumberAnimation on phase {
        from: 0
        to: 20
        duration: 550
        loops: Animation.Infinite
        running: tape.visible && !tape.reducedMotion
    }
    onPaint: {
        const ctx = getContext("2d");
        ctx.reset();
        const w = width, h = height;
        function rounded(inset, radius) {
            const r = Math.min(radius, (h - 2 * inset) / 2, (w - 2 * inset) / 2);
            ctx.beginPath();
            ctx.moveTo(inset + r, inset);
            ctx.arcTo(w - inset, inset, w - inset, h - inset, r);
            ctx.arcTo(w - inset, h - inset, inset, h - inset, r);
            ctx.arcTo(inset, h - inset, inset, inset, r);
            ctx.arcTo(inset, inset, w - inset, inset, r);
            ctx.closePath();
        }
        rounded(0, 30);
        ctx.fillStyle = tape.hazard;
        ctx.fill();
        rounded(1.5, 28.5);
        ctx.clip();
        // Clockwise axes extend beneath the opaque card to fill the rounded corners.
        function edge(length, depth, start, end) {
            ctx.save();
            ctx.beginPath();
            ctx.rect(start, 0, end - start, depth);
            ctx.clip();
            ctx.fillStyle = tape.hazard;
            ctx.fillRect(0, 0, length, depth);
            ctx.fillStyle = tape.critical;
            for (let x = -depth - 20 + tape.phase; x < length + 20; x += 20) {
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x + 10, 0);
                ctx.lineTo(x + 10 + depth, depth);
                ctx.lineTo(x + depth, depth);
                ctx.closePath();
                ctx.fill();
            }
            ctx.restore();
        }
        edge(w, h / 2, 0, w);
        ctx.save();
        ctx.translate(w, h);
        ctx.rotate(Math.PI);
        edge(w, h / 2, 0, w);
        ctx.restore();
        ctx.save();
        ctx.translate(w, 0);
        ctx.rotate(Math.PI / 2);
        edge(h, 26, 16, h - 16);
        ctx.restore();
        ctx.save();
        ctx.translate(0, h);
        ctx.rotate(-Math.PI / 2);
        edge(h, 26, 16, h - 16);
        ctx.restore();
        rounded(8.5, 21.5);
        ctx.fillStyle = tape.hazard;
        ctx.fill();
    }
}
