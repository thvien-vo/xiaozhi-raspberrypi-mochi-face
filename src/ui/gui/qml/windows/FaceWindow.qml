// Robot face window: opaque black, native frame on the desktop, frameless in kiosk mode
import QtQuick
import QtQuick.Window
import "../face"

Window {
    id: root

    readonly property var faceConfig: faceModel.config
    readonly property bool kiosk: faceConfig.fullscreen
    readonly property real unit: Math.min(width / 1024, height / 600)

    width: faceConfig.windowWidth
    height: faceConfig.windowHeight
    title: "Xiaozhi"
    color: "black"
    flags: kiosk ? (Qt.Window | Qt.FramelessWindowHint) : Qt.Window
    // Controlled by QmlAppHost.show_root; avoid stealing focus when QML loads
    visible: false

    function requestQuit() {
        if (eventBridge) eventBridge.onQuitRequest()
        else Qt.quit()
    }

    function onFaceTapped() {
        const wasAwake = faceModel.activity === "awake"
        faceModel.interact()
        if (!wasAwake || !eventBridge) return
        if (mainModel.autoMode) eventBridge.onAutoStart()
        else eventBridge.onManualToggle()
    }

    onClosing: function(close) {
        close.accepted = false
        requestQuit()
    }

    Shortcut {
        sequence: "Esc"
        enabled: root.kiosk && root.faceConfig.escToExit
        context: Qt.ApplicationShortcut
        onActivated: root.requestQuit()
    }

    RobotFace {
        id: robotFace
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: parent.height * 0.72
        emotion: faceModel.emotion
        visualState: faceModel.visualState
        activity: faceModel.activity
        blinkMinMs: root.faceConfig.blinkMinMs
        blinkMaxMs: root.faceConfig.blinkMaxMs
        doubleBlinkChance: root.faceConfig.doubleBlinkChance
    }

    SpeechText {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: robotFace.bottom
        anchors.bottom: parent.bottom
        anchors.leftMargin: 48 * root.unit
        anchors.rightMargin: 48 * root.unit
        anchors.bottomMargin: 14 * root.unit
        unit: root.unit
        sentence: faceModel.speechText
        serial: faceModel.speechSerial
        userText: faceModel.userText
        musicLine: mainModel ? mainModel.musicLine : ""
        speechActive: faceModel.speechActive
        busy: faceModel.visualState === "thinking"
        showUserText: root.faceConfig.showUserText
        charsPerSecond: root.faceConfig.typewriterCps
        holdMs: root.faceConfig.textHoldMs
        onFaded: faceModel.clearText()
    }

    Loader {
        anchors.fill: parent
        active: cameraPreview !== null
        sourceComponent: Item {
            CameraPreview {
                preview: cameraPreview
                unit: root.unit
            }
        }
    }

    TapHandler {
        onTapped: root.onFaceTapped()
    }

    HoverHandler {
        id: hover
    }

    Loader {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        active: !root.kiosk && eventBridge !== null
        visible: hover.hovered
        sourceComponent: FaceControls {}
    }

    Text {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.margins: 8
        visible: root.faceConfig.showFps
        text: fpsCounter.fps + " FPS"
        color: "#40ff80"
        font.pixelSize: 14
    }

    QtObject {
        id: fpsCounter
        property int frames: 0
        property int fps: 0
    }

    Connections {
        target: root
        enabled: root.faceConfig.showFps
        function onFrameSwapped() { fpsCounter.frames += 1 }
    }

    Timer {
        interval: 1000
        repeat: true
        running: root.faceConfig.showFps
        onTriggered: {
            fpsCounter.fps = fpsCounter.frames
            fpsCounter.frames = 0
        }
    }
}
