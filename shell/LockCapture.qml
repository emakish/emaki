pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Optional pre-lock capture. Only ItemGrabResult objects are retained; their
// itemgrabber URLs never leave this process. Authentication does not depend on it.
Scope {
    id: capture
    property bool started: false
    property bool finished: false
    property bool collecting: false
    property int generation: 0
    property real deadlineAt: 0
    property var retained: ({})
    property var workers: ({})
    property var pending: ({})
    property var origins: ({})
    property bool notified: false
    signal ready

    function owns(object: var, name: string): bool {
        return Object.prototype.hasOwnProperty.call(object, name);
    }
    function urlFor(screen: var): string {
        // A new surface may bind before screensChanged prunes the old connector.
        // Identity must match at lookup time; its name alone is never authority.
        if (!screen || !owns(origins, screen.name) || origins[screen.name] !== screen)
            return "";
        return owns(retained, screen.name) ? String(retained[screen.name].url) : "";
    }
    function stopWorker(worker: var): void {
        if (!worker)
            return;
        try {
            worker.stop();
        } catch (_) {}
        try {
            worker.visible = false;
        } catch (_) {}
        try {
            worker.destroy();
        } catch (_) {}
    }
    function stopWorkers(): void {
        const old = workers;
        workers = {};
        for (const name of Object.keys(old))
            stopWorker(old[name]);
    }
    function connectWorker(worker: var, name: string, token: int): void {
        // The helper is intentionally loaded by URL so a visual import failure
        // cannot prevent acquisition of the real lock.
        try {
            worker.completed.connect(capture.complete);
        } catch (_) {
            complete(name, token, null);
        }
    }
    function finish(): void {
        if (finished)
            return;
        collecting = false;
        deadline.stop();
        stopWorkers();
        pending = {};
        finished = true;
        // stop() unmaps every helper and detaches ScreencopyView immediately;
        // defer acquisition one turn so their scheduled destruction can run too.
        Qt.callLater(function () {
            if (!capture.notified) {
                capture.notified = true;
                capture.ready();
            }
        });
    }
    function begin(screens: var): void {
        if (started || finished)
            return;
        started = true;
        collecting = true;
        ++generation;
        const initial = Object.create(null);
        for (const screen of screens) {
            if (screen && typeof screen.name === "string" && screen.name.length)
                initial[screen.name] = screen;
        }
        origins = initial;
        pending = Object.assign(Object.create(null), initial);
        deadlineAt = Date.now() + 300;
        deadline.start();
        if (!Object.keys(pending).length) {
            finish();
            return;
        }
        // A broken optional helper also falls back; never make it an entry-point import.
        const factory = Qt.createComponent("LockCapturePanel.qml");
        if (factory.status !== Component.Ready) {
            finish();
            return;
        }
        const token = generation;
        for (const name of Object.keys(initial)) {
            if (!collecting || token !== generation)
                break;
            if (Date.now() >= deadlineAt) {
                finish();
                break;
            }
            const worker = factory.createObject(capture, {
                screen: initial[name],
                outputName: name,
                generation: token
            });
            if (!worker) {
                complete(name, token, null);
                continue;
            }
            if (!collecting || token !== generation) {
                stopWorker(worker);
                continue;
            }
            const next = Object.assign(Object.create(null), workers);
            next[name] = worker;
            workers = next;
            connectWorker(worker, name, token);
        }
    }
    function complete(name: string, token: int, result: var): void {
        if (!collecting || token !== generation || !owns(pending, name))
            return;
        // The bound includes readback as well as first-frame arrival. A callback
        // queued after the deadline cannot extend pre-lock desktop exposure.
        if (Date.now() >= deadlineAt) {
            finish();
            return;
        }
        if (result && String(result.url).startsWith("itemgrabber:")) {
            const next = Object.assign(Object.create(null), retained);
            next[name] = result;
            retained = next;
        }
        const remaining = Object.assign(Object.create(null), pending);
        delete remaining[name];
        pending = remaining;
        if (!Object.keys(pending).length)
            finish();
    }
    function prune(screens: var): void {
        const live = Object.create(null);
        for (const screen of screens) {
            if (screen)
                live[screen.name] = screen;
        }
        const keep = Object.assign(Object.create(null), retained);
        const waiting = Object.assign(Object.create(null), pending);
        const sources = Object.assign(Object.create(null), origins);
        const helpers = Object.assign(Object.create(null), workers);
        for (const name of Object.keys(origins)) {
            // Replugging the same connector must not resurrect its previous frame.
            if (!owns(live, name) || live[name] !== origins[name]) {
                delete keep[name];
                delete waiting[name];
                delete sources[name];
                if (owns(helpers, name)) {
                    stopWorker(helpers[name]);
                    delete helpers[name];
                }
            }
        }
        retained = keep;
        pending = waiting;
        origins = sources;
        workers = helpers;
        if (collecting && !Object.keys(pending).length)
            finish();
    }
    function clear(): void {
        // Invalidates in-flight readbacks too. Calling this before begin skips
        // capture entirely, as needed for an immediate prepare-sleep request.
        ++generation;
        retained = {};
        origins = {};
        finish();
        // QImage owners are JS wrappers; collect after clearing receiver bindings
        // too, rather than keeping desktop pixels across a sleep until later GC.
        gc();
        Qt.callLater(gc);
    }
    Timer {
        id: deadline
        interval: 300
        onTriggered: capture.finish()
    }
    Component.onDestruction: {
        ++generation;
        retained = {};
        stopWorkers();
    }
}
