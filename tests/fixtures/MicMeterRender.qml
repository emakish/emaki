pragma ComponentBehavior: Bound
import QtQuick

// A cheap real-rendered production meter isolates sample delivery from llvmpipe's
// full-screen glass cost. It measures callback-to-geometry updates, not mic latency.
Item {
    id: root
    width: 400
    height: 24
    property real level: .4
    property string samples: "idle"
    property int sampleCount: 0
    property int meterUpdates: 0
    property int startMilliseconds: 1
    property int sampleMilliseconds: 21
    readonly property bool ready: true
    readonly property var renderStats: ({
            sample_count: sampleCount,
            meter_updates: meterUpdates
        })
    Rectangle {
        anchors.fill: parent
        color: "#eee4ed"
    }
    MicMeter {
        x: 24
        y: 11
        width: 352
        level: root.level
        color: "#e67947"
        onShownWidthChanged: ++root.meterUpdates
    }
    Timer {
        interval: root.startMilliseconds
        running: root.samples !== "idle"
        onTriggered: samples.start()
    }
    Timer {
        id: samples
        interval: root.sampleMilliseconds
        repeat: true
        onTriggered: {
            ++root.sampleCount;
            if (root.samples === "varying")
                root.level = .5 + Math.sin(root.sampleCount * .13) * .45;
            else if (root.samples === "clamped")
                root.level = 1.1 + (root.sampleCount % 2) * .1;
            else if (root.samples === "constant")
                root.level = .4;
            else if (root.samples === "microjitter")
                root.level = .4 + (root.sampleCount % 2 ? 1e-7 : -1e-7);
        }
    }
}
