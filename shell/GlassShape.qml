pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// One draw of Emaki's liquid glass: shaders/dock.frag, the Qt port of the mockups'
// glass.frag, with the uniforms of material() in dock.js / launcher.js. The dock, the
// launcher and the left bar islands all draw through this one item: there is one glass,
// not one per island. Defaults are the dock's (the Clear preset overlaid by the
// Regular optics); each owner overrides what its mockup overrides.
//   backdrop     a DockBackdrop: what lies under the glass and its two blurred copies
//   wallBackdrop the wallpaper-only copy the dock bubble bends past the plate
//   uIcons       content drawn under the glass (premultiplied ShaderEffectSource)
//   drops        up to eight liquid members: [{rect: vector4d, params: vector4d, selected?}]
// Coordinates: uOrigin/uRect/drops are in the owner's scene, uCover maps that scene onto
// the backdrop textures, uSceneSize is the size of the uIcons texture in the same scene.
ShaderEffect {
    id: glass
    property DockBackdrop backdrop: null
    property DockBackdrop wallBackdrop: backdrop
    property var drops: []
    readonly property vector4d none: Qt.vector4d(0, 0, 0, 0)
    // A custom sharp source must override its UV rectangle/bounds together: the
    // backdrop may provide a full image whose region is selected by these uniforms.
    property Item uSharp: backdrop ? backdrop.sharp : null
    property vector4d uSharpRect: backdrop ? backdrop.sharpRect : Qt.vector4d(0, 0, 1, 1)
    property vector4d uSharpBounds: backdrop ? backdrop.sharpBounds : Qt.vector4d(0, 0, 1, 1)
    property vector4d uWallSharpRect: wallBackdrop ? wallBackdrop.sharpRect : Qt.vector4d(0, 0, 1, 1)
    property vector4d uWallSharpBounds: wallBackdrop ? wallBackdrop.sharpBounds : Qt.vector4d(0, 0, 1, 1)
    property Item uBlurred: backdrop ? backdrop.clear : null
    property Item uRegularLow: backdrop ? backdrop.regular : null
    property Item uRegularHigh: uRegularLow
    property Item uIcons: null
    // Optional live content, kept outside the static panel's content texture. Its
    // screen rectangle includes transparent padding, so edge filtering is unchanged.
    property Item uDynamicIcons: uIcons
    property real uHasDynamicIcons: 0
    property vector4d uDynamicRect: Qt.vector4d(0, 0, 1, 1)
    property Item uWallSharp: wallBackdrop ? wallBackdrop.sharp : null
    property Item uWallBlurred: wallBackdrop ? wallBackdrop.clear : null
    // Sampled only while uHasB is set (the dock menu); any valid texture otherwise.
    property Item uMenu: uIcons
    property point uOrigin: Qt.point(x, y)
    property point uItemSize: Qt.point(width, height)
    property real uMaterial: 1
    property real uRegularMix: 0
    // Fully frosted: the mockup's .9 left a .1 share of the sharp capture, and over a window its
    // thin lines showed through as stripes (the dock menu 2026-09-25, the dock plate 27.09).
    property real uRegularBlur: 1
    property real uRegularLight: 0
    property real uRegularDark: 0
    property real uRegularContrast: 1
    property real uRegularTint: 1
    property real uRegularSaturation: 1.07
    property real uRegularDock: .31
    property real uIsDock: 0
    property real uFlat: 0
    property vector4d uFlatColor: none
    property point uSceneSize: Qt.point(1, 1)
    property vector4d uCover: Qt.vector4d(0, 0, 1, 1)
    property vector4d uRect: none
    property vector4d uRectB: none
    property real uRadius: 18
    property real uRadiusB: 18
    property real uUnion: 0
    property real uHasB: 0
    property real uEdge: 6
    property real uThickness: 2.2
    property real uRefraction: 4.5
    property real uDispersion: 0
    property real uLightAngle: 3.97935
    property real uSpecular: 0
    property real uShininess: 26
    property real uRim: .18
    property real uRimWidth: 1
    property real uInnerShadow: .055
    property real uInnerWidth: 1.8
    property real uShadow: .16
    property real uShadowSoftness: 28
    property real uShadowOffset: 5
    property real uTint: .14
    property vector3d uTintColor: Qt.vector3d(.32, .26, .34)
    property real uAdaptation: 1
    property real uContrast: 1
    property real uSaturation: 1.5
    property real uBlur: .69
    property real uActivity: 0
    property real uOpacity: 1
    property vector4d uTrack: none
    property real uTrackValue: 0
    property real uTrackEnabled: 0
    property real uOnlyTrack: 0
    property vector4d uRectC: none
    property real uHasC: 0
    property real uRadiusC: 6
    property real uUnionC: 0
    property real uDropCount: Math.min(8, drops.length)
    property vector4d uDrop0: drops.length > 0 ? drops[0].rect : none
    property vector4d uDrop1: drops.length > 1 ? drops[1].rect : none
    property vector4d uDrop2: drops.length > 2 ? drops[2].rect : none
    property vector4d uDrop3: drops.length > 3 ? drops[3].rect : none
    property vector4d uDrop4: drops.length > 4 ? drops[4].rect : none
    property vector4d uDrop5: drops.length > 5 ? drops[5].rect : none
    property vector4d uDrop6: drops.length > 6 ? drops[6].rect : none
    property vector4d uDrop7: drops.length > 7 ? drops[7].rect : none
    property vector4d uDropParam0: drops.length > 0 ? drops[0].params : none
    property vector4d uDropParam1: drops.length > 1 ? drops[1].params : none
    property vector4d uDropParam2: drops.length > 2 ? drops[2].params : none
    property vector4d uDropParam3: drops.length > 3 ? drops[3].params : none
    property vector4d uDropParam4: drops.length > 4 ? drops[4].params : none
    property vector4d uDropParam5: drops.length > 5 ? drops[5].params : none
    property vector4d uDropParam6: drops.length > 6 ? drops[6].params : none
    property vector4d uDropParam7: drops.length > 7 ? drops[7].params : none
    property vector4d uBulge: Qt.vector4d(0, 0, 1, 0)
    property real uDematerialize: 0
    property real uHasIcons: 0
    property real uDropDome: 0
    property real uRimLight: 0
    property real uRimLightWidth: 0
    // The light base, always: the glass does not adapt to what lies underneath
    // (2026-09-27). The shader still reads <0 as its automatic palette; nothing sets that now.
    property real uRegularBase: 1
    property real uLiveTop: -100000
    property real uRegularBaseB: 1
    // The selected / current drop is tinted (2026-09-27): a drop with `selected: true`
    // takes uSelectedColor.rgb for uSelectedColor.a of its body (LiquidPalette.selectedDrop);
    // uDropSelected0/1 carry drops 0–3 / 4–7's `selected`: true or a weight 0..1 (the open
    // tab's orange comes in with the morph). The rest stay plain glass.
    property color selectedColor: LiquidPalette.selectedDrop
    property vector4d uSelectedColor: Qt.vector4d(selectedColor.r, selectedColor.g, selectedColor.b, selectedColor.a)
    readonly property var selectedFlags: [0, 1, 2, 3, 4, 5, 6, 7].map(i => i < drops.length ? Number(drops[i].selected || 0) : 0)
    property vector4d uDropSelected0: Qt.vector4d(selectedFlags[0], selectedFlags[1], selectedFlags[2], selectedFlags[3])
    property vector4d uDropSelected1: Qt.vector4d(selectedFlags[4], selectedFlags[5], selectedFlags[6], selectedFlags[7])
    // Default off: the shell's existing islands retain their exact rectangle field.
    property real uWaveEnabled: 0
    property vector4d uWaveEdge: Qt.vector4d(0, 0, 1, 0)
    blending: true
    fragmentShader: "file://" + (Quickshell.env("EMAKI_SHELL_SHADER_DIR") || Quickshell.shellPath("shaders")) + "/dock.frag.qsb"
}
