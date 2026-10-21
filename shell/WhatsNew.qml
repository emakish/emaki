// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import "WhatsNewContent.js" as ReleaseContent

Welcome {
    id: release
    signal languageRequested
    pages: ReleaseContent.pages()
    heading: ReleaseContent.title()
    closeLabel: ReleaseContent.closeLabel()
    doneLabel: ReleaseContent.doneLabel()
    showShortcuts: false
    artworkHeight: 190
    actionLabel: current.action === "language" ? ReleaseContent.languageLabel() : ""
    onActionRequested: release.languageRequested()
}
