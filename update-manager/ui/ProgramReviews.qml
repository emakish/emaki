// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "ManagerLogic.js" as Logic
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts
import QtQuick.Controls as C
import Quickshell.Io

// Stars and reviews on a program page. Catalog programs read the shared open ratings
// service (ODRS) through `emaki-programs reviews`, in a process of its own, so a slow
// or absent network never holds the store. AUR packages show the AUR's votes instead.
ColumnLayout {
    id: reviews
    readonly property bool translationsReady: Shell.Translation.initialize()
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
        // English sessions keep 0.10's en_US names ("Sep"); other languages name the month themselves.
        const english = Qt.locale().name === "C" || Qt.locale().name.startsWith("en_");
        return seconds > 0 ? new Date(seconds * 1000).toLocaleDateString(english ? Qt.locale("en_US") : Qt.locale(), "d MMM yyyy") : "";
    }

    Timer {
        // A command that cannot start never exits: say so instead of loading forever.
        id: readerWatch
        interval: 3000
        onTriggered: if (!reader.running && reviews.loading) { reviews.loading = false; reviews.loaded = true; reviews.error = qsTranslate("ProgramReviews", "Ratings could not be loaded. Check your connection."); }
    }
    Timer {
        id: writerWatch
        interval: 3000
        onTriggered: if (!writer.running && dialog.sending) { dialog.sending = false; dialog.problem = qsTranslate("ProgramReviews", "The review could not be sent. Check your connection and try again."); }
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
                reviews.error = result.ok === true ? "" : (result.message || qsTranslate("ProgramReviews", "Ratings could not be loaded. Check your connection."));
            } catch (_) {
                reviews.error = qsTranslate("ProgramReviews", "Ratings could not be loaded. Check your connection.");
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
                reviews.notice = qsTranslate("ProgramReviews", "Thank you. Your review is sent; it shows here once the service lists it.");
            } else {
                dialog.problem = result.message || qsTranslate("ProgramReviews", "The review could not be sent. Check your connection and try again.");
            }
        })
    }

    ManagerLabel { text: reviews.aur ? qsTranslate("ProgramReviews", "VOTES") : qsTranslate("ProgramReviews", "RATINGS AND REVIEWS"); font.pixelSize: 12; font.weight: Font.DemiBold; color: Shell.ShellPalette.managerDim }
    ManagerLabel {
        objectName: "reviews-aur-votes"
        visible: reviews.aur
        text: reviews.aur ? Shell.Translation.englishPlural(qsTranslate("ProgramReviews", "%1 vote(s) on the AUR · popularity %2", "", reviews.program.votes || 0), reviews.program.votes || 0).arg(Logic.groupedCount(reviews.program.votes || 0)).arg(Logic.decimal(reviews.program.popularity || 0, 2)) : ""
        font.weight: Font.DemiBold
    }
    ManagerLabel {
        visible: reviews.aur
        text: qsTranslate("ProgramReviews", "AUR packages have no star ratings. Votes come from AUR users; popularity counts recent votes.")
        color: Shell.ShellPalette.managerDim; font.pixelSize: 12
    }
    Stars {
        objectName: "reviews-summary"
        visible: !reviews.aur && !!reviews.rating
        rating: reviews.rating
        // A narrow page shortens the count: 1.8k instead of 1,804 ratings.
        showCount: reviews.width >= 240
        showShortCount: !showCount
        size: 16
    }
    ManagerLabel {
        objectName: "reviews-state"
        visible: !reviews.aur && text !== ""
        text: reviews.loading ? qsTranslate("ProgramReviews", "Loading ratings…")
            : reviews.error !== "" ? reviews.error
            : reviews.loaded && !reviews.rating && !reviews.items.length ? qsTranslate("ProgramReviews", "No ratings yet. Be the first to review it.")
            : reviews.loaded && !reviews.items.length ? qsTranslate("ProgramReviews", "No written reviews in your language yet.") : ""
        color: Shell.ShellPalette.managerDim; font.pixelSize: 13
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
            color: Qt.alpha(Shell.ShellPalette.managerCream, .42)
            border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14)
            ColumnLayout {
                id: cardColumn
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
                spacing: 4
                Stars { rating: ({average: card.modelData.stars, count: 1}); showAverage: false }
                ManagerLabel { text: card.modelData.summary || ""; font.weight: Font.DemiBold; visible: text !== "" }
                ManagerLabel { text: card.modelData.text || ""; color: Shell.ShellPalette.managerBody; font.pixelSize: 13; lineHeight: 1.3; visible: text !== "" }
                ManagerLabel {
                    text: [card.modelData.mine ? qsTranslate("ProgramReviews", "Your review") : card.modelData.name || qsTranslate("ProgramReviews", "Anonymous"), reviews.date(card.modelData.date || 0),
                           card.modelData.version ? qsTranslate("ProgramReviews", "version %1").arg(card.modelData.version) : ""].filter(part => part !== "").join(" · ")
                    color: Shell.ShellPalette.managerDim; font.pixelSize: 12
                }
            }
        }
    }
    Flow {
        Layout.fillWidth: true
        spacing: 8
        visible: !reviews.aur
        ManagerButton {
            objectName: "reviews-more"
            width: Math.min(implicitWidth, parent.width)
            squeezed: parent.width > 0 && implicitWidth > parent.width
            visible: !reviews.expanded && reviews.items.length > 3
            text: Shell.Translation.englishPlural(qsTranslate("ProgramReviews", "Show all %n review(s)", "", reviews.items.length), reviews.items.length)
            quiet: true
            onClicked: reviews.expanded = true
        }
        ManagerButton {
            objectName: "reviews-write"
            width: Math.min(implicitWidth, parent.width)
            squeezed: parent.width > 0 && implicitWidth > parent.width
            text: qsTranslate("ProgramReviews", "Write a review")
            visible: !reviews.reviewed
            enabled: reviews.loaded && reviews.ids.length > 0
            onClicked: dialog.open()
        }
    }
    ManagerLabel { visible: reviews.notice !== "" && !reviews.aur; text: reviews.notice; color: Shell.ShellPalette.managerPositive; font.pixelSize: 13 }

    C.Popup {
        id: dialog
        objectName: "review-dialog"
        property int stars: 0
        property string problem: ""
        property bool sending: false
        parent: C.Overlay.overlay
        // A narrow window keeps the dialog's sides and padding small.
        readonly property bool narrow: (parent?.width || 640) < 600
        width: Math.min(560, (parent?.width || 640) - (narrow ? 16 : 48))
        x: ((parent?.width || width) - width) / 2
        y: Math.max(narrow ? 8 : 24, ((parent?.height || height) - height) / 2)
        padding: narrow ? 16 : 24
        modal: true
        focus: true
        closePolicy: writer.running ? C.Popup.NoAutoClose : C.Popup.CloseOnEscape
        onOpened: { stars = 0; problem = ""; title.text = ""; body.text = ""; title.forceActiveFocus(); }
        background: Rectangle { radius: 16; color: Shell.ShellPalette.managerPanel; border.color: Qt.alpha(Shell.ShellPalette.managerInk, .14) }
        C.Overlay.modal: Rectangle { color: Qt.alpha(Shell.ShellPalette.managerInk, .26) }
        // A window shorter than the dialog scrolls it.
        height: Math.min(implicitHeight, (parent?.height || implicitHeight) - 2 * (narrow ? 8 : 24))
        contentItem: C.ScrollView {
            id: dialogScroll
            contentWidth: availableWidth
            ColumnLayout {
                width: dialogScroll.availableWidth
                spacing: 12
                ManagerLabel { text: qsTranslate("ProgramReviews", "Review %1").arg(reviews.program?.name || qsTranslate("ProgramReviews", "this program")); font.pixelSize: 20; font.weight: Font.DemiBold }
                Row {
                    objectName: "review-stars"
                    spacing: 2
                    Repeater {
                        model: 5
                        C.AbstractButton {
                            id: star
                            required property int index
                            objectName: "review-star-" + (index + 1)
                            // Five stars always fit the dialog's width.
                            implicitWidth: Math.min(34, Math.floor((dialog.availableWidth - 8) / 5)); implicitHeight: 34
                            Accessible.name: qsTranslate("ProgramReviews", "Rate %1 of 5").arg(index + 1)
                            Accessible.role: Accessible.RadioButton
                            Accessible.selected: dialog.stars === index + 1
                            checkable: true
                            autoExclusive: true
                            checked: dialog.stars === index + 1
                            Accessible.checked: checked
                            focusPolicy: Qt.StrongFocus
                            Keys.onReturnPressed: clicked()
                            Keys.onSpacePressed: clicked()
                            onClicked: dialog.stars = index + 1
                            contentItem: Text {
                                text: star.index < dialog.stars ? "★" : "☆"
                                color: Shell.ShellPalette.managerWarning
                                font.pixelSize: Math.min(26, star.width - 8)
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle { radius: 8; color: "transparent"; border.width: star.activeFocus ? 2 : 0; border.color: Shell.ShellPalette.accentForeground }
                        }
                    }
                }
                C.TextField {
                    id: title
                    objectName: "review-title"
                    Layout.fillWidth: true
                    placeholderText: qsTranslate("ProgramReviews", "Title, for example: Works well for drawing")
                    maximumLength: 70
                    leftPadding: 10; rightPadding: 10
                    font.family: "Noto Sans"; font.pixelSize: 14
                    color: Shell.ShellPalette.managerInk
                    background: Rectangle { radius: 10; color: Shell.ShellPalette.managerBackground; border.width: title.activeFocus ? 2 : 1; border.color: title.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .2) }
                }
                C.ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 130
                    // The text wraps at the field's width; it never scrolls sideways.
                    contentWidth: availableWidth
                    C.TextArea {
                        id: body
                        objectName: "review-text"
                        placeholderText: qsTranslate("ProgramReviews", "What is good or bad about it?")
                        wrapMode: TextEdit.Wrap
                        KeyNavigation.priority: KeyNavigation.BeforeItem
                        KeyNavigation.tab: author
                        KeyNavigation.backtab: title
                        font.family: "Noto Sans"; font.pixelSize: 14
                        color: Shell.ShellPalette.managerInk
                        onTextChanged: if (length > 3000) remove(3000, length)
                        background: Rectangle { radius: 10; color: Shell.ShellPalette.managerBackground; border.width: body.activeFocus ? 2 : 1; border.color: body.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .2) }
                    }
                }
                C.TextField {
                    id: author
                    objectName: "review-name"
                    Layout.fillWidth: true
                    placeholderText: qsTranslate("ProgramReviews", "Your name as others see it (optional)")
                    maximumLength: 40
                    leftPadding: 10; rightPadding: 10
                    font.family: "Noto Sans"; font.pixelSize: 14
                    color: Shell.ShellPalette.managerInk
                    background: Rectangle { radius: 10; color: Shell.ShellPalette.managerBackground; border.width: author.activeFocus ? 2 : 1; border.color: author.activeFocus ? Shell.ShellPalette.accentForeground : Qt.alpha(Shell.ShellPalette.managerInk, .2) }
                }
                ManagerLabel {
                    text: qsTranslate("ProgramReviews", "Your review is public. It goes to the open ratings service at odrs.gnome.org, which GNOME Software and KDE Discover use too, with the name above or “Anonymous”. The service also keeps the network address it came from.")
                    color: Shell.ShellPalette.managerDim; font.pixelSize: 12; lineHeight: 1.3
                }
                ManagerLabel { objectName: "review-problem"; visible: dialog.problem !== ""; text: dialog.problem; color: Shell.ShellPalette.managerError; font.pixelSize: 13 }
                ManagerActions {
                    ManagerButton { text: qsTranslate("ProgramReviews", "Cancel"); enabled: !writer.running; onClicked: dialog.close() }
                    ManagerButton {
                        objectName: "review-send"
                        text: writer.running ? qsTranslate("ProgramReviews", "Sending…") : qsTranslate("ProgramReviews", "Send review")
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
}
