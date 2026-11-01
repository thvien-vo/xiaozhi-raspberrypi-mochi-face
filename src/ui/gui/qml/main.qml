// Application main entry
import QtQuick
import QtQuick.Window

import "windows"

// Main window as root element; initially hidden, QmlAppHost.show_root decides whether to bring to foreground
MainWindow {
    id: mainWindow
    visible: false

    // Settings window - lazy-loaded using Loader (as a standalone window)
    Loader {
        id: settingsLoader
        active: false
        source: "windows/SettingsWindow.qml"

        onLoaded: {
            item.visible = true
            item.raise()
            item.requestActivate()
        }
    }

    // Listen to eventBridge signals to control the settings window
    Connections {
        target: eventBridge

        function onShowSettingsWindow() {
            if (settingsLoader.active) {
                // Already loaded, show directly
                settingsLoader.item.visible = true
                settingsLoader.item.raise()
                settingsLoader.item.requestActivate()
            } else {
                // First time loading
                settingsLoader.active = true
            }
        }
    }
}
