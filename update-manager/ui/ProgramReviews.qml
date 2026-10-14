// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell.Io

// Stars and reviews on a program page. Catalog programs read the shared open ratings
// service (ODRS) through `emaki-programs reviews`, in a process of its own, so a slow
// or absent network never holds the store. AUR packages show the AUR's votes instead.
ColumnLayout {
    id: reviews
    property var program: ({})
    // The engine command; tests point it at a recorded service.
    property var command: ["emaki-programs"]
    // AUR packages show their votes here unless the page already lists them.
    property bool aurVotes: true
    readonly property bool aur: program?.kind === "aur"
    // AppStream IDs of every catalog edition: ODRS keys reviews by them, never by package names.
    readonly property var ownIds: {
        const editions = program?.editions || [program || {}];
        const found = [];
        for (const edition of editions)
            if (edition && edition.id && !edition.packageOnly && (edition.kind === "arch" || edition.kind === "flatpak") && !found.includes(edition.id))
                found.push(edition.id);
        return found;
    }
    // Then the IDs those editions' metadata provides (<provides><id>): read from, never written to.
    readonly property var ids: {
        const found = ownIds.slice();
        for (const edition of program?.editions || [program || {}])
            if (ownIds.includes(edition?.id))
                for (const alias of edition.providedIds || [])
                    if (typeof alias === "string" && alias !== "" && !found.includes(alias))
                        found.push(alias);
        return found;
    }
    readonly property string version: program?.installedVersion || program?.version || ""
    property var rating: null
    property var items: []
    property string appId: ""
    property bool reviewed: false
    property bool loading: false
    property bool loaded: false
    property string error: ""
    property bool expanded: false
    property string notice: ""
    spacing: 12
    visible: aur ? aurVotes && program?.votes !== undefined && program?.votes !== null : ids.length > 0

    onIdsChanged: reload()
    Component.onCompleted: reload()

    function reload(): void {
        if (reader.running && reader.wanted === ids.join(" ") && !reader.again) return;
        // The catalog row's cached stars show at once; the fetch brings the newest count and the reviews.
        rating = program?.rating || null; items = []; appId = ""; reviewed = false; error = ""; loaded = false; expanded = false; notice = "";
        if (aur || !ids.length) { loading = false; return; }
        loading = true;
        if (reader.running) { reader.again = true; reader.running = false; return; }
        reader.command = command.concat(["reviews"]).concat(ids).concat(["--json"]);
        reader.wanted = ids.join(" ");
        reader.running = true;
        readerWatch.restart();
    }
    function date(seconds: int): string {
        return seconds > 0 ? new Date(seconds * 1000).toLocaleDateString(Qt.locale("en_US"), "d MMM yyyy") : "";
    }

    Timer {
        // A command that cannot start never exits: say so instead of loading forever.
        id: readerWatch
        interval: 3000
        onTriggered: if (!reader.running && reviews.loading) { reviews.loading = false; reviews.loaded = true; reviews.error = "Ratings could not be loaded. Check your connection."; }
    }
    Timer {
        id: writerWatch
        interval: 3000
        onTriggered: if (!writer.running && dialog.sending) { dialog.sending = false; dialog.problem = "The review could not be sent. Check your connection and try again."; }
    }
    Process {
        id: reader
        property string reply: ""
        property string wanted: ""
        property bool again: false
        stdout: StdioCollector { onStreamFinished: reader.reply = text }
        onExited: () => Qt.callLater(() => {
            if (reader.again) { reader.again = false; reviews.reload(); return; }
            // A reply for a program the page has left is dropped.
            if (reader.wanted !== reviews.ids.join(" ")) return;
            reviews.loading = false;
            reviews.loaded = true;
            try {
                const result = JSON.parse(reader.reply);
                reviews.rating = result.rating || reviews.rating;
                reviews.items = Array.isArray(result.reviews) ? result.reviews : [];
                reviews.appId = result.appId || "";
                reviews.reviewed = result.reviewed === true;
                reviews.error = result.ok === true ? "" : (result.message || "Ratings could not be loaded. Check your connection.");
            } catch (_) {
                reviews.error = "Ratings could not be loaded. Check your connection.";
            }
        })
    }
    Process {
        id: writer
        property string reply: ""
        stdout: StdioCollector { onStreamFinished: writer.reply = text }
        onExited: () => Qt.callLater(() => {
            dialog.sending = false;
            let result = {};
            try { result = JSON.parse(writer.reply); } catch (_) {}
            if (result.ok === true) {
                dialog.close();
                reviews.reload();
                reviews.notice = "Thank you. Your review is sent; it shows here once the service lists it.";
            } else {
                dialog.problem = result.message || "The review could not be sent. Check your connection and try again.";
            }
        })
    }

    ManagerLabel { text: reviews.aur ? "VOTES" : "RATINGS AND REVIEWS"; font.pixelSize: 12; font.weight: Font.DemiBold; color: "#866c70" }
    ManagerLabel {
        objectName: "reviews-aur-votes"
        visible: reviews.aur
        text: reviews.aur ? Number(reviews.program.votes || 0).toLocaleString(Qt.locale("en_US"), "f", 0) + (reviews.program.votes === 1 ? " vote" : " votes")
            + " on the AUR · popularity " + Number(reviews.program.popularity || 0).toFixed(2) : ""
        font.weight: Font.DemiBold
    }
    ManagerLabel {
        visible: reviews.aur
        text: "AUR packages have no star ratings. Votes come from AUR users; popularity counts recent votes."
        color: "#866c70"; font.pixelSize: 12
    }
    Stars {
        objectName: "reviews-summary"
        visible: !reviews.aur && !!reviews.rating
        rating: reviews.rating
        showCount: true
        size: 16
    }
    ManagerLabel {
        objectName: "reviews-state"
        visible: !reviews.aur && text !== ""
        text: reviews.loading ? "Loading ratings…"
            : reviews.error !== "" ? reviews.error
            : reviews.loaded && !reviews.rating && !reviews.items.length ? "No ratings yet. Be the first to review it."
            : reviews.loaded && !reviews.items.length ? "No written reviews in your language yet." : ""
        color: "#866c70"; font.pixelSize: 13
    }
    Repeater {
        model: reviews.aur ? [] : reviews.expanded ? reviews.items : reviews.items.slice(0, 3)
        Rectangle {
            id: card
            required property var modelData
            objectName: "review-card"
            Layout.fillWidth: true
            implicitHeight: cardColumn.implicitHeight + 28
            radius: 14
            color: Qt.rgba(1, .98, .96, .42)
            border.color: Qt.rgba(36/255,16/255,24/255,.14)
            ColumnLayout {
                id: cardColumn
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                spacing: 4
                Stars { rating: ({average: card.modelData.stars, count: 1}); showAverage: false }
                ManagerLabel { text: card.modelData.summary || ""; font.weight: Font.DemiBold; visible: text !== "" }
                ManagerLabel { text: card.modelData.text || ""; color: "#4a3438"; font.pixelSize: 13; lineHeight: 1.3; visible: text !== "" }
                ManagerLabel {
                    text: [card.modelData.mine ? "Your review" : card.modelData.name || "Anonymous", reviews.date(card.modelData.date || 0),
                           card.modelData.version ? "version " + card.modelData.version : ""].filter(part => part !== "").join(" · ")
                    color: "#866c70"; font.pixelSize: 12
                }
            }
        }
    }
    RowLayout {
        spacing: 8
        visible: !reviews.aur
        ManagerButton {
            objectName: "reviews-more"
            visible: !reviews.expanded && reviews.items.length > 3
            text: "Show all " + reviews.items.length + " reviews"
            quiet: true
            onClicked: reviews.expanded = true
        }
        ManagerButton {
            objectName: "reviews-write"
            text: "Write a review"
            visible: !reviews.reviewed
            enabled: reviews.loaded && reviews.ids.length > 0
            onClicked: dialog.open()
        }
    }
    ManagerLabel { visible: reviews.notice !== "" && !reviews.aur; text: reviews.notice; color: "#4c6b2e"; font.pixelSize: 13 }

    C.Popup {
        id: dialog
        objectName: "review-dialog"
        property int stars: 0
        property string problem: ""
        property bool sending: false
        parent: C.Overlay.overlay
        width: Math.min(560, (parent?.width || 640) - 48)
        x: ((parent?.width || width) - width) / 2
        y: Math.max(24, ((parent?.height || height) - height) / 2)
        padding: 24
        modal: true
        focus: true
        closePolicy: writer.running ? C.Popup.NoAutoClose : C.Popup.CloseOnEscape
        onOpened: { stars = 0; problem = ""; title.text = ""; body.text = ""; title.forceActiveFocus(); }
        background: Rectangle { radius: 16; color: "#fff9f5"; border.color: Qt.rgba(36/255,16/255,24/255,.14) }
        C.Overlay.modal: Rectangle { color: Qt.rgba(36/255,16/255,24/255,.26) }
        contentItem: ColumnLayout {
            spacing: 12
            ManagerLabel { text: "Review " + (reviews.program?.name || "this program"); font.pixelSize: 20; font.weight: Font.DemiBold }
            Row {
                objectName: "review-stars"
                spacing: 2
                Repeater {
                    model: 5
                    C.AbstractButton {
                        id: star
                        required property int index
                        objectName: "review-star-" + (index + 1)
                        implicitWidth: 34; implicitHeight: 34
                        focusPolicy: Qt.StrongFocus
                        Keys.onReturnPressed: clicked()
                        Keys.onSpacePressed: clicked()
                        onClicked: dialog.stars = index + 1
                        contentItem: Text {
                            text: star.index < dialog.stars ? "★" : "☆"
                            color: "#d9962c"
                            font.pixelSize: 26
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle { radius: 8; color: "transparent"; border.width: star.activeFocus ? 2 : 0; border.color: "#c2481f" }
                    }
                }
            }
            C.TextField {
                id: title
                objectName: "review-title"
                Layout.fillWidth: true
                placeholderText: "Title, for example: Works well for drawing"
                maximumLength: 70
                leftPadding: 10; rightPadding: 10
                font.family: "Noto Sans"; font.pixelSize: 14
                color: "#241018"
                background: Rectangle { radius: 10; color: "#fffaf6"; border.width: title.activeFocus ? 2 : 1; border.color: title.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.2) }
            }
            C.ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 130
                C.TextArea {
                    id: body
                    objectName: "review-text"
                    placeholderText: "What is good or bad about it?"
                    wrapMode: TextEdit.Wrap
                    font.family: "Noto Sans"; font.pixelSize: 14
                    color: "#241018"
                    onTextChanged: if (length > 3000) remove(3000, length)
                    background: Rectangle { radius: 10; color: "#fffaf6"; border.width: body.activeFocus ? 2 : 1; border.color: body.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.2) }
                }
            }
            C.TextField {
                id: author
                objectName: "review-name"
                Layout.fillWidth: true
                placeholderText: "Your name as others see it (optional)"
                maximumLength: 40
                leftPadding: 10; rightPadding: 10
                font.family: "Noto Sans"; font.pixelSize: 14
                color: "#241018"
                background: Rectangle { radius: 10; color: "#fffaf6"; border.width: author.activeFocus ? 2 : 1; border.color: author.activeFocus ? "#c2481f" : Qt.rgba(36/255,16/255,24/255,.2) }
            }
            ManagerLabel {
                text: "Your review is public. It goes to the open ratings service at odrs.gnome.org, which GNOME Software and KDE Discover use too, with the name above or “Anonymous”. The service also keeps the network address it came from."
                color: "#866c70"; font.pixelSize: 12; lineHeight: 1.3
            }
            ManagerLabel { objectName: "review-problem"; visible: dialog.problem !== ""; text: dialog.problem; color: "#a01b45"; font.pixelSize: 13 }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10
                ManagerButton { text: "Cancel"; enabled: !writer.running; onClicked: dialog.close() }
                ManagerButton {
                    objectName: "review-send"
                    text: writer.running ? "Sending…" : "Send review"
                    primary: true
                    enabled: !writer.running && dialog.stars > 0 && title.text.trim() !== "" && body.text.trim() !== ""
                    onClicked: {
                        dialog.problem = "";
                        // A review goes to the program's own ID only, never to a key the reviews were read from.
                        const form = {ids: reviews.ownIds.slice(0, 1),
                                      rating: dialog.stars, summary: title.text.trim(), text: body.text.trim(),
                                      name: author.text.trim(), version: reviews.version};
                        writer.command = reviews.command.concat(["review", JSON.stringify(form), "--json"]);
                        dialog.sending = true;
                        writer.running = true;
                        writerWatch.restart();
                    }
                }
            }
        }
    }
}
