// Control panel
import QtQuick
import QtQuick.Layouts
import "../theme"
import "../controls"

Rectangle {
    id: root

    color: Theme.backgroundSecondary

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingMd
        spacing: Theme.spacingMd

        // Input area
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            XTextField {
                id: inputField
                Layout.fillWidth: true
                placeholderText: "Enter message..."

                Keys.onReturnPressed: sendText()
            }

            XButton {
                text: "Send"
                onClicked: sendText()
            }
        }

        // Control button area
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            // Manual/Auto mode toggle
            XButton {
                Layout.fillWidth: true
                text: mainModel.autoMode ? "Switch to Manual" : "Switch to Auto"
                variant: "secondary"
                onClicked: eventBridge.onAutoToggle()
            }

            // Talk button (manual mode)
            XButton {
                Layout.fillWidth: true
                text: mainModel.buttonText
                visible: !mainModel.autoMode
                onPressed: eventBridge.onButtonPress()
                onReleased: eventBridge.onButtonRelease()
            }

            // Interrupt button
            XButton {
                Layout.preferredWidth: 80
                text: "Interrupt"
                variant: "secondary"
                onClicked: eventBridge.onAbort()
            }
        }
    }

    function sendText() {
        let text = inputField.text.trim()
        if (text.length > 0) {
            eventBridge.onSendText(text)
            inputField.text = ""
        }
    }
}
