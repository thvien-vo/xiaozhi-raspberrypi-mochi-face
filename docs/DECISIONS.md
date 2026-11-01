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

## Vision

**A background thread, not a separate process.** Measured on the laptop with a Python
heartbeat thread sleeping 1 ms while inference loops flat out in another thread: p99 gap
about 2 ms, worst 8.7 ms, for both runtimes at 320 and 640. Both release the GIL during
inference, so a process and its shared-memory plumbing would add complexity for nothing.
This must be re-checked on the Pi (see the hardware checklist).

**ncnn first, onnxruntime as fallback.** Ultralytics publish ncnn as the faster option on
the Pi. On this x86 laptop onnxruntime is slightly faster at 320 and slower at 640, so
laptop numbers do not settle it. Run `scripts/benchmark_vision.py` with `--backend` on the
Pi and set `VISION.BACKEND` if needed. Their detections were identical on a test photo.

**Idle inference threads sleep.** With default settings both runtimes spin between
inferences, using 62 to 112 % of a core at 4 inferences/s. `allow_spinning=0` and
`OMP_WAIT_POLICY=PASSIVE` bring that down to 20 to 30 %, at a cost of about 10 ms per
inference. At 4 Hz the saved CPU is worth far more, on a Pi that also runs audio and UI.

**`ncnn.Mat(array)` is not used.** It keeps a mismatched channel stride, so the network
silently sees a scrambled image and detects nothing. `Mat.from_pixels` is correct.

**Each input size has its own model file.** Exported models embed anchors for one size,
so one file cannot serve both. Paths contain `{imgsz}`, and a size mismatch raises an
error that says what to do, rather than returning wrong boxes.

**Models are not committed.** `models/yolo/` is git-ignored. YOLO11 weights are AGPL-3.0,
this repository is MIT, and the files are 10 MB each. `scripts/export_yolo.py` creates
them. If this project is ever distributed or sold, Ultralytics' AGPL terms or an
Enterprise licence apply to the weights. Keeping Ultralytics out of the runtime does not
change that, because exported weights are still derived from them.

**`capture_jpeg` is the routing point for the shared camera.** Every existing caller
already goes through it, so `take_photo` and the settings test needed no changes. A
process-wide `get_active_camera_service()` is used because those callers have no access
to the container; it follows the same pattern as `get_config()`.

**The settings camera test and device enumeration suspend the shared camera.** Both must
open devices directly, possibly another one. The service closes the device, lets them
run, then reopens it. A short gap in the preview is better than a "device busy" failure.

**Detection runs only when something needs it.** The GUI preview keeps it on. In CLI, TUI
and GPIO modes the first `detect_objects` call starts it and it stops after
`VISION.IDLE_STOP_S`, so those modes do not hold the camera open or use CPU for nothing.

**Frames are cleared when the device closes.** A reader must never receive an old frame as
if it were live, so a disconnected camera returns no frame and `detect_objects` says so.

**`detect_objects` left and right are the camera's own.** They are thirds of an unmirrored
image. If the camera is mounted so this feels reversed, that is a change to make in
`horizontal_position`.

**`.python-version` is now 3.12.** It said 3.10, which would make a plain `uv sync` build
the wrong environment.

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
