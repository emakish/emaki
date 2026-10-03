pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: root
    property int stage: 0
    property real stageStarted: Date.now()
    property var manager: null
    property int readiness: 0
    property bool readyStopped: false
    property var witnesses: []
    property var screens: []
    property var oldResult: null
    property int oldGeneration: -1
    property bool sampleReady: false
    property bool sampleGood: false
    property bool sampledAfterDestroy: false
    property string fixtureMode: Quickshell.env("LOCK_CAPTURE_FIXTURE")
    function check(ok: bool, name: string): void {
        if (!ok)
            throw new Error(name);
        console.log("LOCK_CAPTURE_PASS " + name);
    }
    function waitFor(ok: bool, name: string): bool {
        if (!ok && Date.now() - stageStarted > 5000)
            throw new Error("Deadline waiting for " + name + " at stage " + stage);
        return ok;
    }
    function nextStage(): void {
        stage++;
        stageStarted = Date.now();
    }
    function makeScreen(name: string, delay: int, color: string): var {
        const witness = {
            created: 0,
            stopped: 0,
            destroyed: 0
        };
        witnesses.push(witness);
        return {
            name: name,
            width: 32,
            height: 32,
            devicePixelRatio: 1,
            delay: delay,
            color: color,
            witness: witness
        };
    }
    function fresh(): void {
        first.source = "";
        second.source = "";
        if (manager !== null) {
            manager.clear();
            manager.destroy();
        }
        readiness = 0;
        readyStopped = false;
        witnesses = [];
        screens = [];
        sampleReady = false;
        sampleGood = false;
        manager = controller.createObject(root);
        const current = manager;
        manager.ready.connect(() => {
            if (root.manager === current) {
                root.readyStopped = !current.collecting && Object.keys(current.workers).length === 0 && root.allStopped();
                root.readiness++;
            }
        });
    }
    function allStopped(): bool {
        return witnesses.every(w => w.created === w.stopped);
    }
    function allDestroyed(): bool {
        return witnesses.every(w => w.created === w.destroyed);
    }
    Component {
        id: controller
        LockCapture {}
    }
    FloatingWindow {
        visible: true
        implicitWidth: 64
        implicitHeight: 32
        color: "black"
        Image {
            id: first
            width: 32
            height: 32
        }
        Image {
            id: second
            x: 32
            width: 32
            height: 32
        }
        Canvas {
            id: sampler
            anchors.fill: parent
            onPaint: {
                if (first.status !== Image.Ready || second.status !== Image.Ready)
                    return;
                const context = getContext("2d");
                context.clearRect(0, 0, width, height);
                context.drawImage(first, 0, 0, 32, 32);
                context.drawImage(second, 32, 0, 32, 32);
                const data = context.getImageData(0, 0, 64, 32).data;
                function pixel(x, y, red, green, blue) {
                    const i = (y * 64 + x) * 4;
                    return data[i] === red && data[i + 1] === green && data[i + 2] === blue && data[i + 3] === 255;
                }
                root.sampleGood = pixel(16, 16, 255, 0, 0) && pixel(48, 16, 0, 0, 255) && pixel(2, 2, 0, 255, 0) && pixel(34, 2, 0, 255, 0) && pixel(30, 30, 255, 255, 0) && pixel(62, 30, 255, 255, 0);
                root.sampledAfterDestroy = root.allDestroyed();
                root.sampleReady = true;
            }
        }
    }
    function step(): void {
        switch (stage) {
        case 0:
            fresh();
            screens = [makeScreen("A", 30, "#ff0000"), makeScreen("B", 40, "#0000ff")];
            manager.begin(screens);
            nextStage();
            break;
        case 1:
            if (!waitFor(manager.finished && readiness === 1 && allDestroyed(), "bounded preflight and destroyed helper windows"))
                return;
            check(!manager.collecting && Object.keys(manager.workers).length === 0 && allStopped() && readyStopped, "all helper windows stop before preflight readiness");
            if (fixtureMode !== "normal") {
                check(manager.urlFor(screens.find(s => s.name === "A")).toString() === "" && manager.urlFor(screens.find(s => s.name === "B")).toString() === "", fixtureMode + " optional helper reaches wallpaper fallback");
                manager.clear();
                manager.begin(screens);
                check(readiness === 1 && Object.keys(manager.retained).length === 0, fixtureMode + " helper cannot restart capture after readiness");
                console.log("LOCK_CAPTURE_COMPLETE");
                Qt.quit();
                return;
            }
            check(Object.keys(manager.retained).length === 2, "two outputs retain their own in-memory results");
            check(first.sourceSize.width === 0 || first.source.toString() === "", "destination has not requested captured pixels during preflight");
            first.source = manager.urlFor(screens.find(s => s.name === "A"));
            second.source = manager.urlFor(screens.find(s => s.name === "B"));
            nextStage();
            break;
        case 2:
            if (!waitFor(first.status === Image.Ready && second.status === Image.Ready, "memory URLs loaded in a different FloatingWindow"))
                return;
            check(first.sourceSize.width === 32 && first.sourceSize.height === 32 && second.sourceSize.width === 32, "cross-window Images load the full synthetic capture");
            sampler.requestPaint();
            nextStage();
            break;
        case 3:
            if (!waitFor(sampleReady, "Canvas pixel sample"))
                return;
            check(sampleGood && sampledAfterDestroy, "retained ItemGrabResult keeps exact pixels after source windows are destroyed");
            oldResult = manager.retained.A;
            oldGeneration = manager.generation;
            manager.prune([screens[1]]);
            check(manager.urlFor(screens.find(s => s.name === "A")).toString() === "" && manager.urlFor(screens.find(s => s.name === "B")).toString() !== "", "output removal drops only its matching result");
            manager.complete("A", oldGeneration, oldResult);
            check(manager.urlFor(screens.find(s => s.name === "A")).toString() === "", "removed output ignores a late callback");
            const hot = makeScreen("HOT", 20, "#ffffff");
            manager.prune([screens[1], hot]);
            manager.begin([screens[1], hot]);
            check(hot.witness.created === 0 && manager.urlFor(screens.find(s => s.name === "HOT")).toString() === "" && readiness === 1, "hotplug after locking never starts a desktop capture");
            const replacement = makeScreen("B", 20, "#ffffff");
            check(manager.urlFor(replacement) === "" && manager.urlFor(screens[1]) !== "", "same-name hotplug lookup rejects stale capture before prune runs");
            manager.prune([replacement]);
            manager.complete("B", oldGeneration, oldResult);
            check(manager.urlFor(screens.find(s => s.name === "B")).toString() === "" && replacement.witness.created === 0, "same connector name with a new screen never resurrects an old capture");
            manager.clear();
            manager.complete("B", oldGeneration, oldResult);
            check(Object.keys(manager.retained).length === 0 && readiness === 1, "prepare-sleep cleanup drops references and invalidates late callbacks");
            first.source = "";
            second.source = "";
            fresh();
            screens = [makeScreen("A", 30, "#ff0000"), makeScreen("SLOW", 5000, "#0000ff")];
            manager.begin(screens);
            oldGeneration = manager.generation;
            manager.complete("SLOW", oldGeneration - 1, oldResult);
            manager.complete("UNKNOWN", oldGeneration, oldResult);
            check(manager.urlFor(screens.find(s => s.name === "SLOW")).toString() === "" && manager.urlFor(screens.find(s => s.name === "UNKNOWN")).toString() === "" && manager.collecting, "stale generation and unknown output cannot answer a pending capture");
            nextStage();
            break;
        case 4:
            if (!waitFor(manager.finished && allDestroyed(), "partial capture deadline"))
                return;
            check(Date.now() - stageStarted >= 250 && Date.now() - stageStarted < 1000, "one shared 300 ms deadline bounds a pending capture/readback");
            check(manager.urlFor(screens.find(s => s.name === "A")).toString() !== "" && manager.urlFor(screens.find(s => s.name === "SLOW")).toString() === "" && readiness === 1, "partial timeout preserves only the completed output");
            manager.complete("SLOW", oldGeneration, oldResult);
            check(manager.urlFor(screens.find(s => s.name === "SLOW")).toString() === "" && readiness === 1, "readback arriving after the deadline cannot populate a locked screen");
            fresh();
            screens = [makeScreen("REMOVE", 5000, "#ff0000"), makeScreen("KEEP", 50, "#0000ff")];
            manager.begin(screens);
            oldGeneration = manager.generation;
            manager.prune([screens[1]]);
            manager.complete("REMOVE", oldGeneration, oldResult);
            nextStage();
            break;
        case 5:
            if (!waitFor(manager.finished && allDestroyed(), "removed preflight output"))
                return;
            check(manager.urlFor(screens.find(s => s.name === "REMOVE")).toString() === "" && manager.urlFor(screens.find(s => s.name === "KEEP")).toString() !== "" && readiness === 1, "output removed during capture cannot block readiness or repopulate storage");
            fresh();
            screens = [makeScreen("PENDING", 5000, "#ff0000")];
            manager.begin(screens);
            oldGeneration = manager.generation;
            manager.clear();
            manager.complete("PENDING", oldGeneration, oldResult);
            check(manager.finished && !manager.collecting && Object.keys(manager.retained).length === 0, "sleep before readback cancels preflight and proceeds to safe lock");
            nextStage();
            break;
        case 6:
            if (!waitFor(allDestroyed() && readiness === 1, "cancelled helper destruction and readiness"))
                return;
            fresh();
            manager.clear();
            const never = makeScreen("NEVER", 20, "#ff0000");
            manager.begin([never]);
            check(manager.finished && never.witness.created === 0, "sleep before begin permanently skips optional capture");
            nextStage();
            break;
        case 7:
            if (!waitFor(readiness === 1, "early sleep readiness"))
                return;
            fresh();
            screens = [makeScreen("BAD", 5000, "#ff0000")];
            manager.begin(screens);
            manager.complete("BAD", manager.generation, {
                url: "file:///no-capture-file"
            });
            nextStage();
            break;
        case 8:
            if (!waitFor(manager.finished && allDestroyed(), "invalid result fallback"))
                return;
            check(manager.urlFor(screens.find(s => s.name === "BAD")).toString() === "" && Object.keys(manager.retained).length === 0, "file URLs are never admitted to captured-frame storage");
            fresh();
            screens = [makeScreen("BLOCKED", 5000, "#ff0000")];
            manager.begin(screens);
            // Model a queued readback processed after a busy UI turn, before
            // the delayed Timer gets to run. Wall time must still win.
            const lateAt = Date.now() + 320;
            while (Date.now() < lateAt) {}
            manager.complete("BLOCKED", manager.generation, oldResult);
            check(manager.finished && manager.urlFor(screens.find(s => s.name === "BLOCKED")).toString() === "", "late readback cannot beat an overdue deadline timer");
            nextStage();
            break;
        case 9:
            if (!waitFor(readiness === 1 && allDestroyed(), "overdue capture cleanup"))
                return;
            manager.clear();
            oldResult = null;
            console.log("LOCK_CAPTURE_COMPLETE");
            Qt.quit();
            break;
        }
    }
    Timer {
        interval: 10
        running: true
        repeat: true
        onTriggered: {
            try {
                root.step();
            } catch (error) {
                console.error("LOCK_CAPTURE_FAILED " + error);
                Qt.exit(1);
            }
        }
    }
}
