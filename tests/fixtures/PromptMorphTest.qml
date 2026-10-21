// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: root
    property int phase: 0
    property double changedAt: Date.now()
    property int pending: 0
    property int openingFrames: 0
    property int closingFrames: 0
    property int cancelFrames: 0
    property int peekFrames: 0
    property var windows: []
    property var password: null
    property var requestIdentity: null
    property double requestDeadline: 0
    readonly property var surface: promptWindow.body
    property bool peekWasVisible: false
    property int peekStarts: 0
    property var heldPeekIds: []
    property var a: ({
            name: "First",
            width: 1280,
            height: 800,
            devicePixelRatio: 1
        })
    property var b: ({
            name: "Second",
            width: 1440,
            height: 900,
            devicePixelRatio: 1
        })

    component Source: QtObject {
        property string requestKind: "polkit"
        property var flow: null
        property bool responseRequired: true
        property bool passwordRequest: true
        property string requestLine: "Fixture requests administrator rights."
        property string accountName: "fixture"
        property string iconName: ""
        property var detailRows: []
        property int submissions: 0
        property int cancellations: 0
        signal rejected
        signal interrupted
        function submit(value: string): void {
            ++submissions;
            responseRequired = false;
        }
        function cancel(reason: string): void {
            ++cancellations;
            flow = null;
        }
    }
    Source {
        id: polkit
    }
    Source {
        id: sudoSource
        requestKind: "sudo"
    }
    PolkitPromptQueue {
        id: queue
        polkitBackend: polkit
        sudoBackend: sudoSource
    }
    PolkitPromptController {
        id: controller
        backend: queue
        locked: false
        onStarted: outputs.closeAll()
        onNotice: (title, body) => outputs.shared.notifications.local("Emaki", title, body)
    }
    NiriService {
        id: niri
        binary: ""
    }
    ShellOutputs {
        id: outputs
        niri: niri
        screens: [root.a, root.b]
        headless: true
        reservedSpace: 0
        authenticationPrompt: surface
    }
    PromptWindow {
        id: promptWindow
        controller: controller
        scene: outputs.activeScene
        holdFirstFrame: true
    }
    DockBackdrop {
        id: foreignBackdrop
        parent: outputs.activeScene
        mode: "wallpaper"
        active: false
    }
    TestCase {
        id: input
        when: false
    }
    Component {
        id: windowFactory
        Window {
            visible: true
            color: "#39302e"
        }
    }

    function check(condition: bool, message: string): void {
        if (!condition)
            throw new Error("PROMPT_MORPH_FAILED " + message);
    }
    function advance(): void {
        ++phase;
        changedAt = Date.now();
    }
    function publish(name: string): void {
        niri.model = {
            windows: {},
            workspaces: {
                1: {
                    id: 1,
                    idx: 1,
                    output: a.name,
                    is_active: true
                },
                2: {
                    id: 2,
                    idx: 1,
                    output: b.name,
                    is_active: true
                }
            },
            outputs: {
                First: {
                    logical: {
                        width: a.width,
                        height: a.height
                    }
                },
                Second: {
                    logical: {
                        width: b.width,
                        height: b.height
                    }
                }
            },
            focused_output: name,
            overview_open: false,
            keyboard_layouts: {
                names: ["English (US)"],
                current_idx: 0
            }
        };
        niri.connection = "connected";
    }
    function inspect(): void {
        inspectTextures(surface.card);
        check(surface.Window.window !== outputs.activeScene.Window.window, "prompt and bar have separate native windows");
        check(surface.card.backdrop !== null && surface.card.localBackdrop === surface.card.backdrop, "protected card accepts only its own backdrop");
        for (const provider of [surface.card.backdrop, surface.card.backdrop.sharp, surface.card.backdrop.clear, surface.card.backdrop.regular])
            check(provider.Window.window === surface.card.Window.window, "every protected texture provider belongs to its window");
        check(surface.card.y === Metrics.top, "morph remains anchored at clock top");
        check(surface.clockCovered === (surface.present && surface.presented && surface.card.morph > .5), "clock yields only after protected presentation and morph midpoint");
        check(Math.abs(surface.card.width - (surface.card.islandWidth + (surface.card.targetWidth - surface.card.islandWidth) * surface.card.morph)) < .01, "width grows continuously from clock geometry");
        // The shared canvas replaces the card's flat fill, so it grows in with the card body.
        const canvas = input.findChild(surface.card, "polkit-canvas");
        check((canvas !== null) === LiquidPalette.sharedCanvas, "the card lays the shared canvas exactly when the switch is on");
        if (canvas !== null) {
            let opacity = 1;
            for (let item = canvas; item !== null && item !== surface.card; item = item.parent)
                opacity *= item.opacity;
            check(Math.abs(opacity - surface.card.contentAlpha) < .001, "shared canvas fades in with the card body");
        }
        for (const instance of outputs.instances) {
            const scene = instance.scene;
            const owns = scene === outputs.activeScene;
            check(scene.authenticationPresent === (owns && surface.visible), "one output owns prompt visibility");
            check(scene.bar.clockIsland.panelPresent === ((owns && surface.clockCovered) || (scene.clockPresent && scene.clockExpansion > .5)), "clock suppressed only where a panel draws");
        }
    }
    function inspectTextures(item: var): void {
        if (item instanceof ShaderEffect) {
            for (const key of Object.keys(item)) {
                const provider = item[key];
                if (provider instanceof Item)
                    check(provider.Window.window === item.Window.window, "shader texture " + key + " belongs to protected window");
            }
        }
        if (item instanceof ShaderEffectSource && item.sourceItem)
            check(item.sourceItem.Window.window === item.Window.window, "texture source belongs to protected window");
        for (const child of item.children)
            inspectTextures(child);
    }
    function inspectPeek(): void {
        const scene = outputs.activeScene;
        const shown = scene.clockPanel.visible;
        if (surface.present)
            check(!scene.peekOpen, "notification peeks wait for the whole prompt close");
        if (shown && !peekWasVisible) {
            check(scene.clockExpansion <= .05, "first visible notification frame starts at bubble size");
            ++peekStarts;
        }
        peekWasVisible = shown;
    }
    Connections {
        target: outputs.activeScene?.clockPanel ?? null
        function onVisibleChanged(): void {
            root.inspectPeek();
        }
    }
    function capture(name: string, after: var): void {
        const scene = outputs.activeScene;
        const card = surface.card;
        const report = {
            name: name,
            output: scene.outputName,
            expansion: card.expansion,
            x: card.x,
            y: card.y,
            width: card.width,
            height: card.height,
            clockCovered: surface.clockCovered,
            clockPanelVisible: scene.clockPanel.visible,
            clockExpansion: scene.clockExpansion,
            capture: surface.visible ? "protected-window" : "panel-window",
            softwareArtifact: surface.visible && card.contentAlpha === 1 ? "Clipped Flickable may make the transparent window background black in software grabs." : "",
            accepting: controller.accepting
        };
        ++pending;
        const target = surface.visible ? promptWindow.contentItem : scene;
        check(target.grabToImage(result => {
            root.check(result.saveToFile(Quickshell.env("PROMPT_MORPH_SHOTS") + "/" + name + ".png"), "saved frame " + name);
            console.info("PROMPT_MORPH_FRAME " + JSON.stringify(report));
            --root.pending;
            if (after)
                after();
        }), "scheduled frame " + name);
    }
    function begin(source: var, identity: string): void {
        source.responseRequired = true;
        source.flow = {
            id: identity
        };
    }
    function run(): void {
        const elapsed = Date.now() - changedAt;
        if (phase > 0)
            inspectPeek();
        if (phase === 0) {
            publish(a.name);
            for (const instance of outputs.instances) {
                const scene = instance.scene;
                const window = windowFactory.createObject(root, {
                    width: scene.viewportWidth,
                    height: scene.viewportHeight
                });
                scene.parent = window.contentItem;
                scene.width = scene.viewportWidth;
                scene.height = scene.viewportHeight;
                scene.bar.dateTime = new Date(2026, 8, 27, 9, 41);
                windows.push(window);
            }
            check(windows.length === 2, "two output scenes created");
            windows[0].requestActivate();
            advance();
        } else if (phase === 1 && elapsed > 500) {
            inspect();
            check(outputs.activeScene.presentationState === "clear", "notification presentation is clear");
            const ownBackdrop = surface.backdrop;
            surface.backdrop = foreignBackdrop;
            check(surface.card.localBackdrop === null && !surface.card.glassReady, "foreign backdrop rejected before shader binding");
            inspectTextures(surface.card);
            surface.backdrop = ownBackdrop;
            check(!surface.visible && surface.card.expansion === 0, "initial clock bubble");
            capture("00-bubble", () => {
                begin(polkit, "first");
                check(controller.inputBlocked && !controller.accepting, "input guard starts before morph");
                advance();
            });
        } else if (phase === 2) {
            inspect();
            if (promptWindow.holdFirstFrame) {
                check(surface.card.expansion === 0 && !surface.clockCovered, "delayed first frame keeps original bubble and stops morph");
                if (elapsed > Metrics.morphMs + 150) {
                    check(promptWindow.presented, "production frameSwapped marks native presentation");
                    promptWindow.holdFirstFrame = false;
                }
                return;
            }
            if (surface.card.expansion > 0 && surface.card.expansion < 1) {
                capture("01-opening-" + openingFrames++);
            } else if (surface.card.expansion === 1 && controller.accepting) {
                check(openingFrames >= 2, "multiple real opening frames");
                password = input.findChild(surface.card, "polkit-password");
                check(password !== null, "real password field found");
                surface.takeFocus();
                check(password.activeFocus, "real field receives focus after guard");
                capture("02-prompt", () => {
                    controller.submit("fixture answer");
                    check(polkit.submissions === 1 && password.text === "", "submit forwards and clears");
                    polkit.flow = null;
                    advance();
                });
            }
        } else if (phase === 3) {
            inspect();
            if (surface.card.expansion > 0 && surface.card.expansion < 1) {
                check(surface.visible, "surface retained for close morph");
                check(!input.findChild(surface.card, "polkit-error").visible, "a granted request closes without an error line");
                capture("03-closing-" + closingFrames++);
            } else if (surface.card.expansion === 0) {
                check(closingFrames >= 2 && !surface.visible, "successful request shrinks back to bubble");
                capture("04-bubble", () => {
                    begin(polkit, "cancel");
                    advance();
                });
            }
        } else if (phase === 4 && controller.accepting) {
            password.text = "fixture answer";
            controller.cancel("cancel");
            check(polkit.flow === null && password.text === "", "cancel ends provider and clears input");
            advance();
        } else if (phase === 5) {
            inspect();
            if (surface.card.expansion > 0) {
                check(!input.findChild(surface.card, "polkit-error").visible, "a cancelled request closes without an error line");
                capture("05-cancel-close-" + cancelFrames++);
                return;
            }
            if (!outputs.activeScene.peekOpen) {
                check(outputs.activeScene.deferredPeekIds.length > 0, "cancel notice awaits replay after unmapping");
                return;
            }
            check(peekStarts === 1 && outputs.activeScene.peekOpen, "cancel notice begins a fresh peek after shrink");
            if (outputs.activeScene.clockExpansion < 1) {
                capture("05-cancel-peek-" + peekFrames++);
                return;
            }
            check(cancelFrames >= 2 && peekFrames >= 2, "cancel shrink and subsequent peek each have multiple frames");
            capture("05-cancelled", () => {
                begin(polkit, "queued-first");
                begin(sudoSource, "queued-second");
                check(queue.requests.length === 2 && queue.requestKind === "polkit", "two providers queued in order");
                advance();
            });
        } else if (phase === 6 && controller.accepting) {
            password.text = "fixture answer";
            controller.cancel("cancel");
            check(queue.requestKind === "sudo" && controller.opened && controller.inputBlocked, "next queued request opens with new input guard");
            check(password.text === "", "queue transition clears prior input");
            advance();
        } else if (phase === 7 && controller.accepting) {
            inspect();
            heldPeekIds = outputs.activeScene.deferredPeekIds.slice();
            check(heldPeekIds.length > 0, "queued cancellation notice remains held");
            check(surface.card.expansion === 1, "queued prompt keeps expanded clock");
            capture("06-queued-sudo", () => {
                password.text = "fixture answer";
                requestIdentity = controller.currentFlow;
                requestDeadline = controller.deadline;
                promptWindow.requestActivate();
                publish(b.name);
                advance();
            });
        } else if (phase === 8 && elapsed > 100) {
            inspect();
            check(heldPeekIds.every(id => outputs.activeScene.deferredPeekIds.includes(id)), "held notices follow output migration");
            check(surface.scene.outputName === b.name && password.text === "fixture answer", "same input moves to newly focused output");
            check(controller.currentFlow === requestIdentity && controller.deadline === requestDeadline, "output migration preserves request identity and deadline");
            promptWindow.requestActivate();
            surface.takeFocus();
            check(password.activeFocus, "migrated field retains keyboard focus");
            password.text = "";
            capture("07-second-output", () => {
                controller.locked = true;
                check(!surface.visible && !controller.opened && queue.flow === null && password.text === "", "lock hides and cancels active request immediately");
                advance();
            });
        } else if (phase === 9 && elapsed > Metrics.morphMs + 100) {
            inspect();
            check(surface.card.expansion === 0, "locked prompt leaves no animated remainder");
            capture("08-locked", () => {
                controller.locked = false;
                check(!controller.opened, "unlock cannot revive cancelled request");
                begin(polkit, "lock-first");
                begin(sudoSource, "lock-second");
                controller.locked = true;
                check(queue.requests.length === 0 && polkit.flow === null && sudoSource.flow === null, "lock drains both queued providers");
                advance();
            });
        } else if (phase === 10 && elapsed > 100) {
            controller.locked = false;
            outputs.activeScene.closeAll();
            begin(polkit, "timeout");
            advance();
        } else if (phase === 11 && controller.accepting) {
            controller.tick(controller.deadline);
            check(!controller.opened, "deadline cancels the request");
            check(outputs.activeScene.deferredPeekIds.length > 0, "timeout notice held during close");
            advance();
        } else if (phase === 12 && elapsed > Metrics.morphMs + 100) {
            check(!surface.present && outputs.activeScene.peekOpen && peekStarts >= 2, "timeout notice appears after shrink");
            capture("09-timeout", () => root.advance());
        } else if (phase === 13) {
            console.info("PROMPT_MORPH_OK");
            Qt.quit();
        }
    }
    Timer {
        interval: 40
        running: true
        repeat: true
        onTriggered: {
            if (root.pending)
                return;
            try {
                root.run();
            } catch (error) {
                console.error(String(error));
                Qt.exit(1);
            }
        }
    }
}
