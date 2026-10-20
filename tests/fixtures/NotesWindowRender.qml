// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

Rectangle {
    id: root
    property bool dark: false
    property bool selected: true
    property bool empty: false
    width: 1040
    height: 740
    color: ShellPalette.managerBackground
    NotesView { id: view; anchors.fill: parent }
    Component.onCompleted: {
        SettingsBridge.values = [{key: "appearance.theme", value: dark ? "dark" : "light"}];
        NotesService.active = false;
        NotesService.error = "";
        NotesService.ready = true;
        NotesService.notes = empty ? [] : [
            {id: "weekend", title: "A little room to think", body: "A little room to think\nA few things for a quieter afternoon.\n\n- [ ] Sketch the next idea\n- [ ] Take a walk before sunset\n- [x] Clear a space on the desk\n- [ ] Visit the bookshop", pinned: true, revision: "v1", updated: 1792400400},
            {id: "plans", title: "For the weekend", body: "For the weekend\nThe small bookshop by the park, a long walk, and time to read.", pinned: false, revision: "v1", updated: 1792314000},
            {id: "thought", title: "A thought to keep", body: "A thought to keep\nMake the everyday things feel a little more considered.", pinned: false, revision: "v1", updated: 1792227600}
        ];
        if (selected && !empty) view.select("weekend");
    }
}
