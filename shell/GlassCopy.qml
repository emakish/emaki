pragma ComponentBehavior: Bound
import QtQuick

// A GlassTarget drawn once more on the glass while a drop covers it (dual() in the liquid-glass
// mockups): its own pixels (ShaderEffectSource draws the source without its own opacity, so
// the copy shows it whole with `alpha` while the original fades under the glass), on a
// rectangle snapped to device pixels so text is not resampled at 1.25. `space` is the item the
// copy's parent shares its origin with (a layer at the screen or bar origin); bump `tick` to
// re-read where the target is.
ShaderEffectSource {
    id: copy
    property Item target: null
    property Item space: null
    property real alpha: 0
    property int tick: 0
    readonly property real dpr: Screen.devicePixelRatio || 1
    readonly property rect r: {
        tick;
        if (!target || !space)
            return Qt.rect(0, 0, 0, 0);
        const p = target.mapToItem(space, 0, 0);
        return Qt.rect(p.x, p.y, target.width, target.height);
    }
    readonly property real sx: Math.floor(r.x * dpr) / dpr
    readonly property real sy: Math.floor(r.y * dpr) / dpr
    readonly property real sw: Math.ceil((r.x + r.width) * dpr) / dpr - sx
    readonly property real sh: Math.ceil((r.y + r.height) * dpr) / dpr - sy
    sourceItem: target
    sourceRect: Qt.rect(sx - r.x, sy - r.y, sw, sh)
    x: sx
    y: sy
    width: sw
    height: sh
    textureSize: Qt.size(Math.max(1, Math.round(sw * dpr)), Math.max(1, Math.round(sh * dpr)))
    live: true
    hideSource: false
    opacity: alpha
    visible: target !== null && alpha > .001 && sw > 0 && sh > 0
}
