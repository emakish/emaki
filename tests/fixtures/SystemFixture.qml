pragma ComponentBehavior: Bound
import QtQuick

SystemBackend {
    id: fixture
    audioReady: true
    property bool deny: false
    property string failWith: "wrong_password"
    connectivity: "full"
    captures: []
    property double percent: .82
    sink: output1
    source: input1
    audioNodes: [output1, output2, input1, input2]
    streams: [
        {
            id: 9,
            name: "PRIVATE_APP",
            detail: "PRIVATE_MEDIA",
            audio: audio5
        }
    ]
    micLevel: .4
    battery: bat
    batteryPower: true
    networkReady: true
    wifiEnabled: true
    wifiHardwareEnabled: true
    wifiDevices: [({
                name: "fixture"
            })]
    networks: [
        {
            key: "fixture",
            name: "PRIVATE_WIFI",
            connected: network.connected,
            known: network.known,
            busy: false,
            open: false,
            psk: true,
            ref: network
        }
    ]
    adapter: btAdapter
    property bool forgotten: false
    property string pairOutcome: "ok"
    devices: [
        {
            key: "fixture-device",
            name: "PRIVATE_BT",
            connected: device.connected,
            paired: device.paired,
            pairing: false,
            battery: 90,
            ref: device
        }
    ].concat(forgotten ? [] : [
        {
            key: "fixture-nearby",
            name: "PRIVATE_BT2",
            connected: false,
            paired: nearby.paired,
            pairing: nearby.pairing,
            battery: -1,
            ref: nearby
        }
    ])
    property var output1: ({
            id: 1,
            name: "Speakers",
            description: "Built-in speakers",
            isSink: true,
            audio: audio1
        })
    property var output2: ({
            id: 2,
            name: "Headphones",
            description: "Headphones",
            isSink: true,
            audio: audio2
        })
    property var input1: ({
            id: 3,
            name: "Built-in microphone",
            description: "Built-in microphone",
            isSink: false,
            audio: audio3
        })
    property var input2: ({
            id: 4,
            name: "Headset microphone",
            description: "Headset microphone",
            isSink: false,
            audio: audio4
        })
    QtObject {
        id: audio4
        property double volume: .5
        property bool muted: false
    }
    QtObject {
        id: audio5
        property double volume: .8
        property bool muted: false
    }
    QtObject {
        id: audio1
        property double volume: .64
        property bool muted: false
    }
    QtObject {
        id: audio2
        property double volume: .32
        property bool muted: false
    }
    QtObject {
        id: audio3
        property double volume: .5
        property bool muted: false
    }
    QtObject {
        id: bat
        property double percentage: fixture.percent
        property bool isPresent: true
        property bool ready: true
    }
    QtObject {
        id: network
        property bool known: true
        property bool connected: true
        function connect(): void {
            if (!fixture.deny)
                connected = true;
        }
        function disconnect(): void {
            if (!fixture.deny)
                connected = false;
        }
        function forget(): void {
            if (!fixture.deny)
                known = false;
        }
        function connectWithPsk(password: string): void {
            if (!fixture.deny && password === "fixture-password")
                connected = true;
            else
                fixture.actionError = fixture.failWith;
        }
    }
    QtObject {
        id: btAdapter
        property bool enabled: true
        property bool discovering: false
    }
    QtObject {
        id: nearby
        property bool paired: false
        property bool pairing: false
        function pair(): void {
            pairing = true;
            pairDone.restart();
        }
        function cancelPair(): void {
            pairDone.stop();
            pairing = false;
        }
        function forget(): void {
            fixture.forgotten = true;
        }
    }
    Timer {
        id: pairDone
        interval: 300
        onTriggered: {
            nearby.pairing = false;
            if (fixture.pairOutcome === "ok")
                nearby.paired = true;
        }
    }
    QtObject {
        id: device
        property bool paired: true
        property bool connected: false
        function connect(): void {
            if (!fixture.deny)
                connected = true;
        }
        function disconnect(): void {
            if (!fixture.deny)
                connected = false;
        }
    }
    function chooseOutput(id: int): var {
        const node = audioNodes.find(n => n.id === id && n.isSink);
        if (!node)
            return "output_gone";
        if (!deny)
            sink = node;
        return () => sink.id === id;
    }
    function chooseInput(id: int): var {
        const node = audioNodes.find(n => n.id === id && !n.isSink);
        if (!node)
            return "input_gone";
        if (!deny)
            source = node;
        return () => source.id === id;
    }
}
