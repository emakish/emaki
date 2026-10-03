pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Loaded by URL: failure anywhere in this visual tree leaves the independent flat UI.
Item {
    id: visual
    property ShellScreen screen: null
    property LockGeometry geometry: null
    property string captureUrl: ""
    property string wallpaperRoot: ""
    property bool greeter: false
    property bool preparationExpired: false
    onElapsedChanged: {
        // A late decoder must not replace the chosen flat pour midway through it.
        if (greeter && phase === "pour" && elapsed > 0 && !readyForPour)
            preparationExpired = true;
    }
    onWallpaperRootChanged: preparationExpired = false
    property real dpr: 1
    property string phase: "pour"
    property real elapsed: 0
    property real clock: 0
    property real bubbleAge: -1
    property real wrongAge: 100
    property bool showControls: true
    property bool active: true
    property bool reducedMotion: false
    readonly property bool glassReady: glass.glassReady
    readonly property bool wallpaperFailed: wallpaper.state !== "ready" && wallpaper.state !== "loading" && wallpaper.state !== "idle"
    readonly property bool readyForPour: canPour(GraphicsInfo.api === GraphicsInfo.Software)
    function canPour(software: bool): bool {
        // Missing/unsupported wallpaper is terminal on either renderer: begin the
        // flat pour immediately instead of waiting for a texture that cannot arrive.
        return glassReady || wallpaperFailed || (software && glass.wallpaperImage.status === Image.Ready);
    }
    WallpaperSource {
        id: wallpaper
        outputWidth: Math.round(visual.width)
        outputHeight: Math.round(visual.height)
        outputScale: visual.dpr
        outputName: visual.screen ? visual.screen.name : ""
        variant: "sharp"
        publishedRoot: visual.wallpaperRoot
        enabled: !visual.greeter || visual.wallpaperRoot.length > 0
        isolateHelper: visual.greeter
    }
    LockGlass {
        id: glass
        geometry: visual.geometry
        fallbackDrops: false
        anchors.fill: parent
        screen: visual.screen
        wallpaperTexture: visual.preparationExpired ? "" : wallpaper.texture
        blackReveal: visual.greeter
        captureUrl: visual.captureUrl
        dpr: visual.dpr
        phase: visual.phase
        elapsed: visual.elapsed
        clock: visual.clock
        bubbleAge: visual.bubbleAge
        wrongAge: visual.wrongAge
        showControls: visual.showControls
        active: visual.active
        reducedMotion: visual.reducedMotion
    }
}
