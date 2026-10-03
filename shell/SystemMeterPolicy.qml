pragma ComponentBehavior: Bound
import QtQml

// Resource lifetime is independent of temporary geometry/clip fallback. Hovering,
// scrolling and row rebuilds may put the meter back in the content texture, but
// must not destroy the retained panel texture on every such transition.
QtObject {
    property bool glassReady: false
    property bool opened: false
    property bool soundPage: false
    property bool settled: false
    property bool meterVisible: false
    property bool insideView: false
    property bool insidePlate: false
    readonly property bool cacheEnabled: glassReady && opened && soundPage
    readonly property bool separateMeter: cacheEnabled && settled && meterVisible && insideView && insidePlate
}
