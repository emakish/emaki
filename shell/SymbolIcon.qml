pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// A monochrome theme icon (Adwaita "*-symbolic") in the ink of the glass under it, as
// drawSymbol() in liquid-glass/launcher.js: draw, then fill with the ink through
// source-in. Canvas, so it also renders in software Qt. Nothing when the theme lacks it.
Canvas {
    id: symbol
    required property string name
    property color ink: LiquidPalette.inkOnDark
    readonly property string path: name ? Quickshell.iconPath(name, true) : ""
    readonly property bool found: path !== ""
    property string loadedPath: ""
    width: 18
    height: 18
    renderTarget: Canvas.Image
    visible: found
    function reload(): void {
        if (loadedPath && loadedPath !== path)
            unloadImage(loadedPath);
        loadedPath = path;
        if (path && !isImageLoaded(path))
            loadImage(path);
        requestPaint();
    }
    onPathChanged: reload()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onImageLoaded: requestPaint()
    Component.onCompleted: reload()
    onPaint: {
        const c = getContext("2d");
        c.reset();
        c.clearRect(0, 0, width, height);
        if (!loadedPath || !isImageLoaded(loadedPath))
            return;
        c.globalCompositeOperation = "source-over";
        c.drawImage(loadedPath, 0, 0, width, height);
        c.globalCompositeOperation = "source-in";
        c.fillStyle = ink;
        c.fillRect(0, 0, width, height);
    }
}
