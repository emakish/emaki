pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Services.SystemTray

Item {
    readonly property var items: SystemTray.items.values
}
