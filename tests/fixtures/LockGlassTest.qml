pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

ShellRoot {
    id: root
    property var retainedGrab: null
    property int captureStage: 0
    property Image previousCapture: null
    property int pausedPaints: 0
    property Canvas previousFlat: null
    function check(ok: bool, name: string): void {
        if (!ok)
            throw new Error(name);
        console.log("LOCK_GLASS_PASS " + name);
    }
    // The source and lock visual have different windows: the image provider, not a
    // cross-window GPU texture, carries these synthetic pixels into the lock surface.
    FloatingWindow {
        visible: true
        implicitWidth: 64
        implicitHeight: 64
        Rectangle {
            id: sourcePixels
            anchors.fill: parent
            color: "#d31f3d"
        }
    }
    Window {
        visible: true
        width: 1536
        height: 960
        Image {
            id: frozenStandIn
            width: 8
            height: 8
            source: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
            visible: false
        }
        DockBackdrop {
            id: frozenBackdrop
            mode: "wallpaper"
            frozenSource: frozenStandIn
            frozenReady: true
            screenWidth: 8
            screenHeight: 8
        }
        LockGlass {
            id: visual
            anchors.fill: parent
            wallpaperTexture: String(frozenStandIn.source)
            phase: "pour"
            elapsed: .45
            clock: .45
        }
        LockVisual {
            id: missingWallpaper
            width: 32
            height: 32
            visible: false
        }
        Canvas {
            id: pixelProbe
            property string imageUrl: ""
            width: 64
            height: 64
            renderTarget: Canvas.Image
            onImageLoaded: requestPaint()
            onPaint: {
                if (!imageUrl || !isImageLoaded(imageUrl))
                    return;
                const context = getContext("2d");
                context.drawImage(imageUrl, 0, 0);
                const pixel = context.getImageData(32, 32, 1, 1).data;
                root.check(pixel[0] === 211 && pixel[1] === 31 && pixel[2] === 61 && pixel[3] === 255, "receiving window reads the captured pixels rather than black");
                unloadImage(imageUrl);
                imageUrl = "";
                root.captureStage = 2;
            }
        }
    }
    Timer {
        interval: 100
        repeat: true
        running: true
        onTriggered: {
            if (!missingWallpaper.wallpaperFailed)
                return;
            stop();
            root.check(frozenBackdrop.sharp === frozenStandIn && frozenBackdrop.ready && !frozenBackdrop.capturing, "same-window frozen source bypasses capture and wallpaper");
            root.check(missingWallpaper.wallpaperFailed && missingWallpaper.readyForPour && missingWallpaper.canPour(false) && missingWallpaper.canPour(true), "terminal wallpaper failure begins a flat pour on GPU and Software without the preparation deadline");
            frozenBackdrop.frozenReady = false;
            root.check(!frozenBackdrop.ready, "frozen source readiness belongs to owner");
            // Missing wallpaper and Software must always resolve to a non-shader plate.
            root.check(!visual.glassReady && visual.flatPlate.visible, "Software selects an actual visible Canvas fallback");
            const flat = visual.flatPlate.getContext("2d");
            const upper = flat.getImageData(200, 100, 1, 1).data;
            root.check(upper[3] === 255 && flat.getImageData(200, 850, 1, 1).data[3] === 0, "flat fallback paints an opaque sheet above the moving edge and leaves the reveal below it");
            root.check(upper[0] === 255 && Math.abs(upper[1] - 174) <= 2 && Math.abs(upper[2] - 170) <= 2, "flat palette is composited over the actual wallpaper");
            root.check(visual.wallpaperImage === visual.plateGlass.wallBackdrop.frozenSource, "wallpaper shader reads the existing Image without a second decode");
            root.check(visual.plateGlass.uWaveEnabled === 1, "lock explicitly opts into wave field");
            root.check(Math.abs(visual.edgePosition - 485) < .001, "pour crosses centre downward");
            root.check(visual.fieldLife === 0 && visual.avatarLife === 0, "controls absent before edge reaches them");
            visual.bubbleAge = .2;
            root.check(visual.fieldLife > visual.avatarLife && visual.avatarLife > 0, "avatar follows field rise");
            visual.phase = "locked";
            visual.bubbleAge = 1;
            root.check(visual.fieldWidth === 320 && visual.avatarSize === 96 && visual.fieldLife === 1, "steady logical drop dimensions");
            visual.wrongAge = .09;
            root.check(Math.abs(visual.shake) > 1, "rejection drives shake");
            visual.reducedMotion = true;
            root.check(visual.shake === 0 && visual.waveAmplitude === 0, "reduced motion removes wobble and waves");
            visual.reducedMotion = false;
            visual.phase = "melt";
            visual.elapsed = .11;
            root.check(Math.abs(visual.fieldLife - .5) < .001 && Math.abs(visual.avatarLife - .5) < .001, "both drops melt before drain");
            visual.phase = "drain";
            visual.elapsed = .5;
            root.check(visual.plateGlass.uWaveEdge.z === -1 && visual.fieldLife === 0, "drain moves down with glass below the edge");
            visual.phase = "finished";
            root.check(visual.edgePosition > visual.height && visual.plateGlass.uWaveEdge.z === -1, "finished drain never flashes the plate back");
            visual.phase = "locked";
            visual.showControls = false;
            root.check(visual.fieldLife === 0 && visual.avatarLife === 0, "secondary outputs have plate only");
            visual.active = false;
            root.check(visual.flatPlate.visible && !visual.active, "turning output off retains the rendered flat sheet");
            root.check(sourcePixels.grabToImage(result => {
                root.retainedGrab = result;
                visual.captureUrl = result.url;
                root.captureStage = 1;
            }), "cross-window CPU grab request accepted");
        }
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        onTriggered: {
            if (root.captureStage === 1 && visual.captureReady) {
                const image = visual.plateGlass.uSharp;
                root.check(String(image.source).startsWith("itemgrabber:"), "frozen frame uses an in-memory image provider");
                root.check(image.sourceSize.width === 64 && image.sourceSize.height === 64 && !image.cache, "CPU frame loads in the receiving window without cache");
                root.check(image.Window.window === visual.Window.window && image.Window.window !== sourcePixels.Window.window, "frozen image belongs to the receiving window");
                root.check(visual.plateGlass.uSharp === image && visual.plateGlass.wallBackdrop === visual.plateGlass.backdrop && visual.plateGlass.backdrop.frozenSource === visual.wallpaperImage, "frozen sharp is a plain Image and only wallpaper has a blur pipeline");
                root.check(visual.plateGlass.uRegularLow === visual.plateGlass.wallBackdrop.regular && visual.plateGlass.uRegularHigh === visual.plateGlass.wallBackdrop.regular && visual.plateGlass.uBlurred === visual.plateGlass.wallBackdrop.clear && visual.plateGlass.uLiveTop === -100000, "plate and drops sample only wallpaper while a capture is present");
                root.check(image.visible && visual.flatPlate.visible && !visual.glassReady, "Software draws the capture behind the independent flat sheet");
                root.check(visual.plateGlass.uWaveEdge.w === 0, "idle glass has a frozen wave clock");
                visual.clock = 123;
                root.check(visual.plateGlass.uWaveEdge.w === 0 && visual.plateGlass.drops.length === 0, "idle clock changes allocate no drop or selected arrays");
                root.pausedPaints = visual.flatPlate.paintCount;
                visual.phase = "pour";
                visual.elapsed = .45;
                visual.clock = 124;
                root.previousCapture = image;
                visual.captureUrl = "";
                root.captureStage = 4;
                pixelProbe.imageUrl = root.retainedGrab.url;
                pixelProbe.loadImage(pixelProbe.imageUrl);
                pixelProbe.requestPaint();
            } else if (root.captureStage === 2) {
                root.check(visual.flatPlate.paintCount === root.pausedPaints, "inactive output does not repaint even when the animated edge changes");
                visual.active = true;
                root.check(!visual.captureReady && String(root.previousCapture.source) === "" && root.previousCapture.status === Image.Null, "privacy revocation unloads the receiving image");
                root.check(visual.plateGlass.uSharp === visual.wallpaperImage && visual.plateGlass.backdrop.frozenSource === visual.wallpaperImage, "revoked frame disconnects from the sharp sampler");
                root.retainedGrab = null;
                root.previousCapture = null;
                visual.captureUrl = "itemgrabber:#nonexistent-lock-fixture";
                root.captureStage = 3;
            } else if (root.captureStage === 3) {
                root.check(!visual.captureReady && visual.plateGlass.backdrop === visual.plateGlass.wallBackdrop && !visual.glassReady, "invalid frozen URL retains the wallpaper and opaque fallback");
                visual.captureUrl = "";
                root.previousFlat = visual.flatPlate;
                root.check(root.previousFlat !== null, "Software fallback Canvas exists only while its Loader is active");
                visual.fallbackLoader.active = false;
                root.captureStage = 5;
            } else if (root.captureStage === 5) {
                root.check(visual.flatPlate === null && root.previousFlat === null, "disabling fallback destroys its Canvas and releases the backing item");
                visual.fallbackLoader.active = true;
                root.captureStage = 6;
            } else if (root.captureStage === 6) {
                root.check(visual.flatPlate !== null, "fallback Canvas can be recreated after a renderer failure");
                console.log("LOCK_GLASS_COMPLETE");
                Qt.quit();
            }
        }
    }
}
