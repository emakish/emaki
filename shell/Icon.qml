pragma ComponentBehavior: Bound
import QtQuick

// SVG geometry copied from the supplied mockup; no icon theme or network lookup. The power
// modes (power-saver, balanced, performance: the leaf, see-saw and gauge of Adwaita's symbols,
// redrawn in these strokes) are the shell's own, 27.09.2026; lock/sleep/restart/shutdown are the
// mockup's (docs/mockups/shell/app.js P.lock, P.moon, P.restart, P.off).
Image {
    id: icon
    required property string kind
    property real charge: .82
    property color ink: ShellPalette.text
    width: 16
    height: 16
    sourceSize.width: Math.ceil(width * 2)
    sourceSize.height: Math.ceil(height * 2)
    readonly property var paths: ({
            // The Emaki mark (art/logo/mark.py, 27.09): a slanted E of parallelograms in
            // its own "Hot" gradient along the slant (mark-large.svg), not the ink — also in the
            // launcher button.
            logo: '<defs><linearGradient id="hot" gradientUnits="userSpaceOnUse" x1="3" y1="24" x2="21" y2="0"><stop offset="0" stop-color="#b01e78"/><stop offset="0.3" stop-color="#ec2a55"/><stop offset="0.62" stop-color="#ff6a2a"/><stop offset="1" stop-color="#ffb62e"/></linearGradient></defs><path fill="url(#hot)" d="M3 22L7.161 22L12.896 2L8.735 2ZM5.735 4L20 4L24 0L9.735 0ZM2.867 14L17.133 14L21.133 10L6.867 10ZM0 24L14.265 24L18.265 20L4 20Z"/>',
            corner: '<path d="M1.25 26V13.25a12 12 0 0 1 12-12H26"/>',
            close: '<path d="m6 6 12 12M18 6 6 18"/>',
            tray: '<path d="m6 15 6-6 6 6"/>',
            file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
            wifi: '<path d="M1.42 9a16 16 0 0 1 21.16 0M5 12.55a11 11 0 0 1 14.08 0M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"/>',
            // A cable connection: one box above two, as Adwaita's network-wired (2026-10-05).
            wired: '<rect x="8.5" y="2" width="7" height="6" rx="1.5"/><rect x="1.5" y="16" width="7" height="6" rx="1.5"/><rect x="15.5" y="16" width="7" height="6" rx="1.5"/><path d="M12 8v4M5 16v-4h14v4"/>',
            bt: '<path d="m7 7 10 10-5 5V2l5 5L7 17"/>',
            sound: '<path d="M11 5 6 9H2v6h4l5 4zM15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>',
            muted: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="m22 9-6 6M16 9l6 6"/>',
            mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v4"/>',
            cam: '<path d="M23 7l-7 5 7 5z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
            cast: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
            light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
            "power-saver": '<path d="M4 20h8a8 8 0 0 0 8-8V4h-8a8 8 0 0 0-8 8z"/><path d="M4 20 14 10"/>',
            balanced: '<path d="M3 14h18M12 14l-4 6h8z"/><path d="M6.5 4 10 7.5 6.5 11 3 7.5z"/><circle cx="17.5" cy="7.5" r="3.5"/>',
            performance: '<path d="M4.52 19A9 9 0 1 1 19.48 19"/><path d="M13.2 12.8 16.5 9.5"/><circle cx="12" cy="14" r="1.5"/>',
            lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
            sleep: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
            restart: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
            logout: '<path d="M10 3H4v18h6M10 12h11M17 8l4 4-4 4"/>',
            hibernate: '<path d="M18 9A7 7 0 1 1 10 2a5 5 0 0 0 8 7M5 22h14"/>',
            shutdown: '<path d="M12 2v10"/><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/>',
            battery: '<rect x="2" y="7" width="17" height="10" rx="2.5"/><path d="M22 11v2"/><rect x="4.5" y="9.5" width="' + (12 * Math.max(0, Math.min(1, charge))) + '" height="5" rx="1" fill="currentColor" stroke="none"/>'
        })
    // Center the bounds of the painted strokes, preserving their scale and shape.
    // Wi-Fi ink: y ~= 4..21; corner ink: x/y = 0..27.25 with round caps.
    // Other paths already pass the painted-bounds test; logo fills its 24 square.
    readonly property string inkViewBox: kind === "corner" ? "-4.375 -4.375 36 36" : kind === "wifi" ? "0 0.5 24 24" : kind === "logout" ? "0.5 0 24 24" : kind === "hibernate" ? "-0.5 0 24 24" : "0 0 24 24"
    source: 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + inkViewBox + '" color="' + ink + '" fill="' + (kind === "logo" ? 'currentColor' : 'none') + '" stroke="' + (kind === "logo" ? 'none' : 'currentColor') + '" stroke-width="' + (kind === "corner" ? '2.5' : '2') + '" stroke-linecap="round" stroke-linejoin="round">' + (paths[kind] || '') + '</svg>')
}
