# Decisions

Choices made while building the robot face and vision features, with the reason for
each. Newest entries are added at the end of each section.

## Face

**`thinking` is derived, not a device state.** The device stays in `LISTENING` between
the final `stt` message and the first TTS audio. `FaceStateMachine` shows `thinking`
during that gap, with a timeout (`FACE.THINKING_TIMEOUT_S`) in case no reply arrives.

**The reply emotion is kept while speaking.** `UiPresenter.show_device_state` used to
reset the emotion to neutral on every state change. The server can send the `llm`
emotion just before `tts start`, so the reset wiped it. `SPEAKING` no longer resets the
emotion; idle and listening still do. This also corrects the GIF window.

**Only `sentence_start` drives the typewriter.** `sentence_end` can repeat the same
text, which would restart the animation mid-sentence.

**Face extensions on the viewport are optional.** `set_speech_text`, `end_speech` and
`set_device_state` are looked up with `getattr`, the same pattern as `set_button_text`,
so CLI, TUI and GPIO need no changes.

**Rectangles and one-shot canvases, no `QtQuick.Shapes`.** Lids are black rectangles
over white rounded rectangles, which relies on the pure black background. Hearts and
spirals are painted once into a `Canvas` and then only transformed. This keeps per-frame
work to transforms and avoids re-tessellating paths on the Pi.

**Windowed mode keeps controls, kiosk does not.** The old window was the only way to
reach Settings, text input and mode switching, so a hover bar brings them back on the
laptop. In kiosk mode the bar is never created. Tapping the face starts or stops a
conversation, or only wakes the face if it was asleep or sleepy.

**The native window frame is used outside kiosk mode.** The frameless `AppWindow` needs
an opacity mask layer for its rounded corners, which is wasted work for a fullscreen
black window. `FaceWindow` is a plain opaque `Window`.

**A QML failure falls back to the GIF window.** If `face_main.qml` fails to load the
app logs the error and loads `main.qml` rather than exiting.

**Music updates count as activity**, so the face does not fall asleep during playback.

## Dependencies and environment

**`vl_camera.py` no longer imports `httpx`.** `openai` 3.x dropped `httpx` as a
dependency and this project never declared it, so on a fresh install the import failed.
Because `register.py` imported `vl_camera` at module level, that one failure stopped
all MCP tools from registering, on `main` as well. `vl_camera` now uses a plain float
timeout, which every supported `openai` version accepts, and `register.py` imports it
only when a key is configured. The connect timeout is now 30 s rather than 10 s.

**Known issue left alone: Windows volume control.** `pycaw` changed its API, so
`src/mcp/tools/volume/windows.py` logs an error at startup and volume tools do not
work on Windows. It is handled gracefully and does not affect the Pi.

**Known issue left alone: one failing test.**
`tests/test_notify_playback.py::test_decode_failure_after_partial_audio_is_not_reported_as_success`
fails on an untouched `main`, before any of this work.
