// Small expressive extras around the eyes. Only the active overlay exists, so idle
// faces carry no extra items or running animations.
import QtQuick

Item {
    id: overlays

    property string overlay: ""
    property color eyeColor: "white"    // The sleeping "z"s take the eye color
    property real unit: 1
    property real eyeWidth: 190
    property real eyeHeight: 230
    property real leftEyeX: 0           // Eye centers, relative to this item
    property real rightEyeX: 0
    property real eyeY: 0

    Loader {
        anchors.fill: parent
        active: overlays.overlay === "tears"
        sourceComponent: Item {
            Repeater {
                model: 4
                Rectangle {
                    id: tear
                    required property int index
                    readonly property bool onLeft: index % 2 === 0
                    width: 16 * overlays.unit
                    height: 26 * overlays.unit
                    radius: width / 2
                    color: "#7cc8ff"
                    opacity: 0
                    y: overlays.eyeY + overlays.eyeHeight * 0.9
                    x: (onLeft ? overlays.leftEyeX : overlays.rightEyeX) - width / 2
                       + (index < 2 ? -0.22 : 0.22) * overlays.eyeWidth
                    SequentialAnimation on y {
                        loops: Animation.Infinite
                        PauseAnimation { duration: tear.index * 240 }
                        NumberAnimation {
                            from: overlays.eyeY + overlays.eyeHeight * 0.9
                            to: overlays.eyeY + overlays.eyeHeight * 1.32
                            duration: 900
                            easing.type: Easing.InQuad
                        }
                    }
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        PauseAnimation { duration: tear.index * 240 }
                        NumberAnimation { from: 1; to: 0; duration: 900; easing.type: Easing.InCubic }
                    }
                }
            }
        }
    }

    Loader {
        anchors.fill: parent
        active: overlays.overlay === "blush"
        sourceComponent: Item {
            Repeater {
                model: [overlays.leftEyeX, overlays.rightEyeX]
                Rectangle {
                    required property real modelData
                    width: overlays.eyeWidth * 0.62
                    height: 26 * overlays.unit
                    radius: height / 2
                    color: "#ff7a9a"
                    opacity: 0.55
                    x: modelData - width / 2
                    y: overlays.eyeY + overlays.eyeHeight
                }
            }
        }
    }

    Loader {
        anchors.fill: parent
        active: overlays.overlay === "drool"
        sourceComponent: Item {
            Rectangle {
                width: 16 * overlays.unit
                height: 24 * overlays.unit
                radius: width / 2
                color: "#7cc8ff"
                x: overlays.rightEyeX + overlays.eyeWidth * 0.1
                y: overlays.eyeY + overlays.eyeHeight * 0.55
                SequentialAnimation on y {
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: overlays.eyeY + overlays.eyeHeight * 0.55
                        to: overlays.eyeY + overlays.eyeHeight * 1.0
                        duration: 1200
                        easing.type: Easing.InQuad
                    }
                    PauseAnimation { duration: 500 }
                }
            }
        }
    }

    Loader {
        anchors.fill: parent
        active: overlays.overlay === "floatheart"
        sourceComponent: Item {
            Text {
                text: "♥"
                color: "#ff4d6d"
                font.pixelSize: 64 * overlays.unit
                x: overlays.rightEyeX + overlays.eyeWidth * 0.62
                y: overlays.eyeY + overlays.eyeHeight * 0.1
                SequentialAnimation on y {
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: overlays.eyeY + overlays.eyeHeight * 0.1
                        to: overlays.eyeY - overlays.eyeHeight * 0.3
                        duration: 1800
                        easing.type: Easing.OutSine
                    }
                }
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    NumberAnimation { from: 1; to: 0; duration: 1800; easing.type: Easing.InQuad }
                }
            }
        }
    }

    Loader {
        anchors.fill: parent
        active: overlays.overlay === "zzz"
        sourceComponent: Item {
            Repeater {
                model: 3
                Text {
                    id: letter
                    required property int index
                    text: "z"
                    color: overlays.eyeColor
                    opacity: 0
                    y: overlays.eyeY - overlays.eyeHeight * 0.1
                    font.pixelSize: (34 + index * 16) * overlays.unit
                    font.weight: Font.Bold
                    x: overlays.rightEyeX + overlays.eyeWidth * (0.45 + index * 0.24)
                    SequentialAnimation on y {
                        loops: Animation.Infinite
                        PauseAnimation { duration: letter.index * 800 }
                        NumberAnimation {
                            from: overlays.eyeY - overlays.eyeHeight * 0.1
                            to: overlays.eyeY - overlays.eyeHeight * 0.3
                            duration: 2400
                            easing.type: Easing.OutSine
                        }
                    }
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        PauseAnimation { duration: letter.index * 800 }
                        NumberAnimation { from: 0; to: 0.9; duration: 500 }
                        NumberAnimation { to: 0; duration: 1900 }
                    }
                }
            }
        }
    }
}
