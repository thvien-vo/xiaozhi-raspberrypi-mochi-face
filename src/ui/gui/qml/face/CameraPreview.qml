// Small live camera view in a corner of the screen. Hidden whenever frames stop.
import QtQuick

Rectangle {
    id: root

    property var preview: null
    property real unit: 1

    readonly property bool onRight: preview.corner.indexOf("right") >= 0
    readonly property bool onBottom: preview.corner.indexOf("bottom") >= 0
    readonly property real margin: preview.margin * unit

    width: preview.previewWidth * unit
    height: width * preview.aspect
    x: onRight ? parent.width - width - margin : margin
    y: onBottom ? parent.height - height - margin : margin
    visible: preview.available
    color: "black"
    border.color: "#404040"
    border.width: 1

    Image {
        anchors.fill: parent
        anchors.margins: 1
        // The frame id only exists to make QML ask the provider again
        source: root.visible ? "image://camera/frame/" + root.preview.frameId : ""
        cache: false
        asynchronous: false
    }
}
