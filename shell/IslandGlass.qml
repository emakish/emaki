pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// The glass of the bar islands and the launcher: GlassShape with material() of
// docs/mockups/liquid-glass/launcher.js. The plate is Regular (the index.html column,
// underlay .5), the drops take the dock bubble's optics and read as glass by the light on
// their rim, not by a tint (uDropDome). One place for these values: the left islands and
// the launcher panel both draw through it.
// `plate` and `drops` are in the owner's scene coordinates; the item covers them plus the
// shadow and flow margin and sits at bounds − sceneOffset in its parent.
GlassShape {
    id: glass
    property rect plate: Qt.rect(0, 0, 0, 0)
    property point sceneOffset: Qt.point(0, 0)
    readonly property int pad: Liquid.glassPad()
    readonly property rect bounds: {
        let x0 = plate.x, y0 = plate.y, x1 = plate.x + plate.width, y1 = plate.y + plate.height;
        for (const d of drops) {
            x0 = Math.min(x0, d.rect.x);
            y0 = Math.min(y0, d.rect.y);
            x1 = Math.max(x1, d.rect.x + d.rect.z);
            y1 = Math.max(y1, d.rect.y + d.rect.w);
        }
        const x = Math.floor(x0 - pad), y = Math.floor(y0 - pad);
        return Qt.rect(x, y, Math.ceil(x1 + pad) - x, Math.ceil(y1 + pad) - y);
    }
    x: bounds.x - sceneOffset.x
    y: bounds.y - sceneOffset.y
    width: bounds.width
    height: bounds.height
    uOrigin: Qt.point(bounds.x, bounds.y)
    uRect: Qt.vector4d(plate.x, plate.y, plate.width, plate.height)
    uRadius: Metrics.islandRadius
    uRegularBlur: 1
    uRegularContrast: .69
    uRegularSaturation: 1.5
    uRegularDock: .5
    uEdge: Liquid.BUBBLE.edge
    uThickness: Liquid.BUBBLE.thickness
    uRefraction: Liquid.BUBBLE.refraction
    uDispersion: Liquid.BUBBLE.dispersion
    uRimLight: Liquid.BUBBLE.rimLight
    uRimLightWidth: Liquid.BUBBLE.rimWidth
    uBulge: Qt.vector4d(Liquid.BUBBLE.restUnion, 0, 1, 0)
    uIsDock: 2
    uHasIcons: 1
    uDropDome: 1
    // The drops see what the plate sees (a drop may stand a few px proud of its plate).
    uLiveTop: 100000
}
