pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "Liquid.js" as Liquid

// Optional decoration only. LockSurface owns the opaque security backing and input.
// The owner supplies a privacy-gated, in-memory image-provider URL. The sharp reveal
// can use that frozen frame; the plate and drops always use the wallpaper's Regular glass.
Item {
    id: root
    property ShellScreen screen: null
    property string wallpaperTexture: ""
    property string captureUrl: ""
    property bool blackReveal: false
    property real dpr: 1
    property string phase: "pour"
    property real elapsed: 0
    property real clock: 0
    property real bubbleAge: -1
    property real wrongAge: 1000
    property bool showControls: true
    property bool active: true
    property bool reducedMotion: false
    property bool fallbackDrops: true
    readonly property bool glassReady: wallpaperBackdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software && glass.status !== ShaderEffect.Error
    readonly property bool captureReady: captureUrl !== "" && capturedFrame.status === Image.Ready
    readonly property alias plateGlass: glass
    readonly property alias flatPlate: flatPlateLoader.item
    readonly property alias fallbackLoader: flatPlateLoader
    readonly property alias wallpaperImage: wallpaperFrame
    property LockGeometry geometry: null
    readonly property LockGeometry effectiveGeometry: geometry || localGeometry
    LockGeometry {
        id: localGeometry
        width: root.width
        height: root.height
        phase: root.phase
        elapsed: root.geometry ? 0 : root.elapsed
        clock: root.geometry ? 0 : root.clock
        bubbleAge: root.geometry ? -1 : root.bubbleAge
        wrongAge: root.geometry ? 1000 : root.wrongAge
        showControls: root.showControls
        reducedMotion: root.reducedMotion
    }
    readonly property real pour: effectiveGeometry.pour
    readonly property real drain: effectiveGeometry.drain
    readonly property real edgePosition: effectiveGeometry.edgePosition
    readonly property real waveAmplitude: effectiveGeometry.waveAmplitude
    readonly property real melt: effectiveGeometry.melt
    readonly property real fieldRise: effectiveGeometry.fieldRise
    readonly property real avatarRise: effectiveGeometry.avatarRise
    readonly property real fieldLife: effectiveGeometry.fieldLife
    readonly property real avatarLife: effectiveGeometry.avatarLife
    readonly property real fieldWidth: effectiveGeometry.fieldWidth
    readonly property real avatarSize: effectiveGeometry.avatarSize
    readonly property real shake: effectiveGeometry.shake
    readonly property vector4d fieldRect: effectiveGeometry.fieldRect
    readonly property vector4d avatarRect: effectiveGeometry.avatarRect

    // The wallpaper remains the reveal when capture is missing, revoked or cannot load.
    Image {
        id: wallpaperFrame
        anchors.fill: parent
        source: root.wallpaperTexture
        visible: !root.blackReveal
        fillMode: Image.Stretch
        // The same cached pixels also feed the flat Canvas; the GPU uses this Image directly.
        cache: true
    }
    Image {
        id: capturedFrame
        anchors.fill: parent
        source: root.captureUrl
        visible: root.captureReady
        fillMode: Image.Stretch
        cache: false
    }
    DockBackdrop {
        id: wallpaperBackdrop
        mode: "wallpaper"
        screen: root.screen
        screenWidth: root.width
        screenHeight: root.height
        frozenSource: wallpaperFrame
        frozenReady: wallpaperFrame.status === Image.Ready
        dpr: root.dpr
        active: root.active
    }
    // Bind valid empty content to every sampler. The password/ink is drawn by the owner.
    Item {
        id: emptyInk
        width: 1
        height: 1
    }
    ShaderEffectSource {
        id: emptyTexture
        sourceItem: emptyInk
        textureSize: Qt.size(1, 1)
        hideSource: true
        visible: false
        live: false
    }
    Rectangle {
        id: blackScene
        width: 1
        height: 1
        color: "black"
        visible: root.blackReveal
    }
    ShaderEffectSource {
        id: blackTexture
        sourceItem: root.blackReveal ? blackScene : null
        textureSize: Qt.size(1, 1)
        hideSource: true
        visible: false
        live: false
    }
    GlassShape {
        id: glass
        anchors.fill: parent
        visible: root.glassReady
        backdrop: wallpaperBackdrop
        uSharp: root.blackReveal ? blackTexture : root.captureReady ? capturedFrame : wallpaperFrame
        wallBackdrop: wallpaperBackdrop
        // The frozen desktop is only the sharp scene outside the sheet. Keep all
        // frosted samples wallpaper-only, including the wave's lens and drop optics.
        uBlurred: wallpaperBackdrop.clear
        uRegularLow: wallpaperBackdrop.regular
        uRegularHigh: wallpaperBackdrop.regular
        uIcons: emptyTexture
        uHasIcons: 0
        uSceneSize: Qt.point(root.width, root.height)
        uOrigin: Qt.point(0, 0)
        uRect: Qt.vector4d(0, 0, root.width, root.height)
        uCover: wallpaperBackdrop.coverFor(Qt.point(0, 0))
        uRadius: 0
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
        uBulge: Qt.vector4d(0, 0, 1, 0)
        uIsDock: 2
        uDropDome: 1
        uLiveTop: -100000
        uWaveEnabled: 1
        uWaveEdge: Qt.vector4d(root.edgePosition, root.waveAmplitude, root.phase === "drain" || root.phase === "finished" ? -1 : 1, root.waveAmplitude > 0 ? root.clock : 0)
        // Fixed shader slots avoid rebuilding JS arrays (including selectedFlags) every frame.
        uDropCount: (root.avatarLife > .001 ? 1 : 0) + (root.fieldLife > .001 ? 1 : 0)
        uDrop0: root.avatarLife > .001 ? root.avatarRect : root.fieldRect
        uDrop1: root.fieldRect
        uDropParam0: root.avatarLife > .001 ? Qt.vector4d(root.avatarSize / 2, 0, 1, root.avatarLife) : Qt.vector4d(28, 0, 1, root.fieldLife)
        uDropParam1: Qt.vector4d(28, 0, 1, root.fieldLife)
    }
    // Shader-free fallback: the exact moving wave clips wallpaper plus the shell palette.
    // The capture is only behind this sheet; covered desktop pixels are never tinted through it.
    Loader {
        id: flatPlateLoader
        anchors.fill: parent
        // Do not allocate a full-output Canvas during GPU preparation. The opaque
        // lock backing covers preparation; a fallback is needed once its pour begins.
        active: !root.glassReady && (GraphicsInfo.api === GraphicsInfo.Software || root.elapsed > 0 || root.phase !== "pour")
        sourceComponent: Component {
            Canvas {
                id: flatPlate
                anchors.fill: parent
                renderTarget: Canvas.Image
                property string loadedWallpaper: ""
                property int paintCount: 0
                function repaint(): void {
                    if (visible && root.active)
                        requestPaint();
                }
                function refreshWallpaper(): void {
                    if (loadedWallpaper)
                        unloadImage(loadedWallpaper);
                    loadedWallpaper = visible ? root.wallpaperTexture : "";
                    if (loadedWallpaper)
                        loadImage(loadedWallpaper);
                    repaint();
                }
                function wave(x: real): real {
                    return root.waveAmplitude * (.55 * Math.sin(x * .0061 + root.clock * 2.3) + .30 * Math.sin(x * .0137 - root.clock * 3.1 + 1.3) + .15 * Math.sin(x * .029 + root.clock * 4.7 + 2.1));
                }
                onVisibleChanged: refreshWallpaper()
                onImageLoaded: repaint()
                onWidthChanged: repaint()
                onHeightChanged: repaint()
                Component.onCompleted: refreshWallpaper()
                Connections {
                    target: root
                    function onWallpaperTextureChanged(): void {
                        flatPlate.refreshWallpaper();
                    }
                    function onEdgePositionChanged(): void {
                        flatPlate.repaint();
                    }
                    function onWaveAmplitudeChanged(): void {
                        flatPlate.repaint();
                    }
                    function onClockChanged(): void {
                        if (root.waveAmplitude > 0)
                            flatPlate.repaint();
                    }
                    function onActiveChanged(): void {
                        flatPlate.repaint();
                    }
                    function onPhaseChanged(): void {
                        flatPlate.repaint();
                    }
                }
                onPaint: {
                    ++paintCount;
                    const context = getContext("2d");
                    context.reset();
                    const bottom = root.phase === "drain" || root.phase === "finished";
                    const outside = bottom ? height : 0;
                    context.beginPath();
                    context.moveTo(0, outside);
                    context.lineTo(0, root.edgePosition + wave(0));
                    for (let x = 4; x < width; x += 4)
                        context.lineTo(x, root.edgePosition + wave(x));
                    context.lineTo(width, root.edgePosition + wave(width));
                    context.lineTo(width, outside);
                    context.closePath();
                    context.clip();
                    const palette = LiquidPalette.flatPlate;
                    context.fillStyle = Qt.rgba(palette.r, palette.g, palette.b, 1);
                    context.fillRect(0, 0, width, height);
                    if (loadedWallpaper && isImageLoaded(loadedWallpaper))
                        context.drawImage(loadedWallpaper, 0, 0, width, height);
                    context.fillStyle = palette;
                    context.fillRect(0, 0, width, height);
                }
            }
        }
    }
    Rectangle {
        visible: root.fallbackDrops && !root.glassReady && root.fieldLife > 0
        x: root.fieldRect.x
        y: root.fieldRect.y
        width: root.fieldRect.z
        height: root.fieldRect.w
        radius: 28
        color: LiquidPalette.flatDrop
        border.color: LiquidPalette.flatDropRim
        opacity: root.fieldLife
    }
    Rectangle {
        visible: root.fallbackDrops && !root.glassReady && root.avatarLife > 0
        x: root.avatarRect.x
        y: root.avatarRect.y
        width: root.avatarRect.z
        height: root.avatarRect.w
        radius: width / 2
        color: LiquidPalette.flatDrop
        border.color: LiquidPalette.flatDropRim
        opacity: root.avatarLife
    }
}
