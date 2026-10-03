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
