// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Rectangle {
    id: icon
    property var program: ({})
    implicitWidth: 48
    implicitHeight: 48
    radius: width * .25
    color: Qt.rgba(36/255,16/255,24/255,.09)
    readonly property var sources: iconSources()
    property int sourceIndex: 0
    onSourcesChanged: sourceIndex = 0
    function iconSources(): list<string> {
        const icons = program.icons || [];
        const ordered = icons.filter(i => i.kind === "cached" || i.kind === "local")
            .concat(icons.filter(i => i.kind === "stock" || typeof i === "string"))
            .concat(program.icon ? [{kind: "stock", value: program.icon}] : [])
            .concat(icons.filter(i => i.kind === "remote"));
        const urls = [];
        for (const candidate of ordered) {
            const value = typeof candidate === "string" ? candidate : candidate.url || candidate.path || candidate.name || candidate.value || "";
            let url = "";
            if (value.startsWith("/")) url = "file://" + value;
            else if (value.startsWith("file:") || value.startsWith("https://") || value.startsWith("http://")) url = value;
            else if (value) url = Quickshell.iconPath(value, true);
            if (url && !urls.includes(url)) urls.push(url);
        }
        return urls;
    }
    Image {
        id: picture
        anchors.fill: parent
        anchors.margins: 2
        source: icon.sources[icon.sourceIndex] || ""
        onStatusChanged: {
            if (status === Image.Error) {
                const failedSource = source;
                Qt.callLater(() => {
                    if (picture.status === Image.Error && picture.source === failedSource)
                        icon.sourceIndex += 1;
                });
            }
        }
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        sourceSize.width: icon.width * 2
        sourceSize.height: icon.height * 2
    }
    Item {
        id: packageIcon
        objectName: "package-icon"
        anchors.centerIn: parent
        visible: picture.status !== Image.Ready
        readonly property int pixel: Math.max(1, Math.floor(Math.min(icon.width, icon.height) / 16))
        width: 16 * pixel
        height: 16 * pixel
        // Stepped carton, tape and label share the window icon's warm palette.
        Rectangle {
            objectName: "package-body"
            x: 2 * packageIcon.pixel; y: 6 * packageIcon.pixel
            width: 12 * packageIcon.pixel; height: 8 * packageIcon.pixel
            color: "#c9503a"
        }
        Rectangle {
            x: 3 * packageIcon.pixel; y: 3 * packageIcon.pixel
            width: 10 * packageIcon.pixel; height: 2 * packageIcon.pixel
            color: "#f39a5f"
        }
        Rectangle {
            x: 2 * packageIcon.pixel; y: 5 * packageIcon.pixel
            width: 12 * packageIcon.pixel; height: 2 * packageIcon.pixel
            color: "#f39a5f"
        }
        Rectangle {
            x: 3 * packageIcon.pixel; y: 7 * packageIcon.pixel
            width: 10 * packageIcon.pixel; height: 6 * packageIcon.pixel
            color: "#e2733f"
        }
        Rectangle {
            objectName: "package-tape"
            x: 7 * packageIcon.pixel; y: 3 * packageIcon.pixel
            width: 2 * packageIcon.pixel; height: 7 * packageIcon.pixel
            color: "#fffaf5"
        }
        Rectangle {
            x: 4 * packageIcon.pixel; y: 11 * packageIcon.pixel
            width: 3 * packageIcon.pixel; height: packageIcon.pixel
            color: "#fffaf5"
        }
    }
}
