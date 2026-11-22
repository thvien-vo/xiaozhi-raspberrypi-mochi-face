// Animated robot face: two parametric eyes on a black background.
// The eyes are never static: random blinks and glances plus a slow breathing scale.
import QtQuick
import "FacePresets.js" as Presets

Item {
    id: face

    property string emotion: "neutral"
    property string visualState: "idle"
    property string activity: "awake"
    property int blinkMinMs: 2000
    property int blinkMaxMs: 6000
    property real doubleBlinkChance: 0.2
    // Eye color: base color, mode (fixed / rainbow / emotion) and the emotion mode's map
    property string eyeColorBase: "#ffffff"
    property string eyeColorMode: "fixed"
    property var emotionColors: ({})
    property real rainbowHue: 0

    readonly property real unit: Math.min(width / 1024, height / 432)
    readonly property real eyeWidth: 190 * unit
    readonly property real eyeHeight: 230 * unit
    readonly property real eyeGap: 112 * unit
    readonly property string presetName: Presets.activeName(emotion, visualState, activity)
    readonly property var pose: Presets.compose(presetName, visualState)
    readonly property bool asleep: activity === "asleep"
    // Same rule as src/ui/face_color.py effective_color()
    readonly property color targetEyeColor: eyeColorMode === "rainbow"
        ? Qt.hsva(rainbowHue, 0.7, 1, 1)
        : (eyeColorMode === "emotion" && emotionColors[presetName] !== undefined
           ? emotionColors[presetName] : eyeColorBase)
    property color eyeColor: targetEyeColor
    Behavior on eyeColor {
        enabled: face.eyeColorMode !== "rainbow"
        ColorAnimation { duration: 260 }
    }

    // Rainbow: hue from the wall clock (one turn per 12 s) so the web panel's fallback
    // drawing shows the same color; 20 steps a second are smooth enough for a slow turn
    Timer {
        running: face.eyeColorMode === "rainbow"
        repeat: true
        interval: 50
        triggeredOnStart: true
        onTriggered: face.rainbowHue = (Date.now() / 12000) % 1
    }

    property real blink: 1
    property real glanceX: 0
    property real glanceY: 0
    property real breath: 1
    property real motionX: 0
    property real motionY: 0
    property real motionAngle: 0
    property real motionScale: 1

    readonly property real gazeX: (glanceX + pose.gazeX) * eyeWidth
    readonly property real gazeY: (glanceY + pose.gazeY) * eyeHeight

    Behavior on glanceX { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    Behavior on glanceY { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

    function randomBetween(low, high) {
        return low + Math.random() * (high - low)
    }

    // Blinks

    SequentialAnimation {
        id: blinkAnimation
        NumberAnimation { target: face; property: "blink"; to: 0.05; duration: 70; easing.type: Easing.InQuad }
        NumberAnimation { target: face; property: "blink"; to: 1; duration: 120; easing.type: Easing.OutQuad }
    }

    Timer {
        running: !face.asleep
        repeat: true
        interval: face.blinkMaxMs
        onTriggered: {
            interval = face.randomBetween(face.blinkMinMs, face.blinkMaxMs)
            if (blinkAnimation.running) return
            blinkAnimation.loops = Math.random() < face.doubleBlinkChance ? 2 : 1
            blinkAnimation.start()
        }
    }

    // Glances: look away briefly, then return to center

    Timer {
        id: glanceTimer
        running: !face.asleep && face.presetName !== "confused"
        repeat: true
        interval: 3000
        onTriggered: {
            interval = face.randomBetween(2500, 7000)
            var range = face.visualState === "listening" ? 0.5 : 1
            var direction = Math.floor(Math.random() * 4)
            face.glanceX = range * [-0.2, 0.2, 0, 0][direction]
            face.glanceY = range * [0, 0, -0.11, 0.09][direction]
            glanceReturn.interval = face.randomBetween(500, 1500)
            glanceReturn.restart()
        }
    }

    Timer {
        id: glanceReturn
        onTriggered: {
            face.glanceX = 0
            face.glanceY = 0
        }
    }

    // While thinking the eyes drift between the upper corners

    Timer {
        running: face.visualState === "thinking" && !face.asleep
        repeat: true
        interval: 900
        triggeredOnStart: true
        property bool toLeft: false
        onTriggered: {
            toLeft = !toLeft
            face.glanceX = toLeft ? -0.14 : 0.08
            face.glanceY = -0.06
        }
        onRunningChanged: if (!running) { face.glanceX = 0; face.glanceY = 0 }
    }

    SequentialAnimation on breath {
        id: breathing
        loops: Animation.Infinite
        NumberAnimation { to: 1.022; duration: face.asleep ? 3600 : 2400; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.985; duration: face.asleep ? 3600 : 2400; easing.type: Easing.InOutSine }
    }
    onAsleepChanged: breathing.restart()

    // Motion layered on top of the pose; each stops by easing back to rest

    SequentialAnimation {
        running: face.pose.motion === "shake"
        loops: Animation.Infinite
        NumberAnimation { target: face; property: "motionX"; to: 4 * face.unit; duration: 45 }
        NumberAnimation { target: face; property: "motionX"; to: -4 * face.unit; duration: 90 }
        NumberAnimation { target: face; property: "motionX"; to: 0; duration: 45 }
        onRunningChanged: if (!running) face.motionX = 0
    }

    SequentialAnimation {
        running: face.pose.motion === "bounce"
        loops: Animation.Infinite
        NumberAnimation { target: face; property: "motionY"; to: -13 * face.unit; duration: 170; easing.type: Easing.OutQuad }
        NumberAnimation { target: face; property: "motionY"; to: 0; duration: 170; easing.type: Easing.InQuad }
        onRunningChanged: if (!running) face.motionY = 0
    }

    SequentialAnimation {
        running: face.pose.motion === "wobble"
        loops: Animation.Infinite
        NumberAnimation { target: face; property: "motionAngle"; to: 4; duration: 420; easing.type: Easing.InOutSine }
        NumberAnimation { target: face; property: "motionAngle"; to: -4; duration: 420; easing.type: Easing.InOutSine }
        onRunningChanged: if (!running) face.motionAngle = 0
    }

    SequentialAnimation {
        running: face.pose.motion === "pulse" || face.visualState === "speaking"
        loops: Animation.Infinite
        NumberAnimation { target: face; property: "motionScale"; to: 1.04; duration: 320; easing.type: Easing.InOutSine }
        NumberAnimation { target: face; property: "motionScale"; to: 0.985; duration: 320; easing.type: Easing.InOutSine }
        onRunningChanged: if (!running) face.motionScale = 1
    }

    // Eyes

    Item {
        id: eyes
        anchors.fill: parent
        x: face.motionX
        y: face.motionY
        rotation: face.motionAngle
        scale: face.breath * face.motionScale

        Eye {
            side: -1
            color: face.eyeColor
            baseWidth: face.eyeWidth
            baseHeight: face.eyeHeight
            shape: face.pose.left
            blink: face.blink
            x: face.width / 2 - (face.eyeGap + face.eyeWidth) / 2 - width / 2 + face.gazeX
            y: face.height / 2 - height / 2 + face.gazeY + animDy * face.eyeHeight
        }

        Eye {
            side: 1
            color: face.eyeColor
            baseWidth: face.eyeWidth
            baseHeight: face.eyeHeight
            shape: face.pose.right
            blink: face.blink
            x: face.width / 2 + (face.eyeGap + face.eyeWidth) / 2 - width / 2 + face.gazeX
            y: face.height / 2 - height / 2 + face.gazeY + animDy * face.eyeHeight
        }
    }

    FaceOverlays {
        anchors.fill: parent
        overlay: face.pose.overlay
        eyeColor: face.eyeColor
        unit: face.unit
        eyeWidth: face.eyeWidth
        eyeHeight: face.eyeHeight
        leftEyeX: face.width / 2 - (face.eyeGap + face.eyeWidth) / 2 + face.gazeX
        rightEyeX: face.width / 2 + (face.eyeGap + face.eyeWidth) / 2 + face.gazeX
        eyeY: face.height / 2 - face.eyeHeight / 2
    }
}
