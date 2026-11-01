// Application main entry (animated robot face, FACE.STYLE = "robot")
import QtQuick
import QtQuick.Window

import "windows"
import "components"

// Initially hidden, QmlAppHost.show_root decides whether to bring to foreground
FaceWindow {
    id: faceWindow

    SettingsHost {}
}
