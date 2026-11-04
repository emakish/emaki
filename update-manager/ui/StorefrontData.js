// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

// Editorial draft: wording, membership and order await the owner's approval.
// Evaluate translations on use so a language change also updates the start screen.
function content() {
    return {
        title: qsTr("Discover your next favorite"),
        subtitle: qsTr("A few ideas for your computer. Recommendations stay on this device."),
        dailyTitle: qsTr("App of the day"),
        detailsLabel: qsTr("Explore app"),
        seeAllLabel: qsTr("See all"),
        collectionsTitle: qsTr("Find your next possibility"),
        becauseTitle: qsTr("Because you have %1"),
        becauseNote: qsTr("Ideas based on programs installed on this computer."),
        emptyTitle: qsTr("More to discover soon"),
        emptyNote: qsTr("Browse the categories. Ideas appear when matching programs are available in the catalog."),
        unavailableArtwork: qsTr("Preview unavailable"),
        collections: [
            {id: "familiar", title: qsTr("Coming from Windows?"),
             description: qsTr("Familiar ways to browse, work and watch."),
             programs: ["org.mozilla.firefox", "org.libreoffice.LibreOffice", "org.videolan.VLC"]},
            {id: "create", title: qsTr("Make something new"),
             description: qsTr("Turn an idea into a drawing, a design or a new world."),
             programs: ["org.kde.krita", "org.inkscape.Inkscape", "org.gimp.GIMP", "org.blender.Blender"]}
        ],
        daily: ["org.kde.krita", "org.blender.Blender", "org.inkscape.Inkscape", "org.videolan.VLC"],
        related: [
            {trigger: "org.kde.krita", programs: ["org.inkscape.Inkscape", "org.gimp.GIMP", "org.blender.Blender"]},
            {trigger: "org.mozilla.firefox", programs: ["org.libreoffice.LibreOffice", "org.videolan.VLC"]},
            {trigger: "org.videolan.VLC", programs: ["org.kde.kdenlive", "org.audacityteam.Audacity"]}
        ]
    };
}
