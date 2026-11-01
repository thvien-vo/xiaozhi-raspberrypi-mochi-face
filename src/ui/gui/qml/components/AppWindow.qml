// Frameless resizable window base class
import QtQuick
import QtQuick.Window
import Qt5Compat.GraphicalEffects
import "../theme"

Window {
    id: root

    flags: Qt.FramelessWindowHint | Qt.Window
    color: "transparent"

    minimumWidth: 480
    minimumHeight: 360

    // Margin for edge drag resizing
    property int resizeMargin: 8

    // Whether maximized or fullscreen
    property bool isMaximized: root.visibility === Window.Maximized || root.visibility === Window.FullScreen

    // Content area
    default property alias content: contentArea.data

    // Update theme window width
    onWidthChanged: Theme.windowWidth = width

    // Main container (with rounded corners and border)
    Rectangle {
        id: container
        anchors.fill: parent
        anchors.margins: root.isMaximized ? 0 : 1
        radius: root.isMaximized ? 0 : Theme.windowRadius
        color: Theme.background
        antialiasing: true
        border.width: root.isMaximized ? 0 : 1
        border.color: Theme.border

        // Use layer for rounded corner clipping
        layer.enabled: !root.isMaximized
        layer.effect: OpacityMask {
            maskSource: Rectangle {
                width: container.width
                height: container.height
                radius: container.radius
            }
        }

        // Content area
        Item {
            id: contentArea
            anchors.fill: parent
        }
    }

    // Edge resize MouseAreas - placed on top layer (hidden when maximized)
    // Left edge
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: parent.height - resizeMargin * 2
        x: 0
        y: resizeMargin
        cursorShape: Qt.SizeHorCursor
        onPressed: root.startSystemResize(Qt.LeftEdge)
    }

    // Right edge
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: parent.height - resizeMargin * 2
        x: parent.width - resizeMargin
        y: resizeMargin
        cursorShape: Qt.SizeHorCursor
        onPressed: root.startSystemResize(Qt.RightEdge)
    }

    // Top edge
    MouseArea {
        visible: !root.isMaximized
        width: parent.width - resizeMargin * 2
        height: resizeMargin
        x: resizeMargin
        y: 0
        cursorShape: Qt.SizeVerCursor
        onPressed: root.startSystemResize(Qt.TopEdge)
    }

    // Bottom edge
    MouseArea {
        visible: !root.isMaximized
        width: parent.width - resizeMargin * 2
        height: resizeMargin
        x: resizeMargin
        y: parent.height - resizeMargin
        cursorShape: Qt.SizeVerCursor
        onPressed: root.startSystemResize(Qt.BottomEdge)
    }

    // Top-left corner
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: resizeMargin
        x: 0
        y: 0
        cursorShape: Qt.SizeFDiagCursor
        onPressed: root.startSystemResize(Qt.LeftEdge | Qt.TopEdge)
    }

    // Top-right corner
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: resizeMargin
        x: parent.width - resizeMargin
        y: 0
        cursorShape: Qt.SizeBDiagCursor
        onPressed: root.startSystemResize(Qt.RightEdge | Qt.TopEdge)
    }

    // Bottom-left corner
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: resizeMargin
        x: 0
        y: parent.height - resizeMargin
        cursorShape: Qt.SizeBDiagCursor
        onPressed: root.startSystemResize(Qt.LeftEdge | Qt.BottomEdge)
    }

    // Bottom-right corner
    MouseArea {
        visible: !root.isMaximized
        width: resizeMargin
        height: resizeMargin
        x: parent.width - resizeMargin
        y: parent.height - resizeMargin
        cursorShape: Qt.SizeFDiagCursor
        onPressed: root.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
    }
}
