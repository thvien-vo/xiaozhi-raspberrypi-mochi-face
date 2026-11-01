// Application main entry (classic window with GIF emotions, FACE.STYLE = "gif")
import QtQuick
import QtQuick.Window

import "windows"
import "components"

// Main window as root element; initially hidden, QmlAppHost.show_root decides whether to bring to foreground
MainWindow {
    id: mainWindow
    visible: false

    SettingsHost {}
}
