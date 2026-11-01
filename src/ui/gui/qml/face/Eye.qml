// One parametric eye: a rounded rectangle shaped by two black lids.
// Lid shapes rely on the pure black background. Shapes are drawn once into canvases,
// so blinking and morphing only touch transforms and rectangle geometry.
import QtQuick

Item {
    id: eye

    property real side: -1              // -1 = viewer's left, +1 = viewer's right
    property real baseWidth: 190
    property real baseHeight: 230
    property var shape: ({})            // Resolved preset from FacePresets.compose()
    property real blink: 1              // 1 = open, near 0 = closed
    property int morphMs: 280

    property real animW: shape.w
    property real animH: shape.h
    property real animR: shape.r
    property real animDy: shape.dy
    property real animTilt: shape.tilt
    property real animLidTop: shape.lidTop
    property real animLidTopTilt: shape.lidTopTilt
    property real animLidBot: shape.lidBot
    property real animLidBotRound: shape.lidBotRound
    property real animBody: shape.body
    property real animHeart: shape.heart
    property real animSpiral: shape.spiral

    Behavior on animW { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animH { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animR { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animDy { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animTilt { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animLidTop { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animLidTopTilt { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animLidBot { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animLidBotRound { NumberAnimation { duration: eye.morphMs; easing.type: Easing.InOutCubic } }
    Behavior on animBody { NumberAnimation { duration: eye.morphMs } }
    Behavior on animHeart { NumberAnimation { duration: eye.morphMs } }
    Behavior on animSpiral { NumberAnimation { duration: eye.morphMs } }

    width: baseWidth * animW
    height: baseHeight * animH
    rotation: -side * animTilt

    transform: Scale {
        origin.x: eye.width / 2
        origin.y: eye.height / 2
        yScale: eye.blink
    }

    Rectangle {
        anchors.fill: parent
        color: "white"
        antialiasing: true
        opacity: eye.animBody
        visible: opacity > 0.01
        radius: Math.min(width, height) / 2 * Math.max(0, Math.min(1, eye.animR))
    }

    // Upper lid
    Rectangle {
        color: "black"
        antialiasing: true
        visible: eye.animLidTop > 0.002
        width: eye.width * 1.7
        height: eye.height * 1.3
        x: (eye.width - width) / 2
        y: eye.height * eye.animLidTop - height
        transformOrigin: Item.Bottom
        rotation: -eye.side * eye.animLidTopTilt
    }

    // Lower lid: straight, or round for a smiling arc
    Rectangle {
        color: "black"
        antialiasing: true
        visible: eye.animLidBot > 0.002
        width: eye.width * 1.3
        height: width
        x: (eye.width - width) / 2
        y: eye.height * (1 - eye.animLidBot)
        radius: width / 2 * eye.animLidBotRound
    }

    Loader {
        anchors.centerIn: parent
        width: Math.min(eye.baseWidth, eye.baseHeight) * 1.25
        height: width
        active: eye.animHeart > 0.01
        opacity: eye.animHeart
        sourceComponent: Canvas {
            onPaint: {
                var ctx = getContext("2d")
                var w = width, h = height
                ctx.reset()
                ctx.fillStyle = "#ff4d6d"
                ctx.beginPath()
                ctx.moveTo(w * 0.5, h * 0.9)
                ctx.bezierCurveTo(w * 0.02, h * 0.58, w * 0.02, h * 0.12, w * 0.3, h * 0.12)
                ctx.bezierCurveTo(w * 0.42, h * 0.12, w * 0.48, h * 0.2, w * 0.5, h * 0.28)
                ctx.bezierCurveTo(w * 0.52, h * 0.2, w * 0.58, h * 0.12, w * 0.7, h * 0.12)
                ctx.bezierCurveTo(w * 0.98, h * 0.12, w * 0.98, h * 0.58, w * 0.5, h * 0.9)
                ctx.fill()
            }
        }
    }

    Loader {
        anchors.centerIn: parent
        width: Math.min(eye.baseWidth, eye.baseHeight)
        height: width
        active: eye.animSpiral > 0.01
        opacity: eye.animSpiral
        sourceComponent: Canvas {
            onPaint: {
                var ctx = getContext("2d")
                var center = width / 2
                ctx.reset()
                ctx.strokeStyle = "white"
                ctx.lineWidth = width * 0.075
                ctx.lineCap = "round"
                ctx.beginPath()
                for (var a = 0; a < Math.PI * 6.2; a += 0.15) {
                    var r = center * 0.9 * a / (Math.PI * 6.2)
                    ctx.lineTo(center + r * Math.cos(a * eye.side), center + r * Math.sin(a * eye.side))
                }
                ctx.stroke()
            }
            RotationAnimator on rotation {
                from: 0
                to: 360 * eye.side
                duration: 2600
                loops: Animation.Infinite
            }
        }
    }
}
