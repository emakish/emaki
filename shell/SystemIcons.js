.pragma library
// Adwaita "*-symbolic" names for what the system island and its panel show
// (docs/mockups/liquid-glass/system.js cells() and buildView()), from plain values. The
// SymbolIcon that draws a name falls back to Icon.qml's own shapes when the theme lacks it.

// Signal strength 0..1 (0..100 accepted) → the Wi-Fi bars, GNOME's thresholds (80/55/30/5).
function signal(strength) {
    const s = Number(strength);
    const v = s > 1 ? s / 100 : s;
    const level = v > .8 ? "excellent" : v > .55 ? "good" : v > .3 ? "ok" : v > .05 ? "weak" : "none";
    return "network-wireless-signal-" + level + "-symbolic";
}
// system.js soundIcon(): muted or silent, above half, else medium.
function sound(ready, volume, muted) {
    if (!ready || muted || volume <= 0)
        return "audio-volume-muted-symbolic";
    return volume > .5 ? "audio-volume-high-symbolic" : "audio-volume-medium-symbolic";
}
// Battery by tens, with the charging variant; no battery: the power symbol (the cell is
// then the power menu only).
function battery(percent, charging) {
    if (percent < 0)
        return "system-shutdown-symbolic";
    if (charging && percent >= 100)
        return "battery-level-100-charged-symbolic";
    const level = Math.max(0, Math.min(100, Math.round(percent / 10) * 10));
    return "battery-level-" + level + (charging ? "-charging" : "") + "-symbolic";
}
// BlueZ's device icon ("audio-headphones", "input-keyboard", "phone") as a symbolic name.
function bluetoothDevice(icon) {
    return icon ? String(icon) + "-symbolic" : "bluetooth-active-symbolic";
}
// A PipeWire device's own icon name (device.icon_name: "audio-speakers",
// "audio-input-microphone", "audio-headphones") as a symbolic name.
function audioDevice(icon, isSink) {
    return icon ? String(icon) + "-symbolic" : isSink ? "audio-speakers-symbolic" : "audio-input-microphone-symbolic";
}

function isSettings(name) {
    return ["preferences-system", "preferences-system-symbolic", "emaki-settings", "org.emaki.Settings"].indexOf(String(name)) >= 0;
}

// The Settings gear: a pixel gear around a square hole, filled in one ink and with no tile
// behind it. The launcher draws it in the grey of its mode labels; elsewhere it takes the
// ink of the surface it sits on.
function settingsSvg(ink) {
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path ' + svgFill(ink) + ' fill-rule="evenodd" d="M10 4h4v3h3V6h2v4h1v4h-3v3h1v2h-4v1h-4v-3H7v1H5v-4H4v-4h3V7H6V5h4zM10 10v4h4v-4z"/></svg>');
}

// A QML colour with alpha prints as #AARRGGBB, while CSS colour syntax reads eight hex digits
// as #RRGGBBAA: give SVG the opaque colour and the alpha as fill-opacity, which both read the
// same way (the launcher's grey label ink is 55 % ink in light).
function svgFill(ink) {
    if (ink !== null && typeof ink === "object" && typeof ink.a === "number") {
        const hex = value => ("0" + Math.round(value * 255).toString(16)).slice(-2);
        return 'fill="#' + hex(ink.r) + hex(ink.g) + hex(ink.b) + '" fill-opacity="' + Number(ink.a.toFixed(3)) + '"';
    }
    return 'fill="' + ink + '"';
}

function programsSvg(base, strong, soft, shade, ink) {
    // The installed app icon's geometry, also used for its own Installed row.
    return 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 32 32" shape-rendering="crispEdges"><path fill="' + strong + '" d="M4 1h24v2h2v2h1v22h-2v2h-2v2H5v-2H3v-2H1V5h2V3h1z"/><path fill="' + base + '" d="M5 2h22v2h2v23h-2v2H5v-2H3V5h2z"/><path fill="' + soft + '" d="M5 2h22v2H5zM3 4h2v22H3z"/><path fill="' + shade + '" d="M9 16h14v2h2v9H7v-9h2z"/><path fill="' + ink + '" d="M8 15h5v2H9v7h14v-7h-4v-2h5v2h2v9H6v-9h2zM14 6h4v8h4v2h-2v2h-2v2h-4v-2h-2v-2h-2v-2h4z"/></svg>');
}
