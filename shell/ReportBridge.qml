// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Lets Settings open the report window that the shell root owns.
Scope {
    id: bridge
    property var controller: null
    function open(part: string): string {
        return controller ? controller.open(part) : "unavailable";
    }
}
