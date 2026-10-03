pragma ComponentBehavior: Bound
import QtQuick

// Shared by secure input layout and optional glass: one set of motion equations.
QtObject {
    id: geometry
    property real width: 1
    property real height: 1
    property string phase: "pour"
    property real elapsed: 0
    property real clock: 0
    property real bubbleAge: -1
    property real wrongAge: 1000
    property bool showControls: true
    property bool reducedMotion: false
    readonly property real pour: Math.max(0, Math.min(1, elapsed / .9))
    readonly property real drain: Math.max(0, Math.min(1, elapsed))
    readonly property real edgePosition: phase === "finished" ? height + 70 : phase === "pour" && !reducedMotion ? -60 + (height + 130) * (.5 - .5 * Math.cos(Math.PI * pour)) : phase === "drain" ? -70 + (height + 140) * (.35 * drain + .65 * drain * drain) : 100000
    // The wave settles from 28 to 4 during the final 20% of the pour.
    readonly property real waveAmplitude: reducedMotion ? 0 : phase === "pour" ? 24 * (1 - Math.max(0, Math.min(1, (pour - .8) / .2))) + 4 : phase === "drain" ? 24 : 0
    readonly property real melt: phase === "melt" ? Math.max(0, Math.min(1, elapsed / .22)) : 0
    readonly property real fieldRise: reducedMotion ? 1 : Math.max(0, Math.min(1, bubbleAge / .5))
    readonly property real avatarRise: reducedMotion ? 1 : Math.max(0, Math.min(1, (bubbleAge - .06) / .5))
    readonly property real fieldLife: !showControls || phase === "drain" || phase === "finished" ? 0 : phase === "melt" ? 1 - melt : bubbleAge < 0 ? 0 : Math.max(0, Math.min(1, fieldRise / .35))
    readonly property real avatarLife: !showControls || phase === "drain" || phase === "finished" ? 0 : phase === "melt" ? 1 - melt : bubbleAge < 0 ? 0 : Math.max(0, Math.min(1, avatarRise / .35))
    readonly property real fieldWidth: phase === "melt" ? 320 - 80 * melt : 56 + 264 * outBack(fieldRise)
    readonly property real avatarSize: phase === "melt" ? 96 - 24 * melt : 36 + 60 * outBack(avatarRise)
    readonly property real shake: !reducedMotion && wrongAge >= 0 && wrongAge < .6 ? 11 * Math.exp(-wrongAge * 7) * Math.sin(wrongAge * 46) : 0
    readonly property vector4d fieldRect: Qt.vector4d(width / 2 - fieldWidth / 2 + shake, height / 2 - 28, fieldWidth, 56)
    readonly property vector4d avatarRect: Qt.vector4d(width / 2 - avatarSize / 2, height / 2 - 102 - avatarSize / 2, avatarSize, avatarSize)
    function outBack(t: real): real {
        return 1 + 2.4 * Math.pow(t - 1, 3) + 1.4 * Math.pow(t - 1, 2);
    }
}
