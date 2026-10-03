pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// The hold uses the exact published frame. The drain masks its captured plate
// with C8's wave and retains the existing wordmark particle simulation.
Item {
    id: surface
    required property SessionCoverState session
    required property string imagePath
    property ShellScreen screen: null
    property string idleMode: "lake"
    property real wordmarkOrigin: 0
    readonly property string outputName: screen ? screen.name : "offscreen"
    readonly property real dpr: screen ? screen.devicePixelRatio : 1
    readonly property bool visualReady: picture.status === Image.Ready && plate.status === Image.Ready
    property bool discardQueuedFrame: true
    onVisualReadyChanged: {
        discardQueuedFrame = true;
        session.visualReady(outputName, visualReady);
        surface.Window.window?.update();
    }
    Image {
        id: picture
        anchors.fill: parent
        source: "file://" + surface.imagePath
        asynchronous: false
        smooth: false
        visible: surface.session.phase === "locked"
    }
    Image {
        id: plate
        anchors.fill: parent
        source: "file://" + surface.imagePath.replace(".png", "-plate.png")
        asynchronous: false
        smooth: false
        visible: false
    }
    ShaderEffect {
        anchors.fill: parent
        visible: surface.session.phase === "drain" && GraphicsInfo.api !== GraphicsInfo.Software
        property variant source: plate
        property vector2d size: Qt.vector2d(width, height)
        property real edge: -70 + (height + 140) * (.35 * surface.session.elapsed + .65 * surface.session.elapsed * surface.session.elapsed)
        property real clock: surface.session.clock
        fragmentShader: Qt.resolvedUrl("shaders/session-drain.frag.qsb")
    }
    // The same wave for Qt's software backend, which cannot run ShaderEffect.
    Canvas {
        id: softwareDrain
        anchors.fill: parent
        visible: surface.session.phase === "drain" && GraphicsInfo.api === GraphicsInfo.Software
        property real tick: surface.session.elapsed
        onTickChanged: requestPaint()
        onImageLoaded: requestPaint()
        Component.onCompleted: loadImage(plate.source)
        onPaint: {
            const c = getContext("2d");
            c.reset();
            c.clearRect(0, 0, width, height);
            if (!isImageLoaded(plate.source))
                return;
            const t = surface.session.elapsed, clock = surface.session.clock;
            const edge = -70 + (height + 140) * (.35 * t + .65 * t * t);
            c.beginPath();
            c.moveTo(0, height);
            for (let x = 0; x <= width + 4; x += 4) {
                const wave = 24 * (.55 * Math.sin(x * .0061 + clock * 2.3) + .30 * Math.sin(x * .0137 - clock * 3.1 + 1.3) + .15 * Math.sin(x * .029 + clock * 4.7 + 2.1));
                c.lineTo(x, edge + wave);
            }
            c.lineTo(width, height);
            c.closePath();
            c.clip();
            c.drawImage(plate.source, 0, 0, width, height);
        }
    }
    LockWordmark {
        anchors.fill: parent
        active: surface.session.phase === "drain"
        phase: surface.session.phase
        elapsed: surface.session.elapsed
        clock: surface.session.clock
        introComplete: true
        introStart: surface.wordmarkOrigin
        idleMode: surface.idleMode
        dpr: surface.dpr
    }
    Component.onCompleted: {
        session.registerOutput(outputName);
        session.visualReady(outputName, visualReady);
    }
    Component.onDestruction: {
        if (session)
            session.removeOutput(outputName);
    }
    Connections {
        target: surface.Window.window
        function onFrameSwapped(): void {
            if (surface.discardQueuedFrame) {
                surface.discardQueuedFrame = false;
                surface.Window.window?.update();
            } else if (surface.visualReady) {
                surface.session.painted(surface.outputName);
            }
        }
    }
    Connections {
        target: surface.session
        function onRequestFrames(): void {
            surface.Window.window?.update();
        }
    }
}
