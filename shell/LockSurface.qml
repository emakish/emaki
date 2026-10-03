pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

Item {
    id: surface
    required property LockSession session
    required property AuthController auth
    required property LockEnvironment environment
    property ShellScreen screen: null
    readonly property Item glassItem: visual.item as Item
    property string captureUrl: ""
    property string wallpaperRoot: ""
    property bool greeter: false
    property bool wallpaperSelectionReady: true
    readonly property bool revealCapture: session.phase === "pour" || (session.authenticated && session.phase === "drain")
    readonly property string outputName: screen ? screen.name : "offscreen"
    readonly property real dpr: screen ? screen.devicePixelRatio : 1
    readonly property bool controls: session.selectedOutput === outputName
    readonly property bool fieldsVisible: controls && !(greeter && session.phase === "handoff")
    readonly property real age: session.bubbleAge(height)
    readonly property real life: geometry.fieldLife
    readonly property real fieldWidth: geometry.fieldWidth
    readonly property real avatarSize: geometry.avatarSize
    readonly property real avatarLife: geometry.avatarLife
    readonly property real shake: geometry.shake
    LockGeometry {
        id: geometry
        width: surface.width
        height: surface.height
        phase: surface.session.phase
        elapsed: surface.session.elapsed
        clock: surface.session.clock
        bubbleAge: surface.age
        wrongAge: surface.session.wrongAge
        showControls: surface.fieldsVisible
        reducedMotion: surface.session.reducedMotion
    }
    readonly property var visualItem: visual.item
    readonly property bool glassReady: visualItem !== null && visualItem.glassReady === true
    property real keyAt: -100
    property string registeredName: ""
    property bool initialized: false

    function register(): void {
        if (registeredName === outputName)
            return;
        if (registeredName)
            session.removeOutput(registeredName);
        registeredName = outputName;
        session.registerOutput(registeredName);
        session.visualReady(registeredName, visualItem !== null && visualItem.readyForPour === true);
    }
    readonly property bool readyForPour: visualItem !== null && visualItem.readyForPour === true
    onReadyForPourChanged: {
        if (initialized)
            session.visualReady(registeredName, readyForPour);
    }
    onOutputNameChanged: {
        if (initialized)
            register();
    }
    Component.onCompleted: {
        initialized = true;
        register();
    }
    Component.onDestruction: {
        if (session)
            session.removeOutput(registeredName);
    }
    Rectangle {
        anchors.fill: parent
        color: surface.greeter && (visual.status !== Loader.Error || surface.session.phase === "pour" || surface.session.phase === "finished") ? "black" : Qt.rgba(LiquidPalette.flatPlate.r, LiquidPalette.flatPlate.g, LiquidPalette.flatPlate.b, 1)
    }
    Loader {
        id: visual
        anchors.fill: parent
        active: !surface.greeter || surface.wallpaperSelectionReady
        source: "LockVisual.qml"
        onLoaded: {
            item.geometry = geometry;
            item.screen = Qt.binding(() => surface.screen);
            item.captureUrl = Qt.binding(() => surface.revealCapture ? surface.captureUrl : "");
            item.wallpaperRoot = Qt.binding(() => surface.wallpaperRoot);
            item.greeter = Qt.binding(() => surface.greeter);
            item.dpr = Qt.binding(() => surface.dpr);
            item.clock = Qt.binding(() => surface.session.clock);
            item.elapsed = Qt.binding(() => surface.session.elapsed);
            item.phase = Qt.binding(() => surface.session.phase);
            item.bubbleAge = Qt.binding(() => surface.age);
            item.wrongAge = Qt.binding(() => surface.session.wrongAge);
            item.showControls = Qt.binding(() => surface.fieldsVisible);
            item.active = Qt.binding(() => surface.environment.outputsActive);
            item.reducedMotion = Qt.binding(() => surface.session.reducedMotion);
        }
    }
    // Independent ink and drops: a missing visual QML file or shader still takes passwords.
    Item {
        visible: surface.controls
        anchors.fill: parent
        Rectangle {
            x: geometry.fieldRect.x
            y: geometry.fieldRect.y
            width: surface.fieldWidth
            height: 56
            radius: 28
            opacity: surface.life
            color: LiquidPalette.flatDrop
            border.color: LiquidPalette.flatDropRim
            visible: !surface.glassReady
        }
        Rectangle {
            x: geometry.avatarRect.x
            y: geometry.avatarRect.y
            width: surface.avatarSize
            height: width
            radius: width / 2
            opacity: surface.avatarLife
            color: LiquidPalette.flatDrop
            border.color: LiquidPalette.flatDropRim
            visible: !surface.glassReady
        }
        Canvas {
            x: geometry.avatarRect.x
            y: geometry.avatarRect.y
            width: surface.avatarSize
            height: width
            opacity: Math.max(0, (surface.avatarLife - .4) / .6)
            onWidthChanged: requestPaint()
            onPaint: {
                const context = getContext("2d"), r = width / 2 - 5, cx = width / 2, cy = height / 2;
                context.reset();
                context.fillStyle = LiquidPalette.faintOnLight;
                context.beginPath();
                context.arc(cx, cy, r, 0, 2 * Math.PI);
                context.clip();
                context.beginPath();
                context.arc(cx, cy - r * .2, r * .3, 0, 2 * Math.PI);
                context.fill();
                context.beginPath();
                context.ellipse(cx - r * .62, cy + r * .28, r * 1.24, r);
                context.fill();
            }
        }
        Item {
            x: surface.width / 2 + surface.shake
            y: surface.height / 2
            opacity: Math.max(0, (surface.life - .4) / .6)
            Text {
                id: placeholder
                anchors.centerIn: parent
                text: surface.auth.usernameMode ? "Username" : surface.auth.prompt === "Password" ? "Password" : "Response"
                visible: surface.auth.dotCount === 0
                color: LiquidPalette.dimOnLight
                font.pixelSize: 15
                font.weight: Font.Medium
                textFormat: Text.PlainText
            }
            Text {
                id: username
                anchors.centerIn: parent
                width: Math.min(280, surface.fieldWidth - 40)
                text: surface.auth.usernameMode ? surface.auth.buffer : ""
                visible: surface.auth.usernameMode && surface.auth.dotCount > 0
                color: LiquidPalette.inkOnLight
                font.pixelSize: 15
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideLeft
                textFormat: Text.PlainText
            }
            Repeater {
                model: surface.auth.usernameMode ? 0 : Math.min(16, surface.auth.dotCount)
                Rectangle {
                    required property int index
                    readonly property real pop: surface.session.reducedMotion || surface.auth.checking || index !== Math.min(16, surface.auth.dotCount) - 1 ? 1 : 1 + .5 * Math.exp(-(surface.session.clock - surface.keyAt) * 14)
                    x: (index - (Math.min(16, surface.auth.dotCount) - 1) / 2) * 16 - width / 2
                    y: -height / 2
                    width: 9 * pop
                    height: width
                    radius: width / 2
                    color: LiquidPalette.inkOnLight
                    opacity: surface.auth.checking && !surface.session.reducedMotion ? .4 + .25 * Math.sin(surface.session.clock * 18) : 1
                }
            }
            Rectangle {
                x: surface.auth.dotCount ? (surface.auth.usernameMode ? Math.min(username.contentWidth, username.width) / 2 + 4 : (Math.min(16, surface.auth.dotCount) - 1) * 8 + 12) : -placeholder.width / 2 - 5
                y: -10
                width: 1.5
                height: 20
                color: LiquidPalette.inkOnLight
                visible: !surface.auth.checking && (surface.session.reducedMotion || surface.session.clock - surface.keyAt < .5 || (surface.session.clock - surface.keyAt) % 1.06 < .53)
            }
        }
        Text {
            x: Math.max(12, (surface.width - 440) / 2)
            y: surface.height / 2 - 49
            width: Math.min(440, surface.width - 24)
            text: surface.auth.usernameMode || surface.auth.prompt === "Password" ? "" : surface.auth.prompt
            opacity: surface.life
            color: LiquidPalette.dimOnLight
            font.pixelSize: 12
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Text {
            x: Math.max(12, (surface.width - 440) / 2)
            y: surface.height / 2 + 43
            width: Math.min(440, surface.width - 24)
            text: surface.auth.message || (surface.environment.capsLock ? "Caps Lock is on" : "")
            opacity: surface.life
            color: ["technical", "pam-error", "wrong", "username"].indexOf(surface.auth.messageKind) >= 0 ? LiquidPalette.dangerOnLight : LiquidPalette.dimOnLight
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        Text {
            x: Math.max(12, surface.width / 2 + 174)
            y: surface.height / 2 - height / 2
            text: surface.environment.layout
            color: LiquidPalette.dimOnLight
            opacity: surface.life
            font.pixelSize: 12
            textFormat: Text.PlainText
        }
    }
    Loader {
        anchors.fill: parent
        active: surface.controls
        source: "LockWordmark.qml"
        onLoaded: {
            item.dpr = Qt.binding(() => surface.dpr);
            item.clock = Qt.binding(() => surface.session.clock);
            item.elapsed = Qt.binding(() => surface.session.elapsed);
            item.phase = Qt.binding(() => surface.session.phase);
            item.idleMode = Qt.binding(() => surface.session.idleMode);
            item.introComplete = Qt.binding(() => surface.session.introComplete);
            item.active = Qt.binding(() => surface.environment.outputsActive);
            item.reducedMotion = Qt.binding(() => surface.session.reducedMotion);
        }
    }
    LockInput {
        id: input
        anchors.fill: parent
        auth: surface.auth
        onEngaged: surface.session.chooseOutput(surface.outputName, surface.height)
        onEdited: surface.keyAt = surface.session.clock
    }
    Connections {
        target: surface.Window.window
        function onFrameSwapped(): void {
            surface.session.painted(surface.outputName);
            if (surface.session.phase === "locked" && surface.session.surfaces[surface.outputName] < 2)
                surface.Window.window.update();
        }
    }
    Connections {
        target: surface.session
        function onPrivacyChanged(): void {
            if (surface.Window.window)
                surface.Window.window.update();
        }
    }
}
