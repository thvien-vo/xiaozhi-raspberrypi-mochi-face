// The "mouth": a typewriter line of what the assistant is saying, with the recognized
// user speech above it in a dimmer style. Text fades out after speech ends.
import QtQuick

Item {
    id: root

    property string sentence: ""
    property int serial: 0              // Changes for every new sentence
    property string userText: ""
    property string musicLine: ""
    property bool speechActive: false
    property bool busy: false           // Waiting for a reply: keep the text on screen
    property bool showUserText: true
    property int charsPerSecond: 28
    property int holdMs: 1800
    property real unit: 1

    signal faded()

    property var characters: []
    property int shown: 0
    readonly property bool typing: shown < characters.length
    readonly property bool hasText: sentence.length > 0 || userText.length > 0

    // Restart on the sentence serial, so the same sentence twice still types out
    onSerialChanged: {
        characters = Array.from(sentence)   // Keeps emoji and other surrogate pairs whole
        shown = 0
        textLayer.opacity = 1
        holdTimer.stop()
        typewriter.restart()
    }
    onUserTextChanged: if (userText.length > 0) textLayer.opacity = 1

    Timer {
        id: typewriter
        interval: Math.max(10, 1000 / root.charsPerSecond)
        repeat: true
        onTriggered: {
            root.shown += 1
            if (!root.typing) stop()
        }
    }

    readonly property bool canFade: hasText && !speechActive && !typing && !busy
    onCanFadeChanged: canFade ? holdTimer.restart() : holdTimer.stop()

    Timer {
        id: holdTimer
        interval: root.holdMs
        onTriggered: fadeOut.start()
    }

    SequentialAnimation {
        id: fadeOut
        OpacityAnimator { target: textLayer; to: 0; duration: 450 }
        ScriptAction { script: root.faded() }
    }

    Item {
        id: textLayer
        anchors.fill: parent

        Text {
            id: userLine
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            visible: root.showUserText && root.userText.length > 0
            text: root.userText
            color: "#808080"
            font.pixelSize: 21 * root.unit
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }

        // Finds the font size for the full sentence up front, so the text does not
        // jump between sizes while it types out
        Text {
            id: measure
            anchors.fill: spoken
            visible: false
            text: root.sentence
            wrapMode: Text.Wrap
            fontSizeMode: Text.Fit
            font.pixelSize: 34 * root.unit
            minimumPixelSize: 16 * root.unit
        }

        Text {
            id: spoken
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: userLine.visible ? userLine.bottom : parent.top
            anchors.topMargin: 6 * root.unit
            anchors.bottom: parent.bottom
            anchors.bottomMargin: musicText.visible ? musicText.height : 0
            text: root.characters.slice(0, root.shown).join("")
            color: "white"
            font.pixelSize: measure.fontInfo.pixelSize
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            elide: Text.ElideRight
            clip: true
        }
    }

    Text {
        id: musicText
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        visible: root.musicLine.length > 0
        text: root.musicLine
        color: "#808080"
        font.pixelSize: 16 * root.unit
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
    }
}
