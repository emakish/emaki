//@ pragma AppId emaki-install-test
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import Quickshell
import Quickshell.Io
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
                zones: ["UTC", "Europe/Lisbon", "America/New_York", "Europe/London"],
                trial: false
            })
        network: ({
                wired: true,
                networks: [
                    {
                        ssid: "Home",
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
    }
    Process {
        id: mock
        command: ["python3", "-I", "-B", Qt.resolvedUrl("mock-worker.py").toString().replace("file://", ""), "--stdio", "--screen", test.screenName, "--delay", "0"]
        stdinEnabled: true
        running: true
        onStarted: controller.send("hello", {
            proto: 1
        })
        stdout: SplitParser {
            onRead: data => {
                const message = JSON.parse(data);
                controller.receive(message);
                if (message.type === "inventory" && !test.prepared) {
                    test.prepared = true;
                    controller.diskId = "/dev/vda";
                    controller.fullName = "Alex Morgan";
                    controller.login = "alex";
                    controller.layouts = ["us", "ru"];
                    controller.media = ["/run/media/live/LOGS"];
                    if (["review", "plan-errors", "install", "done", "error"].indexOf(test.screenName) >= 0) {
                        const secret = "fixture-" + Date.now();
                        controller.plan(secret, secret);
                    } else {
                        controller.step = test.screenName === "manual" ? "disk" : test.screenName;
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
                    if (["install", "done", "error"].indexOf(test.screenName) >= 0) {
                        controller.agreed = true;
                        controller.confirm();
                    } else
                        shot.restart();
                } else if (message.type === "done" || message.type === "error" || (message.type === "state" && test.screenName === "install"))
                    shot.restart();
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
            if (!test.checkLayout()) {
                console.error("Layout or font check failed");
                Qt.quit();
                return;
            }
            content.grabToImage(function (result) {
                if (!result.saveToFile(test.screenshot)) {
                    console.error("Screenshot failed");
                    Qt.quit();
                    return;
                }
                test.captured = true;
                console.log("SCREENSHOT_OK " + test.screenName);
                Qt.quit();
            });
        }
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
