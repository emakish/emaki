//@ pragma AppId emaki-install-test
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import Quickshell
import Quickshell.Io
import "FakeNiri.js" as FakeNiri
import ".." as UI

ShellRoot {
    id: test
    property string screenName: Quickshell.env("EMAKI_INSTALLER_SCREEN") || "welcome"
    property bool prepared: false
    property bool captured: false
    property string screenshot: Quickshell.env("EMAKI_INSTALLER_SCREENSHOT")
    function findItem(item: Item, name: string): Item {
        if (item.objectName === name)
            return item;
        for (const child of item.children) {
            const found = findItem(child, name);
            if (found)
                return found;
        }
        return null;
    }
    function checkLayout(): bool {
        const footer = findItem(content, "installerFooter");
        const body = findItem(content, "installerBody") as C.ScrollView;
        const copy = findItem(content, "installerCopy") as Text;
        if (!footer || !body || !copy)
            return false;
        const bottom = footer.mapToItem(content, footer.width, footer.height);
        const top = footer.mapToItem(content, 0, 0);
        const bodyBottom = body.mapToItem(content, 0, body.height);
        if (top.x < 0 || top.y < 0 || bottom.x > content.width || bottom.y > content.height || body.height <= 0 || bodyBottom.y > top.y)
            return false;
        if (Quickshell.env("EMAKI_INSTALLER_SCROLL_BOTTOM") === "1") {
            const flick = body.contentItem as Flickable;
            if (!flick || flick.contentHeight <= flick.height)
                return false;
            flick.contentY = flick.contentHeight - flick.height;
            console.log("SCROLL_OK " + flick.contentY);
        }
        console.log("LAYOUT_OK " + content.width + "x" + content.height);
        if (Quickshell.env("EMAKI_INSTALLER_ISO_FONTS") === "1") {
            if (copy.fontInfo.family !== "Adwaita Sans")
                return false;
            console.log("FONT_OK " + copy.fontInfo.family);
        }
        return true;
    }
    // The hibernation screens get a second frame scrolled to the hibernation checkbox, which follows the mode choices.
    readonly property string modeTarget: ({
            "disk-hibernation": "hibernationCheck",
            "manual-hibernation": "hibernationCheck",
            "alongside-hibernation": "hibernationCheck"
        })[screenName] || ""
    function scrollToMode(): bool {
        const body = findItem(content, "installerBody") as C.ScrollView;
        const flick = body.contentItem as Flickable;
        const choice = findItem(content, modeTarget);
        if (!flick || !choice || !choice.visible)
            return false;
        const top = choice.mapToItem(flick.contentItem, 0, 0).y;
        flick.contentY = Math.max(0, Math.min(top - 12, flick.contentHeight - flick.height));
        const area = choice.mapToItem(body, 0, 0, choice.width, choice.height);
        if (area.y < 0 || area.y + area.height > body.height + 0.5)
            return false;
        console.log("TARGET_VISIBLE " + modeTarget);
        return true;
    }
    UI.InstallerController {
        id: controller
        mockTransport: true
        catalog: ({
                layouts: [
                    {
                        layout: "us",
                        variant: "",
                        label: "English (US)"
                    },
                    {
                        layout: "gb",
                        variant: "",
                        label: "English (UK)"
                    },
                    {
                        layout: "de",
                        variant: "",
                        label: "German"
                    },
                    {
                        layout: "ru",
                        variant: "",
                        label: "Russian"
                    },
                    {
                        layout: "us",
                        variant: "dvorak",
                        label: "English (Dvorak)"
                    }
                ],
                zones: ["UTC", "America/New_York", "Asia/Kathmandu", "Asia/Tokyo", "Australia/Sydney", "Europe/Berlin", "Europe/Lisbon", "Europe/London"],
                trial: false
            })
        network: ({
                wired: true,
                networks: [
                    {
                        ssid: "HomeNet",
                        bssid: "00:11:22:33:44:55",
                        device: "wlan0",
                        strength: 91,
                        security: "WPA2",
                        connected: false
                    },
                    {
                        ssid: "Cafe Guest",
                        bssid: "00:11:22:33:44:66",
                        device: "wlan0",
                        strength: 67,
                        security: "",
                        connected: false
                    }
                ]
            })
        onOutbound: message => mock.write(JSON.stringify(message) + "\n")
        // The live-keyboard screens: niri runs every list the window writes, except on
        // live-keyboard-failed, where it never loads the file.
        property var niri: FakeNiri.create(["us"])
        onKeyboardOutbound: request => Qt.callLater(function () {
                controller.keyboardReceive(FakeNiri.answer(controller.niri, controller.catalog.layouts, request));
            })
    }
    // Copied to RAM: no worker answers, and the catalog says the boot medium is gone.
    readonly property bool noWorker: screenName === "welcome-no-boot-medium"
    Component.onCompleted: if (noWorker) {
        controller.helperOp = "catalog";
        controller.helperResult(Object.assign({}, controller.catalog, {
            ok: true,
            boot_medium: false
        }));
        controller.lost();
        shot.restart();
    }
    Process {
        id: mock
        // error-real is served by the real installer controller and worker (real-worker.py).
        command: test.screenName === "error-real" ? ["python3", "-I", "-B", Qt.resolvedUrl("real-worker.py").toString().replace("file://", "")] : ["python3", "-I", "-B", Qt.resolvedUrl("mock-worker.py").toString().replace("file://", ""), "--stdio", "--screen", test.screenName, "--delay", "0"]
        stdinEnabled: true
        running: !test.noWorker
        onStarted: controller.send("hello", {
            proto: 1
        })
        stdout: SplitParser {
            onRead: data => {
                const message = JSON.parse(data);
                controller.receive(message);
                if (message.type === "inventory" && !test.prepared) {
                    test.prepared = true;
                    controller.diskId = test.screenName === "disk-none" ? "" : "/dev/vda";
                    if (test.screenName === "alongside" || test.screenName === "alongside-review")
                        controller.mode = "alongside";
                    controller.encryption = "none";
                    controller.fullName = "Demo User";
                    controller.login = "demo";
                    controller.layouts = ["us", "ru"];
                    controller.media = ["/run/media/live/LOGS"];
                    if (["review", "alongside-review", "review-encrypted", "plan-errors", "install", "install-signatures", "install-updates", "done", "done-warning", "done-no-package-lists", "error", "error-details", "error-real"].indexOf(test.screenName) >= 0) {
                        if (test.screenName === "review-encrypted") {
                            controller.encryption = "encrypted";
                            controller.hibernation = true;
                        }
                        const secret = "fixture-" + Date.now();
                        controller.plan(secret, secret);
                    } else {
                        if (test.screenName.indexOf("encryption") === 0) {
                            controller.encryption = test.screenName === "encryption" ? "" : test.screenName === "encryption-none" ? "none" : "encrypted";
                            controller.encryptionPassword = ["encryption-separate", "encryption-separate-empty", "encryption-manual", "encryption-mismatch", "encryption-invalid", "encryption-revealed", "encryption-caps", "encryption-numlock"].indexOf(test.screenName) >= 0 ? "separate" : "account";
                            controller.hibernation = true;
                            if (test.screenName === "encryption-manual")
                                controller.mode = "manual";
                            if (test.screenName === "encryption-alongside")
                                controller.mode = "alongside";
                            controller.step = "encryption";
                            if (controller.encryptionPassword === "separate" && test.screenName !== "encryption-separate-empty") {
                                controller.diskPassword = test.screenName === "encryption-invalid" ? "\u00e9" : "disposable-fixture";
                                controller.diskConfirmation = test.screenName === "encryption-mismatch" ? "different" : "disposable-fixture";
                            }
                            if (test.screenName === "encryption-revealed") {
                                test.findItem(content, "diskPassword").revealed = true;
                                test.findItem(content, "diskConfirmation").revealed = true;
                            }
                            if (test.screenName === "encryption-caps" || test.screenName === "encryption-numlock") {
                                // Caps Lock on while the disk password has focus.
                                test.findItem(content, "diskPassword").forceActiveFocus();
                                controller.capsLock = true;
                                // Num Lock as well: its line comes under the Caps Lock line.
                                controller.numLock = test.screenName === "encryption-numlock";
                            }
                        } else if (test.screenName.endsWith("-hibernation")) {
                            controller.mode = test.screenName.split("-")[0] === "disk" ? "erase" : test.screenName.split("-")[0];
                            controller.hibernation = true;
                            controller.step = "disk";
                        } else if (test.screenName.indexOf("timezone") === 0) {
                            controller.timezone = "Europe/Berlin";
                            controller.step = "timezone";
                            if (test.screenName !== "timezone") {
                                const search = test.findItem(content, "zoneSearch") as C.TextField;
                                search.text = test.screenName === "timezone-empty" ? "no-such-region" : "berlin";
                                search.forceActiveFocus();
                            }
                        } else if (test.screenName === "you-empty") {
                            // Continue attempted on an empty form: each rule under its own field.
                            controller.fullName = "";
                            controller.login = "";
                            controller.step = "you";
                            content.requestPlan();
                        } else if (test.screenName === "you-console") {
                            // English (US) and Russian: the text console has the us map, which types no Cyrillic
                            // and no no-break space (named, since a list cannot show it).
                            controller.step = "you";
                            (test.findItem(content, "userPassword") as C.TextField).text = "Пароль 2026";
                            (test.findItem(content, "confirmPassword") as C.TextField).text = "Пароль 2026";
                        } else if (test.screenName === "you-caps" || test.screenName === "you-numlock") {
                            // Caps Lock on while the account password has focus, the console warning below it.
                            controller.step = "you";
                            (test.findItem(content, "userPassword") as C.TextField).text = "Пароль-2026";
                            test.findItem(content, "userPassword").forceActiveFocus();
                            controller.capsLock = true;
                            controller.numLock = test.screenName === "you-numlock";
                        } else if (test.screenName === "you-paste") {
                            // Ctrl+V in the account password: refused, the sentence under the buttons.
                            controller.step = "you";
                            (test.findItem(content, "userPassword") as C.TextField).text = "Zebra";
                            test.findItem(content, "userPassword").forceActiveFocus();
                            controller.secretPasted(false);
                        } else if (test.screenName.indexOf("live-keyboard") === 0) {
                            // German chosen; the live session runs it, the disk password field runs English (US).
                            // live-keyboard-second: Super+Space is pressed in the account password field.
                            controller.layouts = test.screenName === "live-keyboard-second" ? ["us", "ru"] : ["de"];
                            if (test.screenName === "live-keyboard-failed") {
                                controller.niri.mode = "stuck";
                                controller.keyboardConfirmMs = 100;
                            }
                            controller.catalog = Object.assign({}, controller.catalog, {
                                trial: true
                            });
                            if (test.screenName !== "live-keyboard-encryption") {
                                controller.step = "you";
                                test.findItem(content, "userPassword").forceActiveFocus();
                            } else {
                                controller.encryption = "encrypted";
                                controller.encryptionPassword = "separate";
                                controller.step = "encryption";
                                test.findItem(content, "diskPassword").forceActiveFocus();
                            }
                        } else if (test.screenName.indexOf("software") === 0) {
                            controller.software = test.screenName === "software-minimal" ? "minimal" : "rich";
                            controller.step = "software";
                        } else
                            controller.step = ["manual", "manual-empty", "alongside", "disk-mbr", "disk-none"].indexOf(test.screenName) >= 0 ? "disk" : test.screenName === "welcome-bios" ? "welcome" : test.screenName;
                        if (test.screenName === "manual-empty") {
                            // A disk with a partition table and no partitions yet.
                            controller.mode = "manual";
                            controller.manualAssignments = true;
                        }
                        if (test.screenName === "manual") {
                            controller.mode = "manual";
                            controller.manualAssignments = true;
                            controller.mounts = [
                                {
                                    partition_id: "/dev/vda1",
                                    mountpoint: "/efi",
                                    fs: "vfat",
                                    format: false,
                                    subvolume: null
                                },
                                {
                                    partition_id: "/dev/vda2",
                                    mountpoint: "/",
                                    fs: "btrfs",
                                    format: true,
                                    subvolume: null
                                }
                            ];
                        }
                        shot.restart();
                    }
                } else if (message.type === "plan_ack") {
                    if (["install", "install-signatures", "install-updates", "done", "done-warning", "done-no-package-lists", "error", "error-details", "error-real"].indexOf(test.screenName) >= 0) {
                        controller.agreed = true;
                        controller.confirm();
                    } else
                        shot.restart();
                } else if (message.type === "done" || message.type === "error" || (message.type === "state" && test.screenName === "install") || (message.type === "progress" && test.screenName.indexOf("install-") === 0)) {
                    if (test.screenName === "error-details")
                        (test.findItem(content, "errorDetailsToggle") as C.CheckBox).toggle();
                    shot.restart();
                }
            }
        }
    }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("EMAKI_INSTALLER_WIDTH") || 1180)
        implicitHeight: Number(Quickshell.env("EMAKI_INSTALLER_HEIGHT") || 820)
        color: "#fff8f3"
        UI.InstallerView {
            id: content
            anchors.fill: parent
            controller: controller
        }
    }
    Timer {
        id: shot
        interval: 650
        onTriggered: {
            // A live-keyboard frame shows the switch once it has settled.
            if (test.screenName.indexOf("live-keyboard") === 0 && controller.keyboardSwitching) {
                shot.restart();
                return;
            }
            if (test.screenName === "live-keyboard-second" && controller.keyboardMessage === "") {
                controller.niri.current = 1;
                shot.restart();
                return;
            }
            if (!test.checkLayout()) {
                console.error("Layout or font check failed");
                Qt.quit();
                return;
            }
            if (test.screenName === "error-details") {
                // The opened log is inside the visible part of the step body.
                const body = test.findItem(content, "installerBody");
                const details = test.findItem(content, "errorDetailsBox");
                const area = details ? details.mapToItem(body, 0, 0, details.width, details.height) : null;
                if (!area || !details.visible || area.y < 0 || area.y + area.height > body.height + 0.5) {
                    console.error("Error details not visible: " + (area ? area.y + "+" + area.height + " in " + body.height : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("DETAILS_VISIBLE " + area.y + "+" + area.height + " in " + body.height);
            }
            if (test.screenName === "disk") {
                // The mode choice follows the disk list and is on screen without scrolling.
                const body = test.findItem(content, "installerBody");
                const erase = test.findItem(content, "eraseChoice");
                const area = erase ? erase.mapToItem(body, 0, 0, erase.width, erase.height) : null;
                if (!area || !erase.visible || area.y < 0 || area.y + area.height > body.height + 0.5) {
                    console.error("Erase disk is not in view: " + (area ? area.y + "+" + area.height + " in " + body.height : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("ERASE_IN_VIEW " + area.y + "+" + area.height + " in " + body.height);
            }
            if (test.screenName === "manual-empty") {
                const hint = test.findItem(content, "noPartitions") as Text;
                if (!hint || !hint.visible) {
                    console.error("No hint on the empty manual page: " + (hint ? hint.visible : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("NO_PARTITIONS_HINT " + hint.text);
            }
            if (test.screenName === "disk-none") {
                const reason = test.findItem(content, "blockedReason") as Text;
                if (!reason || !reason.visible || !reason.text) {
                    console.error("No reason under the grey Continue: " + (reason ? reason.visible + " " + reason.text : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("BLOCKED_REASON " + reason.text);
            }
            if (test.screenName.indexOf("install-") === 0) {
                const status = test.findItem(content, "phaseStatus") as Text;
                if (!status || !status.visible) {
                    console.error("No status in the current phase row");
                    Qt.quit();
                    return;
                }
                console.log("PHASE_STATUS " + status.text);
            }
            if (test.noWorker) {
                const notice = test.findItem(content, "installerNotice") as Text;
                if (!notice || !notice.visible || notice.parent.height < notice.implicitHeight) {
                    console.error("No notice instead of connecting: " + (notice ? notice.visible + " " + notice.text : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("NOTICE " + notice.text);
            }
            if (test.screenName === "live-keyboard-failed") {
                const problem = test.findItem(content, "keyboardProblem") as Text;
                const password = test.findItem(content, "userPassword") as C.TextField;
                if (!problem || !problem.visible || !password || !password.readOnly) {
                    console.error("Failed switch not shown: " + (problem ? problem.visible + " " + problem.text : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("KEYBOARD_PROBLEM " + problem.text);
            }
            if (["you-caps", "encryption-caps", "you-numlock", "encryption-numlock"].indexOf(test.screenName) >= 0) {
                // The line starts under the focused page's first password field, at its left edge.
                const you = test.screenName.indexOf("you") === 0;
                const field = test.findItem(content, you ? "userPassword" : "diskPassword");
                const caps = test.findItem(content, you ? "capsLock" : "diskCapsLock") as Text;
                const a = caps ? caps.mapToItem(content, 0, 0, caps.width, caps.height) : null;
                const b = field ? field.mapToItem(content, 0, 0, field.width, field.height) : null;
                if (!a || !b || !caps.visible || a.y < b.y + b.height - 0.5 || Math.abs(a.x - b.x) > 1) {
                    console.error("Caps Lock line not under the password field: " + (a && b ? a.x + "," + a.y + " / field " + b.x + "," + (b.y + b.height) : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("CAPS_LOCK " + caps.text);
                // The Num Lock line under the Caps Lock line, at the same left edge.
                const num = test.findItem(content, you ? "numLock" : "diskNumLock") as Text;
                if (test.screenName.endsWith("-numlock")) {
                    const c = num ? num.mapToItem(content, 0, 0) : null;
                    if (!c || !num.visible || c.y < a.y + a.height - 0.5 || Math.abs(c.x - a.x) > 1) {
                        console.error("Num Lock line not under the Caps Lock line: " + (c ? c.x + "," + c.y + " / caps " + a.x + "," + (a.y + a.height) : "missing"));
                        Qt.quit();
                        return;
                    }
                    console.log("NUM_LOCK " + num.text);
                }
            }
            if (test.screenName === "you-paste") {
                const problem = test.findItem(content, "keyboardProblem") as Text;
                if (!problem || !problem.visible) {
                    console.error("Refused paste not shown: " + (problem ? problem.visible + " " + problem.text : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("PASTE_REFUSED " + problem.text);
            }
            if (test.screenName === "done-warning" || test.screenName === "done-no-package-lists") {
                const warning = test.findItem(content, "doneWarnings") as Text;
                if (!warning || !warning.visible || warning.text.indexOf("sudo pacman -Syu") < 0) {
                    console.error("Done warning not shown: " + (warning ? warning.visible + " " + warning.text : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("DONE_WARNING_VISIBLE");
            }
            if (test.screenName === "you-empty") {
                // The login rule starts right under the login field, at its left edge, no wider.
                const field = test.findItem(content, "loginField");
                const problem = test.findItem(content, "loginProblem");
                const a = problem ? problem.mapToItem(content, 0, 0, problem.width, problem.height) : null;
                const b = field ? field.mapToItem(content, 0, 0, field.width, field.height) : null;
                if (!a || !b || !problem.visible || a.y < b.y + b.height - 0.5 || a.y > b.y + b.height + 16 || Math.abs(a.x - b.x) > 1 || a.x + a.width > b.x + b.width + 1) {
                    console.error("Login rule not under the login field: " + (a && b ? a.x + "," + a.y + " " + a.width + " / field " + b.x + "," + (b.y + b.height) + " " + b.width : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("PROBLEM_UNDER loginField " + a.x + "," + a.y);
            }
            if (test.screenName === "you-console") {
                // The console sentence starts under the password field, at its left edge, no wider.
                const field = test.findItem(content, "userPassword");
                const warning = test.findItem(content, "consoleWarning") as Text;
                const a = warning ? warning.mapToItem(content, 0, 0, warning.width, warning.height) : null;
                const b = field ? field.mapToItem(content, 0, 0, field.width, field.height) : null;
                if (!a || !b || !warning.visible || a.y < b.y + b.height - 0.5 || a.y > b.y + b.height + 16 || Math.abs(a.x - b.x) > 1 || a.x + a.width > b.x + b.width + 1) {
                    console.error("Console warning not under the password field: " + (a && b ? a.x + "," + a.y + " " + a.width + " / field " + b.x + "," + (b.y + b.height) + " " + b.width : "missing"));
                    Qt.quit();
                    return;
                }
                // A warning: the form still continues.
                const next = test.findItem(content, "accountContinue") as C.Button;
                if (!next || !next.enabled || content.accountBlocked !== "") {
                    console.error("The console warning blocks Continue: " + (next ? next.enabled + " " + content.accountBlocked : "missing"));
                    Qt.quit();
                    return;
                }
                console.log("CONSOLE_WARNING " + warning.text);
            }
            content.grabToImage(function (result) {
                if (!result.saveToFile(test.screenshot)) {
                    console.error("Screenshot failed");
                    Qt.quit();
                    return;
                }
                test.captured = true;
                console.log("SCREENSHOT_OK " + test.screenName);
                if (test.modeTarget === "")
                    Qt.quit();
                else if (!test.scrollToMode()) {
                    console.error("Mode target not visible: " + test.modeTarget);
                    Qt.quit();
                } else
                    modeShot.restart();
            });
        }
    }
    Timer {
        id: modeShot
        interval: 150
        onTriggered: content.grabToImage(function (result) {
            if (!result.saveToFile(test.screenshot.replace(/\.png$/, "-mode.png"))) {
                console.error("Screenshot failed");
                Qt.quit();
                return;
            }
            console.log("SCREENSHOT_OK " + test.screenName + "-mode");
            Qt.quit();
        })
    }
    Timer {
        interval: 10000
        running: true
        onTriggered: {
            console.error("Renderer timeout");
            Qt.quit();
        }
    }
}
