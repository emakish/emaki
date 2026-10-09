// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "OutputSelection.js" as OutputSelection

// Output identity owns the scene and its windows together. Shared services outlive hotplug.
Scope {
    id: outputs
    required property NiriService niri
    property var screens: Quickshell.screens
    property string requestedOutput: ""
    property bool headless: false
    property bool enabled: true
    property string borderMode: "soft"
    property int reservedSpace: Metrics.reservedSpace
    property int testWidth: 0
    property int testHeight: 0
    property SessionStartup startup: null
    readonly property var selectedScreens: requestedOutput ? screens.filter(s => s.name === requestedOutput) : screens
    readonly property var instances: variants.instances
    readonly property var focusedScreen: selectedScreens.find(s => s.name === niri.model?.focused_output) ?? OutputSelection.select(selectedScreens, "")
    readonly property ShellScene activeScene: instances.find(i => i.modelData.name === focusedScreen?.name)?.scene ?? null
    readonly property bool modelsReady: instances.length > 0 && instances.every(i => i.scene.startupModelsReady && (i.surfaces?.materialsReady ?? false))
    readonly property string modelRevision: JSON.stringify(instances.map(i => [i.modelData.name, i.scene.startupRevision]))
    readonly property bool barMapped: instances.length > 0 && instances.every(i => i.surfaces?.barMapped ?? false)
    readonly property bool dockMapped: instances.length > 0 && instances.every(i => i.surfaces?.dockMapped ?? false)
    readonly property bool overlayMapped: instances.length > 0 && instances.every(i => i.surfaces?.overlayMapped ?? false)
    readonly property alias shared: shared
    ShellServices {
        id: shared
        niri: outputs.niri
        live: !outputs.headless
    }
    Binding {
        target: shared
        property: "panelOpen"
        value: outputs.instances.some(i => i.scene.systemOpen)
    }
    // Only the surface containing the pairing page supplies keyboard readiness.
    readonly property var pairingOwner: instances.find(i => i.surfaces?.pairingKeyboard) ?? null
    Binding {
        target: shared.services
        property: "pairingFocusManaged"
        value: !outputs.headless
    }
    Binding {
        target: shared.services
        property: "pairingFocusReady"
        value: outputs.pairingOwner?.surfaces.pairingReady ?? true
    }
    property ShellScene previousScene: null
    property var savedTransient: null
    function followFocus(): void {
        if (previousScene === activeScene)
            return;
        if (previousScene)
            savedTransient = previousScene.exportTransient();
        previousScene = activeScene;
        if (activeScene && savedTransient) {
            activeScene.importTransient(savedTransient);
            savedTransient = null;
        }
    }
    onActiveSceneChanged: followFocus()
    function sealStartupMaterial(): void {
        for (const instance of instances)
            instance.surfaces?.sealStartupMaterial();
    }
    function closeAll(): void {
        for (const instance of instances)
            instance.scene.closeAll();
    }
    // Each startup frame milestone waits for every output, including outputs added
    // while the login cover is active. One fast display cannot acknowledge another.
    property var frames: ({})
    onModelRevisionChanged: frames = ({})
    Connections {
        target: outputs.startup
        function onSettledChanged(): void {
            if (!outputs.startup.settled)
                outputs.frames = ({});
        }
    }
    function painted(output: string, surface: string): void {
        if (!startup?.settled)
            return;
        const next = Object.assign({}, frames);
        const key = output + "/" + surface;
        next[key] = Math.min(2, (next[key] ?? 0) + 1);
        frames = next;
        const count = startup.frameCounts[surface];
        if (instances.every(i => (frames[i.modelData.name + "/" + surface] ?? 0) > count))
            startup.painted(surface);
    }
    Variants {
        id: variants
        model: outputs.enabled ? outputs.selectedScreens : []
        Scope {
            id: instance
            required property var modelData
            readonly property alias scene: scene
            readonly property Surfaces surfaces: surfacesLoader.item as Surfaces
            ShellScene {
                id: scene
                shared: outputs.shared
                niri: outputs.niri
                skipIntro: outputs.startup?.skipIntro ?? false
                enabled: outputs.enabled
                focusedOutput: outputs.activeScene === scene
                output: outputs.headless ? null : instance.modelData
                outputName: instance.modelData.name || outputs.niri.model?.focused_output || ""
                borderMode: outputs.borderMode
                reservedSpace: outputs.reservedSpace
                headless: outputs.headless
                testScale: outputs.headless ? (instance.modelData.devicePixelRatio ?? 1) : 1
                testWidth: outputs.testWidth || (outputs.headless ? instance.modelData.width : 0)
                testHeight: outputs.testHeight || (outputs.headless ? instance.modelData.height : 0)
            }
            Loader {
                id: surfacesLoader
                onStatusChanged: if (status === Loader.Error) {
                    console.error("Emaki shell: failed to load output surfaces.");
                    Qt.callLater(Qt.exit, 1);
                }
            }
            Connections {
                target: instance.surfaces
                function onPainted(name: string): void {
                    outputs.painted(instance.modelData.name, name);
                }
            }
            Component.onCompleted: {
                if (!outputs.headless)
                    surfacesLoader.setSource(Qt.resolvedUrl("Surfaces.qml"), {
                        controller: scene,
                        startup: outputs.startup,
                        sharedFocus: true,
                        reportFrames: false
                    });
            }
            Component.onDestruction: {
                if (outputs.previousScene === scene) {
                    outputs.savedTransient = scene.exportTransient();
                    outputs.previousScene = null;
                }
            }
        }
    }
}
