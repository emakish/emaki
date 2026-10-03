pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "SystemIcons.js" as SystemIcons

// What the system panel shows (docs/mockups/liquid-glass/system.js buildView()), in the
// panel's frame: (0, 0) is its top left once grown, 480 wide, 22 px margins. The head
// (y < 52) is the panel's own: the island's cells at their pixels. The page starts at 52: a
// title with one line under it, sections in small capitals, sliders (a track on the plate,
// the knob a resting drop of the panel, the figure at the right), switches (a track, the knob
// a drop), rows without boxes (a symbol 20, the name, a line under it; the chosen one rests
// under a drop), words in the accent, round buttons for the power actions. Everything lies on
// the plate under the glass; pressable things are GlassTargets, resting drops are announced
// by `restKey` (SystemPanel collects them). A page taller than the screen scrolls under the
// head. The functions are the shell's (SystemService, NightLight, the tray, niri layouts); the
// look is new.
Item {
    id: body
    required property SystemService service
    required property NiriService niri
    // The SystemPanel: colours, drops. Null in tests of the body alone.
    property var glass: null
    property string page: ""
    property bool opened: false
    property string confirmation: ""
    property string selectedNetwork: ""
    property string layoutState: ""
    property string pairTarget: ""
    property bool hiddenOpen: false
    property bool hiddenSecured: true
    // Text of the password fields; cleared as soon as it has been handed to the service.
    property string wifiPassword: ""
    property string hiddenName: ""
    property string hiddenPassword: ""
    readonly property var wifiErrors: ({
            wrong_password: "Wrong password. Try again.",
            auth_timeout: "The network did not answer in time. Try again.",
            network_lost: "The network disappeared while connecting.",
            network_connection_failed: "Couldn’t connect to this network.",
            password_or_security_unsupported: "Password must be 8–63 characters; only open, WPA-PSK and WPA3 networks are supported here.",
            network_gone: "This network is no longer in range.",
            invalid_ssid: "Enter the network name (up to 32 bytes).",
            invalid_password: "Password must be 8–63 characters, or leave it empty for an open network.",
            activation_failed: "Couldn’t connect. Check the name and password.",
            network_not_found: "No network with that name was found.",
            nm_not_running: "NetworkManager is not running.",
            password_required: "This network needs a password.",
            timeout: "The connection attempt timed out.",
            no_handler: "No browser is set for http links.",
            open_failed: "The browser could not be opened."
        })
    property var selectedTray: null
    property var selectedSub: null
    readonly property alias trayMenu: menu
    readonly property alias traySubMenu: subMenu
    readonly property var backend: service.backend
    readonly property color ink: glass ? glass.ink : LiquidPalette.inkOnDark
    readonly property color dim: glass ? glass.dim : LiquidPalette.dimOnDark
    readonly property color faint: glass ? glass.faint : LiquidPalette.faintOnDark
    readonly property color accent: glass ? glass.accent : LiquidPalette.accentOnDark

    // ---- Geometry (system.js PANEL) ----
    readonly property int header: 52
    readonly property int side: 22
    readonly property real inner: width - 2 * side
    readonly property real bodyHeight: header + pages.implicitHeight + 12
    readonly property real desiredHeight: bodyHeight
    readonly property alias scroller: scroller
    readonly property alias pages: pages
    readonly property alias micMeterItem: micSlider.meterItem
    readonly property Item micMeterOwner: micSlider
    function wake(): void {
        if (glass && glass.wake)
            glass.wake();
    }

    function reset(): void {
        confirmation = "";
        selectedNetwork = "";
        layoutState = "";
        hiddenOpen = false;
        hiddenSecured = true;
        hiddenName = "";
        hiddenPassword = "";
        wifiPassword = "";
        selectedTray = null;
        selectedSub = null;
        scroller.contentY = 0;
    }
    function session(value: string): void {
        if (value === "reboot" || value === "poweroff" || value === "suspend")
            confirmation = value;
    }
    // The mic meter runs only while the sound page is open (PwNodePeakMonitor costs a stream).
    function updateMeter(): void {
        if (backend)
            backend.micMeter = opened && page === "sound";
    }
    onPageChanged: {
        reset();
        updateMeter();
    }
    onOpenedChanged: updateMeter()
    // Lists rebuilt (a device, a network's signal): their resting drops find the new rows.
    onRowsChanged: {
        const keys = page === "wifi" ? rows.map(r => r.key) : [];
        if (keys.join("\n") !== networkKeys.join("\n"))
            networkKeys = keys;
        wake();
    }
    // The Wi-Fi list's model: its keys change only when networks come or go.
    property var networkKeys: []
    onStreamGroupsChanged: wake()
    function confirmSession(): void {
        const value = confirmation;
        confirmation = "";
        if (value)
            service.act("session", value);
    }
    function hiddenSsidText(ssid: string, password: string): void {
        hiddenName = ssid;
        hiddenPassword = password;
    }
    function joinHidden(): void {
        service.act("hidden", {
            ssid: hiddenName,
            password: hiddenSecured ? hiddenPassword : ""
        });
        hiddenPassword = "";
    }
    function wifiConnect(): void {
        service.act("wifi-connect", {
            key: selectedNetwork,
            password: wifiPassword
        });
        wifiPassword = "";
    }

    // ---- Rows of the lists (one shape for every row: the delegates read every field) ----
    function entry(fields: var): var {
        return Object.assign({
            key: "",
            name: "",
            detail: "",
            action: "",
            value: "",
            icon: "",
            image: "",
            code: "",
            right: "",
            active: false,
            secondary: "",
            secondaryAction: "",
            fallback: ""
        }, fields);
    }
    // A PipeWire device as the mockup names it: "Speaker" over "Ryzen HD Audio Controller"
    // (node.description is the card's name followed by the profile's), Bluetooth by its name.
    function audioName(n: var): var {
        const p = n?.properties ?? {};
        const description = String(n?.description || n?.name || "");
        const profile = String(p["device.profile.description"] ?? "");
        const bus = String(p["device.bus"] ?? "");
        if (bus === "bluetooth")
            return {
                name: description,
                detail: "Bluetooth"
            };
        if (profile && description.endsWith(" " + profile))
            return {
                name: profile,
                detail: description.slice(0, description.length - profile.length - 1)
            };
        return {
            name: description,
            detail: bus === "usb" ? "USB" : n?.isSink ? "Output" : "Input"
        };
    }
    function signalOf(n: var): real {
        const s = Number(n?.signal ?? 0);
        return s > 1 ? s / 100 : s;
    }
    readonly property var rows: {
        if (page === "sound") {
            const nodes = Array.from(backend?.audioNodes ?? []);
            const row = (n, action, current) => {
                const named = audioName(n);
                return entry({
                    key: action + "-" + n.id,
                    name: named.name,
                    detail: named.detail,
                    action: action,
                    value: n.id,
                    icon: SystemIcons.audioDevice(n.properties?.["device.icon_name"] ?? "", action === "output"),
                    fallback: action === "output" ? "sound" : "mic",
                    active: current?.id === n.id
                });
            };
            return nodes.filter(n => n.isSink).map(n => row(n, "output", backend?.sink)).concat(nodes.filter(n => !n.isSink).map(n => row(n, "input", backend?.source)));
        }
        if (page === "wifi")
            return Array.from(backend?.networks ?? []).slice().sort((a, b) => (b.connected ? 1 : 0) - (a.connected ? 1 : 0) || signalOf(b) - signalOf(a)).map(n => entry({
                    key: "net-" + n.key,
                    name: n.name,
                    detail: n.busy ? "Connecting…" : n.connected ? (backend.connectivity === "portal" ? "Connected · sign-in required" : backend.connectivity === "limited" || backend.connectivity === "none" ? "Connected · no internet" : "Connected") : n.known ? "Saved" : n.open ? "Open network" : "Secured",
                    action: "network",
                    value: n.key,
                    icon: SystemIcons.signal(n.signal),
                    fallback: "wifi",
                    right: n.open ? "" : "network-wireless-encrypted-symbolic",
                    active: n.connected === true
                }));
        if (page === "bt") {
            // Mockup: "My devices" (paired) first, then "Nearby" found while searching.
            const list = Array.from(backend?.devices ?? []);
            const failed = pairTarget !== "" && service.actionState === "pairing_failed";
            return list.filter(d => d.paired).map(d => entry({
                    key: "dev-" + d.key,
                    name: d.name,
                    detail: d.connected ? "Connected" + (d.battery >= 0 ? " · " + d.battery + "%" : "") : "Not connected",
                    action: d.connected ? "bt-disconnect" : "bt-connect",
                    value: d.key,
                    icon: SystemIcons.bluetoothDevice(d.icon ?? ""),
                    fallback: "bt",
                    active: d.connected === true,
                    secondary: "Forget",
                    secondaryAction: "bt-forget"
                })).concat(list.filter(d => !d.paired).map(d => entry({
                    key: "near-" + d.key,
                    name: d.name,
                    detail: d.pairing ? "Pairing…" : failed && pairTarget === d.key ? "Couldn’t pair. Make sure it’s in pairing mode." : "Tap to pair",
                    action: d.pairing ? "bt-cancel-pair" : "bt-pair",
                    value: d.key,
                    icon: SystemIcons.bluetoothDevice(d.icon ?? ""),
                    fallback: "bt"
                })));
        }
        if (page === "power")
            return (service.profiles.profiles ?? []).map(p => entry({
                    key: "profile-" + p,
                    name: ({
                            "power-saver": "Power saver",
                            balanced: "Balanced",
                            performance: "Performance"
                        })[p] ?? p,
                    action: p === service.profiles.current ? "" : "profile",
                    value: p,
                    // Icon.qml's own shape, no theme: the running shell has no icon theme set.
                    fallback: ["power-saver", "balanced", "performance"].includes(p) ? p : "",
                    active: p === service.profiles.current
                }));
        if (page === "kb")
            return (niri.layouts?.names ?? []).map((n, i) => entry({
                    key: "layout-" + i,
                    name: n,
                    action: i === niri.layouts.current_idx ? "" : "layout",
                    value: i,
                    code: niri.layoutCode(n),
                    active: i === niri.layouts.current_idx
                }));
        if (page === "tray")
            return service.tray.items.map((t, i) => entry({
                    key: "tray-" + i,
                    name: t.title || t.id,
                    detail: t.tooltipTitle && t.tooltipTitle !== (t.title || t.id) ? t.tooltipTitle : "Running",
                    action: "tray",
                    value: i,
                    image: t.icon || ""
                }));
        return [];
    }
    function activateRow(row: var): void {
        if (row.action === "network") {
            const n = (backend?.networks ?? []).find(n => n.key === row.value);
            if (!n)
                return;
            // A saved or open network connects at once (system.js); the connected one opens its
            // Disconnect/Forget words; a new secured one asks for the password under its row.
            if (n.connected || n.busy)
                selectedNetwork = selectedNetwork === row.value ? "" : row.value;
            else if (n.known || n.open) {
                selectedNetwork = "";
                service.act("wifi-connect", {
                    key: row.value,
                    password: ""
                });
            } else {
                selectedNetwork = row.value;
                wifiPassword = "";
            }
        } else if (row.action === "bt-pair") {
            pairTarget = row.value;
            service.act("bt-pair", row.value);
        } else if (row.action === "layout") {
            layoutState = niri.switchLayout(row.value) ? "requested" : "niri_unavailable";
        } else if (row.action === "tray") {
            const t = service.tray.items[row.value];
            if (t?.hasMenu) {
                selectedSub = null;
                selectedTray = selectedTray === t ? null : t;
            } else if (t && !t.onlyMenu)
                t.activate();
        } else if (row.action)
            service.act(row.action, row.value);
    }
    readonly property var selected: (backend?.networks ?? []).find(n => n.key === selectedNetwork) ?? null
    readonly property var connectedNetwork: (backend?.networks ?? []).find(n => n.connected) ?? null
    readonly property bool wifiPresent: !!(backend?.networkReady && backend.wifiDevices.length)
    readonly property bool wifiOn: wifiPresent && backend.wifiHardwareEnabled && backend.wifiEnabled
    readonly property bool portal: page === "wifi" && backend?.connectivity === "portal" && connectedNetwork !== null
    readonly property var vpnRows: page === "wifi" ? (service.vpn.connections ?? []) : []
    readonly property bool btOn: backend?.adapter?.enabled ?? false
    // Playback streams by app (system.js STREAMS): five Firefox tabs are one slider.
    readonly property var streamGroups: {
        if (page !== "sound")
            return [];
        const groups = [];
        for (const s of Array.from(backend?.streams ?? [])) {
            if (!s.audio)
                continue;
            const name = String(s.name || "Unknown app");
            let g = groups.find(g => g.name === name);
            if (!g) {
                g = {
                    key: "stream-" + groups.length,
                    name: name,
                    icon: String(s.icon ?? ""),
                    details: [],
                    ids: [],
                    audio: s.audio
                };
                groups.push(g);
            }
            g.ids.push(s.id);
            if (s.detail)
                g.details.push(String(s.detail));
        }
        return groups.map(g => Object.assign(g, {
                detail: g.ids.length > 1 ? g.ids.length + " streams" : g.details[0] ?? ""
            }));
    }
    function appIcon(name: string, icon: string): string {
        const direct = icon ? Quickshell.iconPath(icon, true) : "";
        if (direct)
            return direct;
        const entry = name ? DesktopEntries.heuristicLookup(name) : null;
        return entry?.icon ? Quickshell.iconPath(entry.icon, true) : "";
    }
    function duration(seconds: real): string {
        const minutes = Math.round(seconds / 60);
        return minutes >= 60 ? Math.floor(minutes / 60) + " h " + minutes % 60 + " min" : minutes + " min";
    }
    readonly property int batteryPercent: service.batteryPercent
    readonly property string batterySub: {
        if (batteryPercent < 0)
            return "No battery";
        const b = backend?.battery;
        if (backend?.charging)
            return batteryPercent + "% · Charging" + (b?.timeToFull > 0 ? " · full in " + duration(b.timeToFull) : "");
        if (backend?.batteryPower)
            return batteryPercent + "%" + (b?.timeToEmpty > 0 ? " · " + duration(b.timeToEmpty) + " remaining" : " · On battery");
        return batteryPercent + "% · Connected to power";
    }
    readonly property string wifiSub: !backend?.networkReady ? "NetworkManager unavailable" : !backend.wifiDevices.length ? "No Wi-Fi adapter" : !backend.wifiHardwareEnabled ? "Wi-Fi is hardware blocked" : !backend.wifiEnabled ? "Off" : connectedNetwork ? "Connected to " + connectedNetwork.name : "Not connected"
    readonly property string btSub: {
        const a = backend?.adapter;
        if (!a)
            return "No Bluetooth adapter or service";
        if (!a.enabled)
            return "Off";
        if (a.discoverable && a.name)
            return "Discoverable as “" + a.name + "”";
        const connected = (backend.devices ?? []).filter(d => d.connected).length;
        return connected ? connected + " connected" : "On";
    }
    readonly property string nightDetail: {
        const s = service.night.state;
        return s === "on" ? "On · warmer colors" : s === "off" ? "Off" : s === "not_installed" ? "wlsunset is not installed" : s === "starting" ? "Starting…" : s === "checking" ? "Checking…" : s === "disabled" ? "Not available in this mode" : "Unavailable: " + s;
    }
    // One line at the foot of the page: what the last action did (only when it needs words),
    // then what the page cannot do yet.
    readonly property string message: {
        const s = service.actionState;
        if (s === "idle" || s === "confirmed")
            return "";
        if (page === "wifi" && wifiErrors[s])
            return wifiErrors[s];
        return ({
                pending: "Working…",
                busy: "Still busy with the last change.",
                requested: "Requested.",
                locked: "Screen locked.",
                lock_failed: "The screen lock was not confirmed. Sleep was cancelled.",
                confirmation_timeout: "The service did not confirm the change yet.",
                pairing_failed: "Couldn’t pair.",
                disabled: "Not available in this mode.",
                unavailable: "The service is unavailable."
            })[s] ?? s;
    }
    readonly property string note: page === "wifi" ? (service.vpn.state === "ready" || service.vpn.state === "unavailable" ? "" : "VPN list: " + service.vpn.state) : page === "bt" && rows.some(r => r.action === "bt-pair" || r.action === "bt-cancel-pair") ? "Devices that ask to type or compare a code need a Bluetooth agent; this shell has none yet." : page === "kb" ? (layoutState === "niri_unavailable" ? "niri is unavailable; switch with your keyboard shortcut." : layoutState === "requested" && ["rejected", "unconfirmed", "error"].includes(niri.actionState) ? "Switch not confirmed: " + niri.actionReason : "") : page === "tray" && selectedSub ? "Deeper menus than one level are not shown." : ""

    // ---- Parts (system.js drawItem) ----
    component Label: Text {
        textFormat: Text.PlainText
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        font.weight: Font.Medium
        color: body.dim
    }
    // An Adwaita symbol in the ink, or Icon.qml's shape without the theme.
    component Symbol: Item {
        id: symbolBox
        property string name: ""
        property string fallback: ""
        property color ink: body.ink
        width: 20
        height: 20
        SymbolIcon {
            id: themed
            width: symbolBox.width
            height: symbolBox.height
            name: symbolBox.name
            ink: symbolBox.ink
        }
        Icon {
            visible: !themed.found && symbolBox.fallback !== ""
            width: symbolBox.width
            height: symbolBox.height
            kind: symbolBox.fallback || "tray"
            ink: symbolBox.ink
        }
    }
    component Gap: Item {
        width: body.inner
    }
    component Title: Item {
        id: title
        required property string text
        property string sub: ""
        width: body.inner
        height: sub ? 56 : 40
        // h1 centred on 12, the line under it on 36.
        Label {
            y: 1
            height: 22
            width: parent.width - 60
            text: title.text
            font.pixelSize: 17
            font.weight: Font.DemiBold
            color: body.ink
        }
        Label {
            visible: title.sub !== ""
            y: 26
            height: 20
            width: parent.width - 60
            text: title.sub
        }
    }
    component Section: Item {
        id: section
        required property string text
        width: body.inner
        height: 26
        Label {
            height: 20
            width: parent.width
            text: section.text.toUpperCase()
            font.pixelSize: 11
            font.weight: Font.DemiBold
        }
    }
    // The switch: a flat track (the accent when on); the knob is a drop the panel draws, with
    // a dot on the glass (white on the accent, dim on the plate).
    component Toggle: GlassTarget {
        id: toggle
        property bool on: false
        glass: body.glass
        drop: false
        width: 38
        height: 20
        readonly property string restKey: key + "-knob"
        readonly property rect restRect: Qt.rect(on ? 18 : 0, -1, 20, 22)
        readonly property real restRadius: 10
        readonly property string restKind: "knob"
        onRestRectChanged: body.wake()
        Rectangle {
            anchors.fill: parent
            radius: 10
            color: toggle.on ? body.accent : body.faint
        }
        Accessible.role: Accessible.CheckBox
        Accessible.checked: on
    }
    component ToggleRow: Item {
        id: toggleRow
        required property string key
        required property string text
        property string detail: ""
        property bool on: false
        property bool available: true
        signal toggled
        width: body.inner
        height: detail ? 44 : 36
        onYChanged: body.wake()
        Label {
            y: toggleRow.detail ? -4 : 4
            height: 20
            width: parent.width - 60
            text: toggleRow.text
            font.pixelSize: 14
            color: body.ink
        }
        Label {
            visible: toggleRow.detail !== ""
            y: 16
            height: 18
            width: parent.width - 60
            text: toggleRow.detail
        }
        Toggle {
            x: toggleRow.width - 38
            y: 4
            key: toggleRow.key
            label: toggleRow.text
            on: toggleRow.on
            enabled: toggleRow.available
            onClicked: toggleRow.toggled()
        }
    }
    // A row: symbol 20 (or an app's image 26, or a layout code), the name, a line under it;
    // the chosen one rests under a drop of the row's size.
    component PanelRow: GlassTarget {
        id: panelRow
        property string icon: ""
        property string fallback: ""
        property string image: ""
        property string code: ""
        property string text: ""
        property string detail: ""
        property string rightIcon: ""
        property bool active: false
        glass: body.glass
        width: body.inner
        height: 44
        bubblePad: 0
        bubbleRadius: 12
        readonly property string restKey: active ? key : ""
        readonly property rect restRect: Qt.rect(0, 0, width, height)
        readonly property real restRadius: 12
        readonly property string restKind: "target"
        onRestKeyChanged: body.wake()
        onYChanged: body.wake()
        Symbol {
            x: 14
            y: 12
            visible: panelRow.icon !== "" || panelRow.fallback !== ""
            name: panelRow.icon
            fallback: panelRow.fallback
        }
        Image {
            id: rowImage
            x: 11
            y: 9
            width: 26
            height: 26
            visible: panelRow.image !== ""
            source: panelRow.image
            sourceSize: Qt.size(52, 52)
            smooth: true
            mipmap: true
        }
        Label {
            visible: panelRow.image !== "" && rowImage.status !== Image.Ready
            x: 11
            width: 26
            height: 44
            horizontalAlignment: Text.AlignHCenter
            text: panelRow.text.slice(0, 1).toLocaleUpperCase()
            font.pixelSize: 15
            font.weight: Font.DemiBold
        }
        Label {
            visible: panelRow.code !== ""
            width: 48
            y: .5
            height: 44
            horizontalAlignment: Text.AlignHCenter
            text: panelRow.code
            font.pixelSize: 12
            font.weight: Font.DemiBold
            color: body.ink
        }
        Label {
            x: 50
            y: panelRow.detail ? 4 : 12.5
            width: panelRow.width - 90
            height: 20
            text: panelRow.text
            font.pixelSize: 14
            color: body.ink
        }
        Label {
            visible: panelRow.detail !== ""
            x: 50
            y: 23
            width: panelRow.width - 90
            height: 18
            text: panelRow.detail
        }
        Symbol {
            x: panelRow.width - 30
            y: 15
            width: 14
            height: 14
            visible: panelRow.rightIcon !== ""
            name: panelRow.rightIcon
            ink: body.dim
        }
    }
    // A word in the accent (system.js button()).
    component Word: GlassTarget {
        id: word
        property color color: body.accent
        property bool strong: false
        glass: body.glass
        width: Math.ceil(wordLabel.implicitWidth) + 20
        height: 26
        bubblePad: 2
        bubbleRadius: 11
        Label {
            id: wordLabel
            anchors.centerIn: parent
            anchors.verticalCenterOffset: .5
            text: word.label
            elide: Text.ElideNone
            font.weight: word.strong ? Font.DemiBold : Font.Medium
            color: word.color
        }
    }
    // system.js slider(): the icon (a button when it mutes), the track on the plate with the
    // accent up to the knob (the knob is a resting drop; the fill ends under its real,
    // springing place), the figure at the right. Drag or click to set; the service hears the
    // value on release (one request, not one per pointer move).
    component PanelSlider: Item {
        id: slider
        required property string key
        property real value: 0
        property string icon: ""
        property string fallback: ""
        property bool iconPressable: false
        property string iconLabel: ""
        property bool off: false
        property bool available: true
        property real level: -1
        readonly property alias meterItem: meter
        property int minimum: 0
        property bool wheelSteps: false
        signal committed(int value)
        signal iconClicked
        signal stepped(int delta)
        width: body.inner
        height: 36
        property real dragValue: -1
        property real pendingValue: -1
        readonly property real shown: dragValue >= 0 ? dragValue : pendingValue >= 0 ? pendingValue : Math.max(0, Math.min(100, value))
        onValueChanged: {
            if (pendingValue >= 0 && Math.abs(value - pendingValue) < 1)
                pendingValue = -1;
        }
        readonly property real trackX: 40
        readonly property real trackWidth: width - 40 - 44
        readonly property string restKey: available ? key + "-knob" : ""
        readonly property rect restRect: Qt.rect(trackX + shown / 100 * trackWidth - 11, 10 - 7, 22, 22)
        readonly property real restRadius: 11
        readonly property string restKind: "slider"
        onRestRectChanged: body.wake()
        onYChanged: body.wake()
        // Where the knob drop really is (it springs after the value), on the track.
        readonly property real knobX: {
            const g = body.glass;
            if (g && restKey) {
                g.tick;
                const b = g.restBubble(restKey);
                if (b && b.placed && b.alpha.x > .001) {
                    const p = track.mapToItem(g.layerItem, 0, 0);
                    return b.x.x + b.w.x / 2 - p.x;
                }
            }
            return shown / 100 * trackWidth;
        }
        Timer {
            id: settle
            interval: 2500
            onTriggered: slider.pendingValue = -1
        }
        GlassTarget {
            visible: slider.icon !== ""
            glass: body.glass
            key: slider.key + "-icon"
            label: slider.iconLabel
            pressable: slider.iconPressable
            width: 28
            height: 28
            bubblePad: 0
            bubbleRadius: 12
            onClicked: slider.iconClicked()
            Symbol {
                x: 5
                y: 5
                width: 18
                height: 18
                name: slider.icon
                fallback: slider.fallback
                ink: slider.off ? body.faint : body.ink
            }
        }
        Rectangle {
            id: track
            x: slider.trackX
            y: 10
            width: slider.trackWidth
            height: 8
            radius: 4
            color: body.faint
        }
        Rectangle {
            visible: slider.available
            x: slider.trackX
            y: 10
            width: Math.max(8, Math.min(slider.trackWidth, slider.knobX))
            height: 8
            radius: 4
            color: slider.off ? body.faint : body.accent
        }
        // The microphone's live level, a hairline under its track while the page is open.
        MicMeter {
            id: meter
            parent: body.glass?.separateMeter ? body.glass.meterLayer : slider
            level: slider.level
            x: slider.trackX
            y: 21
            width: slider.trackWidth
            color: body.accent
        }
        Label {
            x: slider.width - 36
            width: 36
            height: 28
            horizontalAlignment: Text.AlignRight
            text: slider.available ? String(Math.round(slider.shown)) : "—"
            color: slider.off ? body.faint : body.dim
        }
        Item {
            id: hitArea
            x: slider.trackX - 11
            width: slider.trackWidth + 22
            height: 28
            // The pointer on a slider keeps no hover drop (its knob is one already).
            GlassTarget {
                id: proxy
                anchors.fill: parent
                glass: body.glass
                key: slider.key
                drop: false
                pressable: false
            }
            HoverHandler {
                enabled: slider.available
                cursorShape: Qt.PointingHandCursor
                onHoveredChanged: {
                    if (hovered && body.glass)
                        body.glass.hover(proxy);
                }
            }
            MouseArea {
                anchors.fill: parent
                enabled: slider.available
                preventStealing: true
                function valueAt(x: real): int {
                    return Math.round(Math.max(0, Math.min(1, (x + hitArea.x - slider.trackX) / slider.trackWidth)) * 100);
                }
                onPressed: mouse => slider.dragValue = valueAt(mouse.x)
                onPositionChanged: mouse => {
                    if (pressed)
                        slider.dragValue = valueAt(mouse.x);
                }
                onReleased: {
                    const v = Math.max(slider.minimum, Math.round(slider.dragValue));
                    slider.pendingValue = v;
                    settle.restart();
                    slider.dragValue = -1;
                    slider.committed(v);
                }
                onCanceled: slider.dragValue = -1
            }
            WheelHandler {
                enabled: slider.wheelSteps && slider.available
                onWheel: event => slider.stepped((event.angleDelta.y || event.angleDelta.x) > 0 ? 5 : -5)
            }
        }
        Accessible.role: Accessible.Slider
        Accessible.name: slider.iconLabel || slider.key
    }
    // An app over its stream slider: its icon 22, its name, what it plays.
    component AppHead: Item {
        id: appHead
        required property string text
        property string detail: ""
        property string image: ""
        width: body.inner
        height: 26
        Image {
            id: appImage
            y: 1
            width: 22
            height: 22
            visible: appHead.image !== ""
            source: appHead.image
            sourceSize: Qt.size(44, 44)
            smooth: true
            mipmap: true
        }
        Label {
            visible: appHead.image === "" || appImage.status !== Image.Ready
            width: 22
            height: 24
            horizontalAlignment: Text.AlignHCenter
            text: appHead.text.slice(0, 1).toLocaleUpperCase()
            font.weight: Font.DemiBold
        }
        Label {
            id: appName
            x: 30
            height: 24
            width: Math.min(implicitWidth, appHead.width - 30)
            text: appHead.text
            font.pixelSize: 14
            color: body.ink
        }
        Label {
            x: appName.x + appName.width + 8
            y: .5
            height: 24
            width: Math.max(0, appHead.width - x)
            text: appHead.detail
        }
    }
    // A text field on the plate (a network's password, a hidden network's name).
    component Field: Rectangle {
        id: field
        property string placeholder: ""
        property bool secret: false
        property int maximumLength: 63
        property alias text: input.text
        signal accepted
        width: body.inner
        height: 34
        radius: 10
        color: body.faint
        TextInput {
            id: input
            x: 12
            width: parent.width - 24
            height: parent.height
            verticalAlignment: TextInput.AlignVCenter
            clip: true
            color: body.ink
            selectionColor: body.accent
            selectedTextColor: LiquidPalette.text
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
            echoMode: field.secret ? TextInput.Password : TextInput.Normal
            maximumLength: field.maximumLength
            selectByMouse: true
            onAccepted: field.accepted()
        }
        Label {
            x: 12
            width: parent.width - 24
            height: parent.height
            visible: input.text === "" && !input.activeFocus
            text: field.placeholder
        }
    }
    // system.js 'round': a circle 52 on the plate with its symbol (Icon.qml's kind, in the ink),
    // the word under it.
    component Round: Item {
        id: round
        required property string key
        required property string kind
        required property string text
        signal clicked
        width: body.inner / 4
        height: 90
        GlassTarget {
            glass: body.glass
            key: round.key
            label: round.text
            x: (round.width - 52) / 2
            width: 52
            height: 52
            bubblePad: 0
            bubbleRadius: 28
            onClicked: round.clicked()
            Rectangle {
                anchors.fill: parent
                radius: 26
                color: body.faint
            }
            Icon {
                x: 15
                y: 15
                width: 22
                height: 22
                kind: round.kind
                ink: body.ink
            }
        }
        Label {
            y: 58
            width: round.width
            height: 20
            horizontalAlignment: Text.AlignHCenter
            text: round.text
        }
    }

    // One entry of a tray app's menu: a word-sized line, "›" for a submenu, a hairline for a
    // separator.
    component MenuLine: GlassTarget {
        id: line
        required property var entry
        signal picked
        glass: body.glass
        label: entry.text
        width: body.inner - 40
        height: entry.isSeparator ? 9 : 32
        pressable: !entry.isSeparator && entry.enabled
        bubblePad: 0
        bubbleRadius: 10
        onClicked: line.picked()
        Rectangle {
            visible: line.entry.isSeparator
            x: 10
            y: 4
            width: parent.width - 20
            height: 1
            color: body.faint
        }
        Label {
            visible: !line.entry.isSeparator
            x: 10
            width: parent.width - 20
            height: 32
            text: line.entry.text + (line.entry.hasChildren ? "  ›" : "")
            color: line.entry.enabled ? body.ink : body.faint
        }
    }

    // ---- The page (under the head; scrolls when taller than the screen allows) ----
    Flickable {
        id: scroller
        y: body.header
        width: body.width
        height: Math.max(0, body.height - body.header)
        contentWidth: width
        contentHeight: pages.implicitHeight + 12
        clip: true
        interactive: contentHeight > height + .5
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        onContentYChanged: body.wake()
        Column {
            id: pages
            x: body.side
            width: body.inner
            onImplicitHeightChanged: body.wake()

            // -- Sound --
            Column {
                width: parent.width
                visible: body.page === "sound"
                Title {
                    text: "Sound"
                    sub: body.backend?.audioReady ? (body.backend.sink ? body.audioName(body.backend.sink).name : "No output") : "PipeWire unavailable"
                }
                PanelSlider {
                    key: "volume"
                    visible: !!body.backend?.sink?.audio
                    value: (body.backend?.sink?.audio?.volume ?? 0) * 100
                    icon: SystemIcons.sound(true, body.service.sinkVolume, body.service.sinkMuted)
                    fallback: body.service.sinkMuted ? "muted" : "sound"
                    iconPressable: true
                    iconLabel: body.service.sinkMuted ? "Unmute" : "Mute"
                    off: body.service.sinkMuted
                    wheelSteps: true
                    onIconClicked: body.service.act("mute", null)
                    onCommitted: v => body.service.act("volume", v)
                    onStepped: delta => body.service.step("sound", delta)
                }
                PanelSlider {
                    id: micSlider
                    key: "mic"
                    visible: !!body.backend?.source?.audio
                    value: (body.backend?.source?.audio?.volume ?? 0) * 100
                    icon: body.backend?.source?.audio?.muted ? "microphone-sensitivity-muted-symbolic" : "audio-input-microphone-symbolic"
                    fallback: "mic"
                    iconPressable: true
                    iconLabel: body.backend?.source?.audio?.muted ? "Enable microphone" : "Mute microphone"
                    off: body.backend?.source?.audio?.muted ?? false
                    level: body.backend?.micMeter ? (body.backend?.micLevel ?? 0) : -1
                    onIconClicked: body.service.act("mic", null)
                    onCommitted: v => body.service.act("mic-volume", v)
                }
                Gap {
                    height: 6
                    visible: body.streamGroups.length > 0
                }
                Section {
                    text: "Apps"
                    visible: body.streamGroups.length > 0
                }
                Repeater {
                    model: body.streamGroups
                    Column {
                        id: stream
                        required property var modelData
                        width: body.inner
                        AppHead {
                            text: stream.modelData.name
                            detail: stream.modelData.detail
                            image: body.appIcon(stream.modelData.name, stream.modelData.icon)
                        }
                        PanelSlider {
                            key: stream.modelData.key
                            value: (stream.modelData.audio?.volume ?? 0) * 100
                            onCommitted: v => body.service.act("stream-volume", {
                                    ids: stream.modelData.ids,
                                    percent: v
                                })
                        }
                    }
                }
                Gap {
                    height: 6
                }
                Section {
                    text: "Output"
                    visible: body.rows.some(r => r.action === "output")
                }
                Repeater {
                    model: body.page === "sound" ? body.rows.filter(r => r.action === "output") : []
                    delegate: rowDelegate
                }
                Gap {
                    height: 10
                    visible: body.rows.some(r => r.action === "input")
                }
                Section {
                    text: "Input"
                    visible: body.rows.some(r => r.action === "input")
                }
                Repeater {
                    model: body.page === "sound" ? body.rows.filter(r => r.action === "input") : []
                    delegate: rowDelegate
                }
            }

            // -- Display --
            Column {
                width: parent.width
                visible: body.page === "light"
                Title {
                    text: "Display"
                    sub: body.service.brightness.state === "ready" ? "Built-in display" : body.service.brightness.state === "unavailable" ? "No brightness control" : "Brightness: " + body.service.brightness.state
                }
                PanelSlider {
                    key: "brightness"
                    available: body.service.brightness.state === "ready"
                    value: body.service.brightness.percent ?? 0
                    icon: "display-brightness-symbolic"
                    fallback: "light"
                    minimum: 5
                    wheelSteps: true
                    onCommitted: v => body.service.act("brightness", v)
                    onStepped: delta => body.service.step("light", delta)
                }
                Gap {
                    height: 6
                }
                ToggleRow {
                    key: "night"
                    text: "Night Light"
                    detail: body.nightDetail
                    on: body.service.night.on
                    available: ["on", "off", "starting"].includes(body.service.night.state)
                    onToggled: body.service.night.setOn(!body.service.night.on)
                }
                PanelSlider {
                    key: "warmth"
                    value: body.service.night.warmth
                    icon: "night-light-symbolic"
                    iconLabel: "Warmth"
                    // Warmth can be set before the light is on (system.js); it is stored and
                    // used when Night Light starts.
                    off: !body.service.night.on
                    available: ["on", "off", "starting"].includes(body.service.night.state)
                    onCommitted: v => body.service.night.setWarmth(v)
                }
            }

            // -- Wi-Fi --
            Column {
                width: parent.width
                visible: body.page === "wifi"
                Title {
                    id: wifiTitle
                    text: "Wi-Fi"
                    sub: body.wifiSub
                    Toggle {
                        x: body.inner - 38
                        y: 9
                        key: "wifi-on"
                        label: "Wi-Fi"
                        visible: body.wifiPresent
                        enabled: body.backend?.wifiHardwareEnabled ?? false
                        on: body.wifiOn
                        onClicked: body.service.act("wifi-power", null)
                    }
                }
                Item {
                    width: body.inner
                    height: 62
                    visible: body.portal
                    Label {
                        width: parent.width
                        height: 28
                        text: "This network wants you to sign in before the internet works."
                        color: body.ink
                    }
                    Word {
                        x: -10
                        y: 30
                        key: "portal"
                        label: "Open sign-in page"
                        onClicked: body.service.act("portal", null)
                    }
                }
                Section {
                    text: "Networks"
                    visible: body.wifiOn
                }
                Repeater {
                    // Keyed by network, not by row: the rows are rebuilt on every change of a
                    // signal, and a rebuilt delegate would take the password field's focus away.
                    model: body.page === "wifi" && body.wifiOn ? body.networkKeys : []
                    Column {
                        id: network
                        required property string modelData
                        readonly property var row: body.rows.find(r => r.key === network.modelData) ?? body.entry({})
                        readonly property bool open: body.selectedNetwork === row.value && body.selected !== null
                        width: body.inner
                        PanelRow {
                            key: network.modelData
                            label: network.row.name
                            text: network.row.name
                            detail: network.row.detail
                            icon: network.row.icon
                            fallback: "wifi"
                            rightIcon: network.row.right
                            active: network.row.active
                            onClicked: body.activateRow(network.row)
                        }
                        Gap {
                            height: 2
                        }
                        // Under the chosen network: its password, or what can be done with it.
                        Column {
                            x: 50
                            width: body.inner - 50
                            visible: network.open
                            spacing: 6
                            Field {
                                width: parent.width
                                visible: !!body.selected && !body.selected.connected && !body.selected.known && !body.selected.open
                                secret: true
                                placeholder: "Password"
                                text: body.wifiPassword
                                onTextChanged: body.wifiPassword = text
                                onAccepted: body.wifiConnect()
                            }
                            Row {
                                x: -10
                                spacing: 2
                                Word {
                                    key: network.row.key + ":go"
                                    label: body.selected?.connected ? "Disconnect" : "Connect"
                                    onClicked: {
                                        if (body.selected?.connected)
                                            body.service.act("wifi-disconnect", {
                                                key: body.selectedNetwork
                                            });
                                        else
                                            body.wifiConnect();
                                    }
                                }
                                Word {
                                    key: network.row.key + ":forget"
                                    label: "Forget network"
                                    visible: body.selected?.known ?? false
                                    onClicked: body.service.act("wifi-forget", {
                                        key: body.selectedNetwork
                                    })
                                }
                                Word {
                                    key: network.row.key + ":cancel"
                                    label: "Cancel"
                                    onClicked: {
                                        body.selectedNetwork = "";
                                        body.wifiPassword = "";
                                    }
                                }
                            }
                            Gap {
                                height: 2
                            }
                        }
                    }
                }
                // system.js: the word 6 px in, 40 px for the line.
                Item {
                    width: body.inner
                    height: 40
                    visible: body.wifiOn
                    Word {
                        x: 6
                        y: 2
                        key: "hidden"
                        label: body.hiddenOpen ? "Join hidden network" : "Join hidden network…"
                        onClicked: body.hiddenOpen = !body.hiddenOpen
                    }
                }
                Column {
                    width: body.inner
                    visible: body.wifiOn && body.hiddenOpen
                    spacing: 6
                    Field {
                        placeholder: "Network name"
                        secret: false
                        maximumLength: 32
                        text: body.hiddenName
                        onTextChanged: body.hiddenName = text
                        onAccepted: body.joinHidden()
                    }
                    Row {
                        x: -10
                        spacing: 2
                        Word {
                            key: "hidden-open"
                            label: "Open"
                            strong: !body.hiddenSecured
                            color: body.hiddenSecured ? body.dim : body.ink
                            onClicked: body.hiddenSecured = false
                        }
                        Word {
                            key: "hidden-secured"
                            label: "Password"
                            strong: body.hiddenSecured
                            color: body.hiddenSecured ? body.ink : body.dim
                            onClicked: body.hiddenSecured = true
                        }
                    }
                    Field {
                        visible: body.hiddenSecured
                        placeholder: "Password"
                        secret: true
                        text: body.hiddenPassword
                        onTextChanged: body.hiddenPassword = text
                        onAccepted: body.joinHidden()
                    }
                    Row {
                        x: -10
                        spacing: 2
                        Word {
                            key: "hidden-cancel"
                            label: "Cancel"
                            onClicked: {
                                body.hiddenOpen = false;
                                body.hiddenName = "";
                                body.hiddenPassword = "";
                            }
                        }
                        Word {
                            key: "hidden-join"
                            label: "Join"
                            onClicked: body.joinHidden()
                        }
                    }
                    Gap {
                        height: 8
                    }
                }
                Section {
                    text: "VPN"
                    visible: body.vpnRows.length > 0
                }
                Repeater {
                    model: body.vpnRows
                    ToggleRow {
                        required property var modelData
                        key: "vpn-" + modelData.uuid
                        text: modelData.name
                        detail: (modelData.kind === "wireguard" ? "WireGuard" : "VPN") + " · " + (modelData.active ? "Connected" : "Not connected")
                        on: modelData.active
                        onToggled: body.service.act("vpn", {
                            uuid: modelData.uuid,
                            up: !modelData.active
                        })
                    }
                }
            }

            // -- Bluetooth --
            Column {
                width: parent.width
                visible: body.page === "bt"
                Title {
                    text: "Bluetooth"
                    sub: body.btSub
                    Toggle {
                        x: body.inner - 38
                        y: 9
                        key: "bt-on"
                        label: "Bluetooth"
                        visible: !!body.backend?.adapter
                        on: body.btOn
                        onClicked: body.service.act("bt-power", null)
                    }
                }
                Section {
                    text: "My devices"
                    visible: body.btOn && body.rows.some(r => r.secondaryAction === "bt-forget")
                }
                Repeater {
                    model: body.page === "bt" && body.btOn ? body.rows.filter(r => r.secondaryAction === "bt-forget") : []
                    Item {
                        id: pairedBlock
                        required property var modelData
                        width: body.inner
                        height: 46
                        onYChanged: body.wake()
                        PanelRow {
                            id: device
                            // The row or its Forget word holds the hover drop.
                            readonly property bool hot: body.glass ? body.glass.hoverKey === key || body.glass.hoverKey === key + ":forget" : false
                            key: pairedBlock.modelData.key
                            label: pairedBlock.modelData.name
                            text: pairedBlock.modelData.name
                            detail: pairedBlock.modelData.detail
                            icon: pairedBlock.modelData.icon
                            fallback: "bt"
                            active: pairedBlock.modelData.active
                            onClicked: body.activateRow(pairedBlock.modelData)
                            // Forgetting a device was in the shell; the mockup has no place for
                            // it, so it shows on the hovered row only.
                            Word {
                                x: device.width - width - 6
                                y: 9
                                visible: device.hot
                                owner: device
                                key: device.key + ":forget"
                                label: "Forget"
                                onClicked: body.activateRow({
                                    action: "bt-forget",
                                    value: pairedBlock.modelData.value
                                })
                            }
                        }
                    }
                }
                Gap {
                    height: 10
                    visible: body.btOn
                }
                Item {
                    width: body.inner
                    height: 26
                    visible: body.btOn
                    Label {
                        height: 20
                        text: "NEARBY"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                    Word {
                        x: body.inner - width
                        y: -4
                        key: "search"
                        label: body.backend?.adapter?.discovering ? "Stop" : "Search"
                        onClicked: body.service.act("bt-scan", null)
                    }
                }
                Repeater {
                    model: body.page === "bt" && body.btOn ? body.rows.filter(r => r.secondaryAction !== "bt-forget") : []
                    delegate: rowDelegate
                }
                Item {
                    width: body.inner
                    height: 30
                    visible: body.btOn && !body.rows.some(r => r.secondaryAction !== "bt-forget")
                    Label {
                        width: parent.width
                        height: 24
                        text: body.backend?.adapter?.discovering ? "Searching…" : "Put a device in pairing mode, then press Search."
                    }
                }
            }

            // -- Battery and power --
            Column {
                width: parent.width
                visible: body.page === "power"
                Title {
                    text: body.batteryPercent >= 0 ? "Battery" : "Power"
                    sub: body.batterySub
                }
                Section {
                    text: "Power mode"
                    visible: body.page === "power" && body.rows.length > 0
                }
                Repeater {
                    model: body.page === "power" ? body.rows : []
                    delegate: rowDelegate
                }
                Gap {
                    height: 4
                }
                Section {
                    text: "Power"
                }
                Row {
                    width: body.inner
                    Round {
                        key: "act-lock"
                        kind: "lock"
                        text: "Lock"
                        onClicked: {
                            body.confirmation = "";
                            body.service.act("lock", null);
                        }
                    }
                    Round {
                        key: "act-sleep"
                        kind: "sleep"
                        text: "Sleep"
                        onClicked: body.confirmation = body.confirmation === "suspend" ? "" : "suspend"
                    }
                    Round {
                        key: "act-reboot"
                        kind: "restart"
                        text: "Restart"
                        onClicked: body.confirmation = body.confirmation === "reboot" ? "" : "reboot"
                    }
                    Round {
                        key: "act-off"
                        kind: "shutdown"
                        text: "Shut down"
                        onClicked: body.confirmation = body.confirmation === "poweroff" ? "" : "poweroff"
                    }
                }
                // "Shut down now? · Save your work first." with Cancel and the action.
                Item {
                    id: confirmBlock
                    width: body.inner
                    height: 48
                    visible: body.confirmation !== ""
                    readonly property string action: body.confirmation === "reboot" ? "Restart" : body.confirmation === "suspend" ? "Sleep" : "Shut down"
                    Label {
                        y: -4
                        width: body.inner - 180
                        height: 20
                        text: confirmBlock.action + " now?"
                        font.pixelSize: 14
                        color: body.ink
                    }
                    Label {
                        y: 16
                        width: body.inner - 180
                        height: 18
                        text: body.confirmation === "suspend" ? "The screen locks first." : "Save your work first."
                    }
                    Row {
                        x: body.inner - width
                        y: 2
                        spacing: 4
                        Word {
                            key: "confirm-no"
                            label: "Cancel"
                            onClicked: body.confirmation = ""
                        }
                        Word {
                            key: "confirm-go"
                            label: confirmBlock.action
                            onClicked: body.confirmSession()
                        }
                    }
                }
            }

            // -- Keyboard --
            Column {
                width: parent.width
                visible: body.page === "kb"
                Title {
                    text: "Keyboard"
                    sub: "Super+Space switches the layout"
                }
                Repeater {
                    model: body.page === "kb" ? body.rows : []
                    delegate: rowDelegate
                }
            }

            // -- Background apps (the tray) and a chosen app's menu --
            Column {
                width: parent.width
                visible: body.page === "tray"
                Title {
                    text: "Background apps"
                    sub: body.service.tray.state === "active" ? body.service.tray.items.length + " running" : body.service.tray.state === "owned_elsewhere" ? "The tray is shown by another program" : body.service.tray.state === "disabled" ? "The tray is off in this session" : "Tray: " + body.service.tray.state
                }
                Repeater {
                    model: body.page === "tray" ? body.rows : []
                    Column {
                        id: trayApp
                        required property var modelData
                        readonly property bool open: body.selectedTray !== null && body.service.tray.items[modelData.value] === body.selectedTray
                        width: body.inner
                        PanelRow {
                            key: trayApp.modelData.key
                            label: trayApp.modelData.name
                            text: trayApp.modelData.name
                            detail: trayApp.modelData.detail
                            image: trayApp.modelData.image
                            active: trayApp.open
                            onClicked: body.activateRow(trayApp.modelData)
                        }
                        Gap {
                            height: 2
                        }
                        Column {
                            x: 40
                            width: body.inner - 40
                            visible: trayApp.open
                            Repeater {
                                model: trayApp.open ? menu.children.values : []
                                MenuLine {
                                    required property var modelData
                                    required property int index
                                    entry: modelData
                                    key: trayApp.modelData.key + ":m" + index
                                    onPicked: {
                                        if (modelData.hasChildren)
                                            body.selectedSub = body.selectedSub === modelData ? null : modelData;
                                        else
                                            modelData.triggered();
                                    }
                                }
                            }
                            Repeater {
                                model: trayApp.open && body.selectedSub ? subMenu.children.values : []
                                MenuLine {
                                    required property var modelData
                                    required property int index
                                    x: 16
                                    width: body.inner - 56
                                    entry: modelData
                                    key: trayApp.modelData.key + ":s" + index
                                    onPicked: {
                                        if (!modelData.hasChildren)
                                            modelData.triggered();
                                    }
                                }
                            }
                            Gap {
                                height: 6
                            }
                        }
                    }
                }
            }

            // -- The foot: the last action, what the page cannot do yet --
            Item {
                width: body.inner
                height: footText.implicitHeight + 8
                visible: footText.text !== ""
                Text {
                    id: footText
                    y: 4
                    width: parent.width
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: [body.message, body.note].filter(Boolean).join("\n")
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 12
                    font.weight: Font.Medium
                    lineHeight: 1.2
                    color: body.dim
                }
            }
        }
    }
    // A row of a list with the 2 px under it (system.js row(): 44 + 2).
    Component {
        id: rowDelegate
        Item {
            id: block
            required property var modelData
            width: body.inner
            height: 46
            onYChanged: body.wake()
            PanelRow {
                key: block.modelData.key
                label: block.modelData.name
                text: block.modelData.name
                detail: block.modelData.detail
                icon: block.modelData.icon
                code: block.modelData.code
                fallback: block.modelData.fallback
                image: block.modelData.image
                active: block.modelData.active
                onClicked: body.activateRow(block.modelData)
            }
        }
    }
    QsMenuOpener {
        id: menu
        menu: body.selectedTray?.menu ?? null
    }
    QsMenuOpener {
        id: subMenu
        menu: body.selectedSub
    }
}
