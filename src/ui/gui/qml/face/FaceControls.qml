// Laptop controls for the windowed face: appears while the pointer is over the window.
// Kiosk mode never creates this item.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    color: "#d0181818"
    height: 52

    component FaceButton: Button {
        id: control
        Layout.preferredHeight: 34
        background: Rectangle {
            radius: 6
            color: control.pressed ? "#555555" : (control.hovered ? "#444444" : "#2c2c2c")
        }
        contentItem: Text {
            text: control.text
            color: "white"
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    function sendText() {
        const text = input.text.trim()
        if (text.length > 0 && eventBridge) {
            eventBridge.onSendText(text)
            input.text = ""
        }
    }

    // Clicks between the buttons must not reach the face behind the bar
    MouseArea {
        anchors.fill: parent
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 9
        spacing: 8

        FaceButton {
            text: mainModel ? mainModel.buttonText : ""
            onClicked: {
                if (!eventBridge) return
                if (mainModel.autoMode) eventBridge.onAutoStart()
                else eventBridge.onManualToggle()
            }
        }
        FaceButton {
            text: "Interrupt"
            onClicked: if (eventBridge) eventBridge.onAbort()
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            radius: 6
            color: "#000000"
            border.color: input.activeFocus ? "#ffffff" : "#555555"

            TextInput {
                id: input
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                verticalAlignment: TextInput.AlignVCenter
                color: "white"
                font.pixelSize: 14
                clip: true
                selectByMouse: true
                Keys.onReturnPressed: root.sendText()

                Text {
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    text: "Enter message..."
                    color: "#808080"
                    font: input.font
                    visible: !input.text && !input.activeFocus
                }
            }
        }
        FaceButton {
            text: "Send"
            onClicked: root.sendText()
        }
        FaceButton {
            text: mainModel ? mainModel.modeText : ""
            onClicked: if (eventBridge) eventBridge.onAutoToggle()
        }
        FaceButton {
            text: "Settings"
            onClicked: if (eventBridge) eventBridge.onOpenSettings()
        }
    }
}
