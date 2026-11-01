// Shortcuts settings tab
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../theme"
import "../../controls"

ScrollView {
    id: root
    clip: true

    ColumnLayout {
        width: root.availableWidth
        spacing: Theme.spacingLg

        // Page title
        Text {
            text: "Keyboard Shortcuts"
            font.pixelSize: Theme.fontSizeXl
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        // General settings
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            Text {
                text: "General"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Enable Global Shortcuts"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textSecondary
                }

                Item { Layout.fillWidth: true }

                XSwitch {
                    checked: settingsModel ? settingsModel.shortcutsEnabled : false
                    onToggled: if (settingsModel) settingsModel.shortcutsEnabled = checked
                }
            }
        }

        // Divider
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // Shortcut configuration
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd
            opacity: settingsModel && settingsModel.shortcutsEnabled ? 1.0 : 0.5
            enabled: settingsModel ? settingsModel.shortcutsEnabled : false

            Text {
                text: "Shortcut Assignments"
                font.pixelSize: Theme.fontSizeMd
                font.weight: Font.Medium
                color: Theme.textSecondary
            }

            // Hold to talk
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Hold to Talk"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPrimary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: manualModCombo
                    Layout.preferredWidth: 100
                    model: ["Ctrl", "Alt", "Shift", "Cmd"]
                    currentIndex: {
                        var mod = settingsModel ? settingsModel.shortcutManualModifier : "ctrl"
                        if (mod === "alt") return 1
                        if (mod === "shift") return 2
                        if (mod === "cmd") return 3
                        return 0  // ctrl
                    }
                    onActivated: function(index) {
                        var options = ["ctrl", "alt", "shift", "cmd"]
                        if (settingsModel) settingsModel.shortcutManualModifier = options[index]
                    }
                }

                Text { text: "+"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }

                TextField {
                    id: manualKeyField
                    Layout.preferredWidth: 50
                    text: settingsModel ? settingsModel.shortcutManualKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.shortcutManualKey = text.toLowerCase()
                    maximumLength: 1
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeSm
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: manualKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Hold key to record"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.divider }

            // Auto chat
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Auto Chat"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPrimary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: autoModCombo
                    Layout.preferredWidth: 100
                    model: ["Ctrl", "Alt", "Shift", "Cmd"]
                    currentIndex: {
                        var mod = settingsModel ? settingsModel.shortcutAutoModifier : "ctrl"
                        if (mod === "alt") return 1
                        if (mod === "shift") return 2
                        if (mod === "cmd") return 3
                        return 0
                    }
                    onActivated: function(index) {
                        var options = ["ctrl", "alt", "shift", "cmd"]
                        if (settingsModel) settingsModel.shortcutAutoModifier = options[index]
                    }
                }

                Text { text: "+"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }

                TextField {
                    id: autoKeyField
                    Layout.preferredWidth: 50
                    text: settingsModel ? settingsModel.shortcutAutoKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.shortcutAutoKey = text.toLowerCase()
                    maximumLength: 1
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeSm
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: autoKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Start/stop automatic conversation"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.divider }

            // Interrupt conversation
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Interrupt"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPrimary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: abortModCombo
                    Layout.preferredWidth: 100
                    model: ["Ctrl", "Alt", "Shift", "Cmd"]
                    currentIndex: {
                        var mod = settingsModel ? settingsModel.shortcutAbortModifier : "ctrl"
                        if (mod === "alt") return 1
                        if (mod === "shift") return 2
                        if (mod === "cmd") return 3
                        return 0
                    }
                    onActivated: function(index) {
                        var options = ["ctrl", "alt", "shift", "cmd"]
                        if (settingsModel) settingsModel.shortcutAbortModifier = options[index]
                    }
                }

                Text { text: "+"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }

                TextField {
                    id: abortKeyField
                    Layout.preferredWidth: 50
                    text: settingsModel ? settingsModel.shortcutAbortKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.shortcutAbortKey = text.toLowerCase()
                    maximumLength: 1
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeSm
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: abortKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Immediately stop current conversation"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.divider }

            // Switch mode
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Switch Mode"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPrimary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: modeModCombo
                    Layout.preferredWidth: 100
                    model: ["Ctrl", "Alt", "Shift", "Cmd"]
                    currentIndex: {
                        var mod = settingsModel ? settingsModel.shortcutModeModifier : "ctrl"
                        if (mod === "alt") return 1
                        if (mod === "shift") return 2
                        if (mod === "cmd") return 3
                        return 0
                    }
                    onActivated: function(index) {
                        var options = ["ctrl", "alt", "shift", "cmd"]
                        if (settingsModel) settingsModel.shortcutModeModifier = options[index]
                    }
                }

                Text { text: "+"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }

                TextField {
                    id: modeKeyField
                    Layout.preferredWidth: 50
                    text: settingsModel ? settingsModel.shortcutModeKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.shortcutModeKey = text.toLowerCase()
                    maximumLength: 1
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeSm
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: modeKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Toggle between Manual and Auto mode"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPlaceholder
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.divider }

            // Show/Hide window
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd

                Text {
                    text: "Show/Hide"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPrimary
                    Layout.preferredWidth: 80
                }

                XComboBox {
                    id: windowModCombo
                    Layout.preferredWidth: 100
                    model: ["Ctrl", "Alt", "Shift", "Cmd"]
                    currentIndex: {
                        var mod = settingsModel ? settingsModel.shortcutWindowModifier : "ctrl"
                        if (mod === "alt") return 1
                        if (mod === "shift") return 2
                        if (mod === "cmd") return 3
                        return 0
                    }
                    onActivated: function(index) {
                        var options = ["ctrl", "alt", "shift", "cmd"]
                        if (settingsModel) settingsModel.shortcutWindowModifier = options[index]
                    }
                }

                Text { text: "+"; font.pixelSize: Theme.fontSizeSm; color: Theme.textSecondary }

                TextField {
                    id: windowKeyField
                    Layout.preferredWidth: 50
                    text: settingsModel ? settingsModel.shortcutWindowKey : ""
                    onEditingFinished: if (settingsModel) settingsModel.shortcutWindowKey = text.toLowerCase()
                    maximumLength: 1
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSizeSm
                    background: Rectangle {
                        radius: Theme.radiusSm
                        color: Theme.backgroundSecondary
                        border.color: windowKeyField.activeFocus ? Theme.primary : "transparent"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "Toggle main window visibility"
                    font.pixelSize: Theme.fontSizeSm
                    color: Theme.textPlaceholder
                }
            }
        }

        // Divider
        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.divider
        }

        // Hint message
        Text {
            Layout.fillWidth: true
            text: "Shortcuts work globally across the system. Ensure they do not conflict with system or other application shortcuts."
            font.pixelSize: Theme.fontSizeSm
            color: Theme.textPlaceholder
            wrapMode: Text.WordWrap
        }

        Item { Layout.fillHeight: true }
    }
}
