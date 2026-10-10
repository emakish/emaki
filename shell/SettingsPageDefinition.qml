// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

QtObject {
    required property string pageId
    property list<string> keys: []
    property var resetKeys: keys
    property string description: ""
    property var searchAnchors: ({})
    required property Component component
}
