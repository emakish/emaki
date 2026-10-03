pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

// What lies under the glass, in screen space, plus the two blurred copies the mockup
// shader samples (docs/mockups/liquid-glass/dock.js: prepareBlur, prepareRegularBlur).
// One per surface that draws glass: the dock, the top bar, the launcher overlay.
//   "capture":   live wlr-screencopy of the output. Needs Emaki's niri, which leaves the
//                shell's own surfaces out of the shell's capture; on stock niri the capture
//                would contain the shell itself.
//   "wallpaper": the untouched wallpaper (stock niri fallback; windows are not refracted).
// `region` limits the textures to one screen rectangle (the bar's band, the launcher's
// corner): the blur passes then cost that rectangle, not the whole output. The glass maps
// its scene onto the region through coverFor(). Default: the whole output.
// Everything here is hidden (ShaderEffectSource.hideSource); nothing is drawn on screen.
Item {
    id: backdrop
    required property string mode
    property ShellScreen screen: null
    property string wallpaperTexture: ""
    // Shell-only opt-in: all regions use the same full-image pixmap cache key. Qt shares
    // the decode and, within each window, its texture; shaders sample a UV region, never an
    // ImageReader crop or a full-resolution regional texture. Legacy/frozen users keep their existing loading contract.
    property bool sharedWallpaper: false
    // A dedicated window with a small band can cache that crop instead of uploading
    // the full shared image. This is independent of the opt-in full-image UV path.
    property bool cacheWallpaper: false
    // Image is a texture provider; it must belong to this window. The owner retains
    // its in-memory result and supplies readiness. No cross-window GPU sharing occurs.
    property Image frozenSource: null
    property bool frozenReady: false
    property real screenWidth: 1
    property real screenHeight: 1
    property real dpr: 1
    property rect region: Qt.rect(0, 0, screenWidth, screenHeight)
    readonly property bool whole: region.x === 0 && region.y === 0 && region.width === screenWidth && region.height === screenHeight
    readonly property real regionWidth: Math.max(1, region.width)
    readonly property real regionHeight: Math.max(1, region.height)
    // Capture only while someone looks at the glass; each activation starts a new capture
    // (see ScreencopyView below), so `ready` is false while inactive and for a frame after.
    property bool active: true
    readonly property bool capturing: frozenSource === null && mode === "capture" && screen !== null
    readonly property Item sharp: frozenSource !== null ? frozenSource : capturing ? captureSource : still
    readonly property bool sharedSharp: sharedWallpaper && !capturing && frozenSource === null && !whole
    readonly property real imageWidth: Math.max(1, still.sourceSize.width)
    readonly property real imageHeight: Math.max(1, still.sourceSize.height)
    readonly property vector4d sharpRect: sharedSharp ? Qt.vector4d(Math.round(region.x * dpr) / imageWidth, Math.round(region.y * dpr) / imageHeight, Math.round(regionWidth * dpr) / imageWidth, Math.round(regionHeight * dpr) / imageHeight) : Qt.vector4d(0, 0, 1, 1)
    // Match the cropped Image's clamp-to-edge even when refraction reaches its edge.
    readonly property vector4d sharpBounds: sharedSharp ? Qt.vector4d(sharpRect.x + .5 / imageWidth, sharpRect.y + .5 / imageHeight, sharpRect.x + sharpRect.z - .5 / imageWidth, sharpRect.y + sharpRect.w - .5 / imageHeight) : Qt.vector4d(0, 0, 1, 1)
    readonly property Item clear: clearV
    readonly property Item regular: regularV
    readonly property bool ready: (frozenSource !== null ? frozenReady && frozenSource.status === Image.Ready : capturing ? capture.hasContent : still.status === Image.Ready) && shadersReady
    readonly property bool shadersReady: halfPass.status !== ShaderEffect.Error && quarterPass.status !== ShaderEffect.Error && clearHPass.status !== ShaderEffect.Error && clearVPass.status !== ShaderEffect.Error && regularHPass.status !== ShaderEffect.Error && regularVPass.status !== ShaderEffect.Error
    // Mockup: Clear blur is σ 9 texels of a 1280-wide map of the wallpaper; the wallpaper
    // covers the screen, so σ is 9/1280 of the screen width. Regular is σ 30 logical px.
    readonly property real clearSigma: 9 * screenWidth / 1280
    readonly property real regularSigma: 30
    readonly property string shaderDir: Quickshell.env("EMAKI_SHELL_SHADER_DIR") || Quickshell.shellPath("shaders")
    // uCover for a glass whose scene origin sits at `origin` on the screen.
    function coverFor(origin: point): vector4d {
        return Qt.vector4d((origin.x - region.x) / regionWidth, (origin.y - region.y) / regionHeight, 1 / regionWidth, 1 / regionHeight);
    }
    width: regionWidth
    height: regionHeight
    visible: true
    enabled: false

    Image {
        id: still
        visible: false
        width: backdrop.sharedWallpaper ? backdrop.screenWidth : backdrop.regionWidth
        height: backdrop.sharedWallpaper ? backdrop.screenHeight : backdrop.regionHeight
        source: backdrop.capturing || backdrop.frozenSource !== null ? "" : backdrop.wallpaperTexture
        // The wallpaper texture is the output in device pixels; a region loads only its part.
        sourceSize: backdrop.sharedWallpaper || backdrop.whole ? undefined : Qt.size(Math.round(backdrop.screenWidth * backdrop.dpr), Math.round(backdrop.screenHeight * backdrop.dpr))
        sourceClipRect: backdrop.sharedWallpaper || backdrop.whole ? Qt.rect(0, 0, 0, 0) : Qt.rect(Math.round(backdrop.region.x * backdrop.dpr), Math.round(backdrop.region.y * backdrop.dpr), Math.round(backdrop.regionWidth * backdrop.dpr), Math.round(backdrop.regionHeight * backdrop.dpr))
        cache: backdrop.sharedWallpaper || backdrop.cacheWallpaper
        smooth: true
    }
    ScreencopyView {
        id: capture
        width: backdrop.screenWidth
        height: backdrop.screenHeight
        // A new capture on every activation, none while inactive. After its first frame
        // Quickshell asks only copy_with_damage, which waits for damage, and the shell's own
        // surfaces make none in its own capture: a panel opened over a still screen showed the
        // frame captured when the shell started (27.09: the launcher over the wallpaper drew
        // the terminal of another workspace). A fresh context starts with a plain copy, which
        // comes at the next compositor frame; until then `ready` is false and the owner draws
        // its wallpaper fallback.
        captureSource: backdrop.capturing && backdrop.active ? backdrop.screen : null
        live: backdrop.capturing && backdrop.active
        paintCursor: false
    }
    ShaderEffectSource {
        id: captureSource
        width: backdrop.regionWidth
        height: backdrop.regionHeight
        sourceItem: capture
        sourceRect: backdrop.whole ? Qt.rect(0, 0, 0, 0) : backdrop.region
        hideSource: true
        live: true
        visible: false
        textureSize: Qt.size(Math.round(backdrop.regionWidth * backdrop.dpr), Math.round(backdrop.regionHeight * backdrop.dpr))
    }
    component Pass: ShaderEffect {
        required property Item source
        property point axis: Qt.point(0, 0)
        property real sigma: 1
        width: backdrop.regionWidth
        height: backdrop.regionHeight
        fragmentShader: "file://" + backdrop.shaderDir + "/gauss.frag.qsb"
    }
    component Layer: ShaderEffectSource {
        required property int factor
        width: backdrop.regionWidth
        height: backdrop.regionHeight
        hideSource: true
        live: true
        visible: false
        smooth: true
        textureSize: Qt.size(Math.max(1, Math.round(backdrop.regionWidth * backdrop.dpr / factor)), Math.max(1, Math.round(backdrop.regionHeight * backdrop.dpr / factor)))
    }
    component Down: ShaderEffect {
        required property Item source
        property vector4d uvRect: Qt.vector4d(0, 0, 1, 1)
        width: backdrop.regionWidth
        height: backdrop.regionHeight
        fragmentShader: "file://" + backdrop.shaderDir + "/down.frag.qsb"
    }
    // Box-downsampled copies of what lies under the glass (2×2 averages): half size for
    // Clear, quarter for Regular. The Gaussian passes then read every texel of their own size
    // (gauss.frag); reading the full-size capture sparsely drew a lattice (27.09).
    Down {
        id: halfPass
        source: backdrop.sharp
        uvRect: backdrop.sharpRect
    }
    Layer {
        id: half
        factor: 2
        sourceItem: halfPass
    }
    Down {
        id: quarterPass
        source: half
    }
    Layer {
        id: quarter
        factor: 4
        sourceItem: quarterPass
    }
    // Clear: half resolution. Regular: quarter resolution; σ stays in logical pixels.
    Pass {
        id: clearHPass
        source: half
        axis: Qt.point(1 / clearH.textureSize.width, 0)
        sigma: backdrop.clearSigma * backdrop.dpr / 2
    }
    Layer {
        id: clearH
        factor: 2
        sourceItem: clearHPass
    }
    Pass {
        id: clearVPass
        source: clearH
        axis: Qt.point(0, 1 / clearV.textureSize.height)
        sigma: backdrop.clearSigma * backdrop.dpr / 2
    }
    Layer {
        id: clearV
        factor: 2
        sourceItem: clearVPass
    }
    Pass {
        id: regularHPass
        source: quarter
        axis: Qt.point(1 / regularH.textureSize.width, 0)
        sigma: backdrop.regularSigma * backdrop.dpr / 4
    }
    Layer {
        id: regularH
        factor: 4
        sourceItem: regularHPass
    }
    Pass {
        id: regularVPass
        source: regularH
        axis: Qt.point(0, 1 / regularV.textureSize.height)
        sigma: backdrop.regularSigma * backdrop.dpr / 4
    }
    Layer {
        id: regularV
        factor: 4
        sourceItem: regularVPass
    }
}
