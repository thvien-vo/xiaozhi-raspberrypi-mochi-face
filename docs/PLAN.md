# Robot face and on-device vision: implementation plan

Branch: `robot-face`. Target: Raspberry Pi 5 (CPU only), developed and tested on
Windows. Choices that were ambiguous are recorded in [DECISIONS.md](DECISIONS.md).

## What the existing code gives us

| Area | Existing piece | How it is used |
| --- | --- | --- |
| Display flow | `UiPresenter` -> `ViewPort.set_*` -> `MainWindowController` -> `MainModel` | The face hooks in here. No parallel path. |
| Emotions | `EmotionService` knows names and GIF files | Gains name normalisation; the GIF path stays as a fallback. |
| Device state | `UIPlugin.on_device_state_changed` | Forwarded to the face. `thinking` does not exist as a device state, so it is derived. |
| Camera | `capture_backend.capture_jpeg` opens the device per call | Every caller already goes through it, so it becomes the routing point to the shared camera service. |
| MCP | `McpPlugin.setup` registers camera tools | `detect_objects` is registered the same way. |
| Lifecycle | Container shared services + `ResourcePool` | Vision runtime is a shared service, released on shutdown. |

## Phase 1: animated robot face

- `src/ui/gui/services/face_state.py`: Qt-free state machine (awake / sleepy /
  asleep, derived `thinking`), unit tested with a fake clock.
- `src/ui/gui/models/face_model.py`: QObject exposing face state and config to QML.
- `src/ui/gui/qml/face/`: `RobotFace.qml`, `Eye.qml`, `EyeOverlays`, `SpeechText.qml`,
  `FacePresets.js`, using only Rectangles and one-shot Canvas items.
- `src/ui/gui/qml/windows/FaceWindow.qml`, `face_main.qml`: opaque black window, kiosk
  option, controls on hover in windowed mode only.
- `UiPresenter` passes speech role and device state through optional viewport methods;
  CLI / TUI / GPIO are unaffected.
- `python main.py --face-demo` cycles every emotion and state with no server.
- `FACE.STYLE = "gif"` keeps the old window.

## Phase 2: YOLO detection and MCP tool

- `scripts/export_yolo.py`: run once, Ultralytics only in this script. Exports NCNN and ONNX.
- `src/vision/detector.py`: letterbox, YOLO11 decode, NMS, NCNN and ONNX Runtime backends.
- `src/vision/camera_service.py`: single device owner, reference counted, reconnects.
- `src/vision/detection_service.py`: rate-limited background inference.
- `src/vision/runtime.py`: owns both; started and stopped by `VisionPlugin`.
- `src/mcp/tools/vision/`: `detect_objects`.
- `capture_jpeg` routes through the camera service, so `take_photo` and the settings
  camera test share the device.
- `scripts/benchmark_vision.py`: FPS and inference timings with current settings.

## Phase 3: live preview

- `src/ui/gui/services/camera_preview.py`: background thread composes a small frame with
  boxes, `QQuickImageProvider` hands it to QML at a capped rate.
- `qml/face/CameraPreview.qml`: corner overlay, hidden whenever frames stop.

## Phase 4: Raspberry Pi 5 deployment

- `docs/raspberry-pi-5.md`, `docs/HARDWARE_CHECKLIST.md`.
- `deploy/raspberry-pi/`: config preset, systemd user service, installer.
- `scripts/apply_config_preset.py`.

## Phase 5: review and report

Full test run, start GUI / CLI / TUI / GPIO modes, write `docs/REPORT.md`.

## Verification after each phase

Tests pass, `ruff` is clean on new code, the app imports and starts, new scripts run,
then commit. Anything needing real hardware goes in the checklist.
