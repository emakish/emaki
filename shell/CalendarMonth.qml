pragma ComponentBehavior: Bound
import QtQuick

// The month on the clock panel (docs/mockups/liquid-glass/clock.js, calendar): no box, the
// month as a heading, pan-start/pan-end arrows, "Today" in the accent once you leave the
// month; today is a resting drop 42 × 26 whose digit lies on the glass, the picked day is its
// digit in the accent. Every day of the month and every arrow is a GlassTarget.
Item {
    id: calendar
    property var glass: null
    property date today: new Date()
    property int year: today.getFullYear()
    property int month: today.getMonth()
    property int picked: 0
    readonly property int start: (new Date(year, month, 1).getDay() + 6) % 7
    readonly property int days: new Date(year, month + 1, 0).getDate()
    readonly property int cellCount: Math.ceil((start + days) / 7) * 7
    readonly property bool current: year === today.getFullYear() && month === today.getMonth()
    readonly property real cell: width / 7
    readonly property color ink: glass ? glass.ink : LiquidPalette.inkOnDark
    readonly property color dim: glass ? glass.dim : LiquidPalette.dimOnDark
    readonly property color faint: glass ? glass.faint : LiquidPalette.faintOnDark
    readonly property color accent: glass ? glass.accent : LiquidPalette.accentOnDark
    readonly property var monthNames: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    // Today's cell while its month is shown: a resting drop of the panel.
    readonly property GlassTarget todayTarget: current && dayRepeater.count === cellCount ? dayRepeater.itemAt(start + today.getDate() - 1) as GlassTarget : null
    function shift(delta: int): void {
        const next = new Date(year, month + delta, 1);
        year = next.getFullYear();
        month = next.getMonth();
        picked = 0;
    }
    implicitHeight: 58 + cellCount / 7 * 28
    height: implicitHeight
    Text {
        width: 200
        height: 28
        verticalAlignment: Text.AlignVCenter
        text: calendar.monthNames[calendar.month] + " " + calendar.year
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 14
        font.weight: Font.DemiBold
        color: calendar.ink
    }
    component Arrow: GlassTarget {
        id: arrow
        required property string symbol
        required property string glyph
        glass: calendar.glass
        width: 28
        height: 28
        bubblePad: 0
        bubbleRadius: 12
        SymbolIcon {
            id: arrowIcon
            x: 7
            y: 7
            width: 14
            height: 14
            name: arrow.symbol
            ink: calendar.dim
        }
        Text {
            anchors.fill: parent
            visible: !arrowIcon.found
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: arrow.glyph
            textFormat: Text.PlainText
            font.pixelSize: 14
            color: calendar.dim
        }
    }
    Arrow {
        x: calendar.width - 28
        key: "month-next"
        label: "Next month"
        symbol: "pan-end-symbolic"
        glyph: "›"
        onClicked: calendar.shift(1)
    }
    Arrow {
        x: calendar.width - 58
        key: "month-previous"
        label: "Previous month"
        symbol: "pan-start-symbolic"
        glyph: "‹"
        onClicked: calendar.shift(-1)
    }
    GlassTarget {
        id: todayButton
        glass: calendar.glass
        key: "month-today"
        label: "Today"
        visible: !calendar.current
        x: calendar.width - 60 - width
        y: 1
        width: todayLabel.implicitWidth + 20
        height: 26
        onClicked: {
            calendar.year = calendar.today.getFullYear();
            calendar.month = calendar.today.getMonth();
            calendar.picked = 0;
        }
        Text {
            id: todayLabel
            anchors.centerIn: parent
            anchors.verticalCenterOffset: .5
            text: "Today"
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: calendar.accent
        }
    }
    Repeater {
        model: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        Text {
            required property int index
            required property string modelData
            x: index * calendar.cell
            y: 34
            width: calendar.cell
            height: 22
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: modelData
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: calendar.dim
        }
    }
    // A day of the month is a 42 × 26 target centred in its cell; the neighbours' days are
    // faint digits only.
    Repeater {
        id: dayRepeater
        model: calendar.cellCount
        GlassTarget {
            id: day
            required property int index
            readonly property int number: index - calendar.start + 1
            readonly property bool inMonth: number > 0 && number <= calendar.days
            readonly property bool isToday: inMonth && calendar.current && number === calendar.today.getDate()
            glass: calendar.glass
            key: "day-" + calendar.year + "-" + calendar.month + "-" + index
            label: String(number)
            pressable: inMonth
            x: (index % 7) * calendar.cell + calendar.cell / 2 - 21
            y: 58 + Math.floor(index / 7) * 28 + 1
            width: 42
            height: 26
            bubblePad: 0
            bubbleRadius: 12
            onClicked: calendar.picked = number
            Text {
                anchors.fill: parent
                anchors.topMargin: 1
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: String(day.number < 1 ? new Date(calendar.year, calendar.month, 0).getDate() + day.number : day.number > calendar.days ? day.number - calendar.days : day.number)
                textFormat: Text.PlainText
                font.family: ShellPalette.uiFont
                font.pixelSize: 13
                font.weight: Font.Medium
                color: !day.inMonth ? calendar.faint : day.isToday ? calendar.ink : day.number === calendar.picked ? calendar.accent : calendar.ink
            }
        }
    }
}
