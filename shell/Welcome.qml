pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "WelcomeContent.js" as Content

FocusScope {
    id: welcome
    property int page: 0
    property string wallpaperTexture: ""
    property real dpr: 1
    property string error: ""
    readonly property real inset: Math.max(28, (width - 744) / 2)
    readonly property var current: Content.pages[page]
    readonly property alias glassReady: glass.glassReady
    signal closeRequested
    signal shortcutsRequested
    implicitWidth: 800
    implicitHeight: 660
    focus: true
    function takeFocus(): void {
        next.forceActiveFocus();
    }
    function turn(delta: int): void {
        page = Math.max(0, Math.min(Content.pages.length - 1, page + delta));
        if (!back.enabled && back.activeFocus)
            next.forceActiveFocus();
    }
    function focusButtons(): var {
        const buttons = [close];
        for (let i = 0; i < tabs.count; ++i)
            buttons.push(tabs.itemAt(i));
        buttons.push(shortcuts);
        if (back.enabled)
            buttons.push(back);
        buttons.push(next);
        return buttons;
    }
    Keys.onPressed: event => {
        const buttons = focusButtons();
        if (event.key === Qt.Key_Escape) {
            closeRequested();
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
            const index = buttons.findIndex(b => b.activeFocus);
            const step = event.key === Qt.Key_Backtab || (event.modifiers & Qt.ShiftModifier) ? -1 : 1;
            buttons[(index + step + buttons.length) % buttons.length].forceActiveFocus();
        } else if (event.key === Qt.Key_Left) {
            turn(-1);
        } else if (event.key === Qt.Key_Right) {
            turn(1);
        } else if (event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
            body.contentY = Math.max(0, Math.min(body.contentHeight - body.height, body.contentY + (event.key === Qt.Key_Up ? -48 : 48)));
        } else {
            return;
        }
        event.accepted = true;
    }
    onPageChanged: body.contentY = 0
    Component.onCompleted: takeFocus()
    WelcomeGlass {
        id: glass
        anchors.fill: parent
        wallpaperTexture: welcome.wallpaperTexture
        dpr: welcome.dpr
    }
    component Copy: Text {
        color: LiquidPalette.inkOnLight
        font.family: ShellPalette.uiFont
        font.pixelSize: 15
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
    }
    Item {
        id: header
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
            margins: 28
            leftMargin: welcome.inset
            rightMargin: welcome.inset
        }
        height: 132
        Icon {
            kind: "logo"
            width: 34
            height: 34
            y: 2
        }
        Copy {
            x: 48
            text: "Welcome to Emaki"
            font.pixelSize: 28
            font.weight: Font.DemiBold
        }
        WelcomeButton {
            id: close
            objectName: "welcome-close"
            anchors.right: parent.right
            width: 42
            text: "×"
            Accessible.name: "Close welcome"
            onClicked: welcome.closeRequested()
        }
        Copy {
            y: 49
            width: parent.width
            font.pixelSize: 13
            text: "Super is the Windows key, or Command on a Mac keyboard.\nYour own shortcuts override Emaki defaults, including new ones (niri bindings)."
        }
        Row {
            y: 94
            spacing: 8
            Repeater {
                id: tabs
                model: Content.pages
                WelcomeButton {
                    required property int index
                    required property var modelData
                    objectName: "welcome-tab-" + index
                    text: (index + 1) + "  " + modelData.tab
                    selected: welcome.page === index
                    Accessible.role: Accessible.PageTab
                    Accessible.selected: selected
                    onClicked: welcome.page = index
                }
            }
        }
    }
    Flickable {
        id: body
        objectName: "welcome-body"
        anchors {
            top: header.bottom
            bottom: footer.top
            left: parent.left
            right: parent.right
            leftMargin: welcome.inset
            rightMargin: welcome.inset
            topMargin: 20
            bottomMargin: 16
        }
        clip: true
        contentWidth: width
        contentHeight: content.height
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        Column {
            id: content
            width: body.width - 12
            spacing: 14
            Copy {
                text: welcome.current.title
                width: parent.width
                font.pixelSize: 24
                font.weight: Font.DemiBold
            }
            Copy {
                text: welcome.current.description
                width: parent.width
            }
            Rectangle {
                width: parent.width
                height: 80
                radius: 14
                color: LiquidPalette.flatDrop
                Row {
                    visible: welcome.page !== 1
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 8
                    spacing: 8
                    Repeater {
                        model: ["Files", "Browser", "Notes"]
                        Rectangle {
                            id: diagramTile
                            required property int index
                            required property string modelData
                            width: welcome.page === 2 ? [100, 152, 100][index] : 112
                            height: 32
                            radius: 7
                            color: index === 1 ? LiquidPalette.selectedDrop : LiquidPalette.flatPanel
                            Copy {
                                anchors.centerIn: parent
                                text: diagramTile.modelData
                                font.pixelSize: 13
                            }
                        }
                    }
                }
                Column {
                    visible: welcome.page === 1
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 5
                    spacing: 2
                    Repeater {
                        model: 3
                        Rectangle {
                            id: workspaceTile
                            required property int index
                            width: 176
                            height: 14
                            radius: 4
                            color: index === 1 ? LiquidPalette.selectedDrop : LiquidPalette.flatPanel
                            Copy {
                                anchors.centerIn: parent
                                text: "Workspace " + (workspaceTile.index + 1)
                                font.pixelSize: 11
                            }
                        }
                    }
                }
                Copy {
                    anchors {
                        horizontalCenter: parent.horizontalCenter
                        bottom: parent.bottom
                        bottomMargin: 10
                    }
                    text: welcome.current.diagram
                    font.pixelSize: 13
                }
            }
            Flow {
                width: parent.width
                spacing: 16
                Repeater {
                    model: welcome.current.actions
                    Column {
                        id: action
                        required property var modelData
                        width: content.width >= 620 ? (content.width - 16) / 2 : content.width
                        spacing: 9
                        Copy {
                            width: parent.width
                            text: action.modelData.title
                            font.pixelSize: 18
                            font.weight: Font.DemiBold
                        }
                        Copy {
                            width: parent.width
                            text: action.modelData.pointer
                        }
                        Rectangle {
                            width: keyText.implicitWidth + 20
                            height: 32
                            radius: 8
                            color: LiquidPalette.flatDrop
                            border.color: LiquidPalette.flatDropRim
                            Copy {
                                id: keyText
                                anchors.centerIn: parent
                                text: action.modelData.keys
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                        }
                        Copy {
                            width: parent.width
                            text: action.modelData.keyboard
                            font.pixelSize: 14
                        }
                    }
                }
            }
        }
    }
    Column {
        id: footer
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            margins: 28
            leftMargin: welcome.inset
            rightMargin: welcome.inset
        }
        spacing: 10
        Copy {
            width: parent.width
            visible: welcome.error !== ""
            text: welcome.error
            color: LiquidPalette.dangerOnLight
            font.pixelSize: 13
            Accessible.role: Accessible.AlertMessage
        }
        Item {
            width: parent.width
            height: 42
            WelcomeButton {
                id: shortcuts
                objectName: "welcome-shortcuts"
                text: "All shortcuts"
                onClicked: welcome.shortcutsRequested()
            }
            Row {
                anchors.right: parent.right
                spacing: 10
                WelcomeButton {
                    id: back
                    objectName: "welcome-back"
                    text: "Back"
                    enabled: welcome.page > 0
                    onClicked: welcome.turn(-1)
                }
                WelcomeButton {
                    id: next
                    objectName: "welcome-next"
                    primary: true
                    text: welcome.page === Content.pages.length - 1 ? "Start using Emaki" : "Next"
                    onClicked: welcome.page === Content.pages.length - 1 ? welcome.closeRequested() : welcome.turn(1)
                }
            }
        }
        Copy {
            width: parent.width
            font.pixelSize: 13
            text: "Reopen Welcome to Emaki from the launcher anytime.\nFull shortcut list: Super + Shift + /    ·    Esc closes this welcome."
        }
    }
}
