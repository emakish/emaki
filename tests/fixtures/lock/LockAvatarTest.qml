// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// Drives the production AccountPicture and LockSurface against a fake AccountsService. The
// runner changes the picture after each "LOCK_AVATAR_WAITING <phase>" line on its stdin side.
ShellRoot {
    id: root
    property int index: -1
    property real since: 0
    property bool announced: false
    property var late: null
    readonly property var phases: [
        {
            name: "absent",
            expect: "silhouette"
        },
        {
            name: "present",
            expect: [255, 0, 0]
        },
        {
            name: "changed",
            expect: [0, 0, 255]
        },
        {
            name: "late-output",
            expect: [0, 0, 255],
            late: true
        },
        {
            name: "changed-in-place",
            expect: [0, 160, 0]
        },
        {
            name: "unreadable",
            expect: "silhouette"
        },
        {
            name: "corrupt",
            expect: "silhouette"
        },
        {
            name: "restored",
            expect: [0, 160, 0]
        },
        {
            name: "removed",
            expect: "silhouette"
        }
    ]
    function fail(message: string): void {
        console.log("LOCK_AVATAR_FAILED " + message);
        Qt.exit(1);
    }
    function find(item: Item, name: string): Item {
        if (!item)
            return null;
        if (item.objectName === name)
            return item;
        for (let i = 0; i < item.children.length; ++i) {
            const found = find(item.children[i], name);
            if (found)
                return found;
        }
        return null;
    }
    function pixel(canvas: Item, x: real, y: real): var {
        const data = canvas.getContext("2d").getImageData(x, y, 1, 1).data;
        return [data[0], data[1], data[2], data[3]];
    }
    function matches(canvas: Item, expect: var): bool {
        const centre = pixel(canvas, canvas.width / 2, canvas.height / 2);
        if (expect === "silhouette") {
            // The silhouette's head in the shell's faint ink, no picture. The ink follows the
            // theme (translucent dark on light, opaque on dark), so compare with it.
            const ink = LiquidPalette.faint, near = (a, b) => Math.abs(a - b * 255) < 8;
            return !canvas.showsPicture && near(centre[0], ink.r) && near(centre[1], ink.g) && near(centre[2], ink.b) && near(centre[3], ink.a);
        }
        const close = (a, b) => Math.abs(a - b) < 40;
        if (!canvas.showsPicture || centre[3] < 240 || !close(centre[0], expect[0]) || !close(centre[1], expect[1]) || !close(centre[2], expect[2]))
            return false;
        // Same round shape as the silhouette: clear outside the circle, the picture just inside its rim.
        const corner = pixel(canvas, 3, 3), rim = pixel(canvas, canvas.width / 2, 7);
        return corner[3] === 0 && rim[3] > 240 && close(rim[0], expect[0]) && close(rim[2], expect[2]);
    }
    LockAuth {
        id: authController
    }
    LockEnvironment {
        id: metadata
    }
    LockSession {
        id: sessionController
        auth: authController
        active: false
        reducedMotion: true
    }
    AccountPicture {
        id: picture
    }
    // The login screen: a named account through FindUserByName, nobody at the name step,
    // and no file another account does not own ("bo" names one of "ada"'s pictures).
    AccountPicture {
        id: named
        user: "ada"
        isolateHelper: true
    }
    AccountPicture {
        id: foreign
        user: "bo"
        isolateHelper: true
    }
    AccountPicture {
        id: nameStep
        user: ""
        active: false
    }
    Window {
        id: window
        visible: true
        width: 1000
        height: 700
        LockSurface {
            id: lockSurface
            anchors.fill: parent
            session: sessionController
            auth: authController
            environment: metadata
            avatarUrl: picture.url
        }
    }
    // An output added while locked: its surface starts with the picture already in Qt's cache.
    Component {
        id: lateOutput
        Window {
            property alias surface: lateSurface
            visible: true
            width: 600
            height: 400
            LockSurface {
                id: lateSurface
                anchors.fill: parent
                session: sessionController
                auth: authController
                environment: metadata
                avatarUrl: picture.url
            }
        }
    }
    Timer {
        interval: 50
        repeat: true
        running: true
        onTriggered: {
            const now = Date.now();
            const phase = root.phases[Math.max(0, root.index)];
            if (phase.late && !root.late)
                root.late = lateOutput.createObject(root);
            const canvas = root.find(phase.late ? root.late.surface : lockSurface, "lock-avatar");
            if (!canvas)
                return root.fail("no avatar canvas");
            if (phase.late && canvas.width < 90)
                return;
            if (root.index < 0) {
                if (canvas.width < 90)
                    return;
                root.index = 0;
                root.since = now;
                root.announced = false;
            }
            if (!root.announced) {
                console.log("LOCK_AVATAR_WAITING " + phase.name);
                root.announced = true;
                root.since = now;
                return;
            }
            // The helper polls every 1.5 s; "absent"-like phases must also hold, not just pass once.
            const settle = phase.expect === "silhouette" ? 3200 : 0;
            if (now - root.since >= settle && root.matches(canvas, phase.expect)) {
                console.log("LOCK_AVATAR_PASS " + phase.name + (phase.expect === "silhouette" ? " shows the silhouette" : " shows the picture inside the drop's rim"));
                if (phase.name === "changed-in-place") {
                    if (!named.path.startsWith("/") || named.path === picture.path)
                        return root.fail("a named account's picture through FindUserByName: " + named.path);
                    if (nameStep.path !== "")
                        return root.fail("the name step shows no account's picture");
                    if (foreign.path !== "")
                        return root.fail("a picture file another account owns: " + foreign.path);
                    console.log("LOCK_AVATAR_PASS the login screen reads the chosen account's picture, none at the name step and none from a file another account owns");
                    named.user = "bo"; // The login screen switches accounts.
                }
                if (phase.name === "restored") {
                    if (named.path !== "")
                        return root.fail("a switched account keeps the previous account's picture: " + named.path);
                    console.log("LOCK_AVATAR_PASS switching accounts on the login screen drops the previous picture");
                }
                if (++root.index === root.phases.length) {
                    console.log("LOCK_AVATAR_COMPLETE");
                    Qt.quit();
                    return;
                }
                root.announced = false;
            } else if (now - root.since > 9000) {
                const centre = root.pixel(canvas, canvas.width / 2, canvas.height / 2);
                root.fail(phase.name + ": centre " + JSON.stringify(centre) + " showsPicture " + canvas.showsPicture + " url " + lockSurface.avatarUrl);
            }
        }
    }
}
