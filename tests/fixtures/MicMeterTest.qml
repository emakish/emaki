pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: root
    property int stage: 0
    property int updates: 0
    property int rapidSamples: 0
    SystemMeterPolicyTest {}
    function check(value: bool, message: string): void {
        if (!value)
            throw new Error(message);
    }
    Window {
        visible: true
        width: 400
        height: 2
        MicMeter {
            id: meter
            width: 352
            onShownWidthChanged: ++root.updates
        }
    }
    Component.onCompleted: {
        meter.level = .4;
        check(meter.shownWidth === 352 * .4, "first sample must be immediate");
        meter.level = .5;
        check(meter.shownWidth === 352 * .5, "second sample must be immediate without a timer");
        meter.level = 1;
        check(meter.shownWidth === 352, "a short peak must publish before the following sample");
        meter.level = .6;
        check(meter.shownWidth === 352 * .6, "every distinct sample must publish synchronously");
        step.start();
    }
    Timer {
        id: step
        interval: 80
        repeat: true
        onTriggered: {
            ++root.stage;
            if (root.stage === 1) {
                root.check(meter.shownWidth === 352 * .6, "no delayed geometry update");
                meter.level = 1.1;
                root.check(meter.shownWidth === 352, "first sample after idle is immediate");
            } else if (root.stage === 2) {
                root.updates = 0;
                meter.level = 1.2;
                meter.level = 1.3;
                root.check(root.updates === 0, "same visible clamped width must not redraw");
                meter.level = .4;
            } else if (root.stage === 3) {
                root.updates = 0;
                meter.level = .4 - 1e-7;
                meter.level = .4 + 1e-7;
                root.check(root.updates === 0, "sub-raster endpoint noise must not redraw");
            } else if (root.stage === 4) {
                root.check(root.updates === 0, "sub-raster noise must leave no pending redraw");
                meter.level = .3;
                meter.level = .8;
                meter.level = -1;
                root.check(meter.shownWidth === 0 && !meter.visible, "closing clears immediately");
            } else if (root.stage === 5) {
                root.check(meter.shownWidth === 0, "no pending update may return after closing");
                meter.level = .7;
                root.check(meter.shownWidth === 352 * .7 && meter.visible, "reopening is immediate");
                meter.width = 400;
            } else if (root.stage === 6) {
                root.check(meter.shownWidth === 280, "geometry changes retain the same exact ratio");
                root.updates = 0;
                rapid.start();
            } else {
                rapid.stop();
                root.check(root.rapidSamples > 0 && root.updates === root.rapidSamples, "every distinct rapid sample must publish, with no coalescing timer");
                console.log("MIC_METER_OK");
                Qt.quit();
            }
        }
    }
    Timer {
        id: rapid
        interval: 1
        repeat: true
        onTriggered: {
            ++root.rapidSamples;
            meter.level = root.rapidSamples % 2 ? .2 : .8;
            root.check(meter.shownWidth === meter.width * meter.level, "rapid sample must be immediate");
        }
    }
}
