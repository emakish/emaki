// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

DockBackdrop {
    id: bar
    property bool surfacePresent: false
    // Other shell captures share the compositor's damage queue. Their last frame
    // can consume a wallpaper replacement while the bar retains the loading frame.
    // Closing a surface does not damage our capture, so explicitly sample again.
    onSurfacePresentChanged: {
        if (!surfacePresent)
            Qt.callLater(bar.refresh);
    }
    // Also cover a replacement that finishes after the surface has already closed.
    onWallpaperTextureChanged: {
        if (wallpaperTexture !== "")
            Qt.callLater(bar.refresh);
    }
}
