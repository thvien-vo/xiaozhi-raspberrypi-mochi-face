// Device activation window
import QtQuick
import QtQuick.Layouts
import "../theme"
import "../components"
import "../controls"

AppWindow {
    id: root

    width: 520
    height: 340
    minimumWidth: 450
    minimumHeight: 300
    title: "Device Activation"
    visible: true

    // Signals
    signal activationCompleted(bool success)

    // Use platform-adaptive title bar
    TitleBar {
        id: titleBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        title: "Device Activation"
        showMaximize: false
        onMinimizeClicked: root.showMinimized()
        onCloseClicked: root.close()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: titleBar.height
        anchors.margins: Theme.spacingXl
        spacing: Theme.spacingLg

        // Status indicator - placed independently at top of content area
        RowLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignRight
            spacing: Theme.spacingSm

            Rectangle {
                width: 8
                height: 8
                radius: Theme.radiusSm
                color: activationModel ? activationModel.statusColor : Theme.textPlaceholder

                // Blinking animation while activating
                SequentialAnimation on opacity {
                    running: activationModel ? activationModel.isActivating : false
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 500 }
                    NumberAnimation { to: 1.0; duration: 500 }
                }
            }

            Text {
                text: activationModel ? activationModel.activationStatus : ""
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSizeSm
                color: Theme.textSecondary
            }
        }

        // Device information card
        XCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 90
            hoverable: true

            ColumnLayout {
                anchors.fill: parent
                spacing: Theme.spacingMd

                Text {
                    text: "Device Information"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSizeMd
                    font.weight: Font.Medium
                    color: Theme.textSecondary
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: Theme.spacingXxl
                    rowSpacing: Theme.spacingXs

                    // Serial number
                    Text {
                        text: "Serial Number"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textPlaceholder
                    }

                    Text {
                        text: "MAC Address"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textPlaceholder
                    }

                    Text {
                        text: activationModel ? activationModel.serialNumber : ""
                        font.family: Theme.fontFamilyMono
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textPrimary
                        elide: Text.ElideMiddle
                        Layout.maximumWidth: 200
                    }

                    Text {
                        text: activationModel ? activationModel.macAddress : ""
                        font.family: Theme.fontFamilyMono
                        font.pixelSize: Theme.fontSizeSm
                        color: Theme.textPrimary
                    }
                }
            }
        }

        // Activation code card
        XCard {
            Layout.fillWidth: true
            Layout.preferredHeight: 70
            hoverable: true

            RowLayout {
                anchors.fill: parent
                spacing: Theme.spacingLg

                Text {
                    text: "Activation Code"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSizeMd
                    font.weight: Font.Medium
                    color: Theme.textSecondary
                }

                // Activation code display box
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    radius: Theme.radiusSm
                    color: Theme.background
                    border.width: 1
                    border.color: Theme.border

                    Text {
                        anchors.centerIn: parent
                        text: activationModel ? activationModel.activationCode : "------"
                        font.family: Theme.fontFamilyMono
                        font.pixelSize: Theme.fontSizeLg
                        font.weight: Font.Bold
                        font.letterSpacing: 4
                        color: (activationModel && activationModel.activationCode !== "------") ? Theme.error : Theme.textPlaceholder
                    }
                }

                // Copy button
                XButton {
                    text: "Copy"
                    enabled: activationModel ? activationModel.activationCode !== "------" : false
                    onClicked: {
                        if (typeof activationController !== 'undefined') {
                            activationController.copyActivationCode()
                        }
                        copyToast.show()
                    }
                }
            }
        }

        // Action buttons
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            XButton {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                text: "Open Activation Page"
                enabled: activationModel ? !activationModel.isActivated : true
                onClicked: {
                    if (typeof activationController !== 'undefined') {
                        activationController.openActivationUrl()
                    }
                }
            }
        }

        // Hint message
        Text {
            Layout.fillWidth: true
            text: (activationModel && activationModel.isActivated)
                ? "Device activated, closing window..."
                : "Please enter the activation code on the activation page to complete setup"
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSizeSm
            color: Theme.textPlaceholder
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }
    }

    // Copy success toast
    Rectangle {
        id: copyToast
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spacingXl
        anchors.horizontalCenter: parent.horizontalCenter
        width: toastText.implicitWidth + Theme.spacingLg * 2
        height: 36
        radius: Theme.radiusMd
        color: Theme.success
        opacity: 0
        visible: opacity > 0

        function show() {
            opacity = 1
            hideTimer.restart()
        }

        Timer {
            id: hideTimer
            interval: 2000
            onTriggered: copyToast.opacity = 0
        }

        Behavior on opacity {
            NumberAnimation { duration: Theme.animationNormal }
        }

        Text {
            id: toastText
            anchors.centerIn: parent
            text: "Activation code copied"
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSizeSm
            color: "white"
        }
    }
}
