# Robot face and on-device vision: report

All five phases are done on the `robot-face` branch, committed and not pushed. Everything
was tested on the Windows laptop, including on the exact package versions `uv.lock`
installs on the Pi. **Nothing has run on a Raspberry Pi yet**, and no real server
conversation has been tested. [HARDWARE_CHECKLIST.md](HARDWARE_CHECKLIST.md) lists what is
left to verify.

## What was built

**Animated face.** Two white eyes on black, drawn in QML from rectangles and a few one-shot
canvases. They blink, glance and breathe continuously. All 21 server emotions have a
preset, and unknown names become neutral. Eyes change for idle, listening, thinking and
speaking, and the face grows sleepy and falls asleep when left idle. The sentence being
spoken types out below the eyes, with the recognized user speech above it in grey.
`FACE.FULLSCREEN` gives a kiosk with no frame or cursor. `FACE.STYLE = "gif"` restores the
old window, which is also the automatic fallback if the face fails to load.

**Object detection.** YOLO11n runs on the CPU through ncnn or onnxruntime, with PyTorch and
Ultralytics used only by the one-time export script. One `CameraService` owns the device,
and detection, the preview and `take_photo` all share it. It reconnects by itself after a
disconnect. The new `detect_objects` MCP tool returns labels, counts, confidence and
left/center/right. In CLI, TUI and GPIO modes detection runs only on demand.

**Live preview.** A corner overlay shows the camera with boxes and labels at about 12 FPS.
It hides itself when the camera or vision fails, and the face carries on.

**Pi deployment.** Install guide, config preset, systemd user service with desktop
autostart, and a hardware checklist.

## Commands on Windows

Run these from `D:\Xiaozhi_Laptop\py-xiaozhi`, with the venv's Python.

```powershell
# Normal mode: robot face, 1024x600 window
D:\Xiaozhi_Laptop\venv\Scripts\python.exe main.py

# Face demo: all emotions and states, no server needed
D:\Xiaozhi_Laptop\venv\Scripts\python.exe main.py --face-demo

# Face demo with the preview
D:\Xiaozhi_Laptop\venv\Scripts\python.exe main.py --face-demo --demo-camera test-pattern
D:\Xiaozhi_Laptop\venv\Scripts\python.exe main.py --face-demo --demo-camera real

# YOLO benchmark (--test-pattern runs without a camera)
D:\Xiaozhi_Laptop\venv\Scripts\python.exe scripts\benchmark_vision.py
D:\Xiaozhi_Laptop\venv\Scripts\python.exe scripts\benchmark_vision.py --backend onnxruntime --size 640

# Tests
D:\Xiaozhi_Laptop\venv\Scripts\python.exe -m pytest
```

Hover over the window for Talk, Interrupt, a message box, Mode and Settings. Clicking the
face also starts or stops a conversation.

The models are already exported to `models\yolo\` on this laptop. That folder is
git-ignored, so copy it to the Pi as described in the guide.

## Verification results

| Check | Result |
| --- | --- |
| Tests, laptop venv | 218 passed, 1 failed |
| Tests, `uv.lock` versions (PySide6 6.10, OpenCV 4.11) | 218 passed, 1 failed |
| GUI robot, GUI GIF, kiosk, demo, CLI, TUI start | All start, on both environments |
| GPIO | Falls back to CLI on Windows, so the real GPIO path did not run |
| Whole app: protocol messages, MCP calls, shutdown | 20 of 20 checks passed |
| Real webcam | `detect_objects` and `take_photo` both worked, one device open |
| Detection accuracy | Same objects as PyTorch on a test photo at 640; ncnn and onnxruntime identical |

The one failure is `test_decode_failure_after_partial_audio_is_not_reported_as_success`,
which fails the same way on an untouched `main`.

Laptop benchmark with the Pi-style limits of 2 threads, CPU only:

| Backend | 320 | 640 |
| --- | --- | --- |
| ncnn | 23 ms | 79 ms |
| onnxruntime | 15 ms | 59 ms |

These compare settings, not hardware: expect the Pi to be slower, and the ranking may
differ on ARM. With detection and the preview running, a 5 ms GUI timer stayed under 11 ms
at p99. The face's 165 FPS on the laptop is the panel's refresh rate and says nothing about
60 FPS on the Pi.

## Decisions

Reasons for each are in [DECISIONS.md](DECISIONS.md). The ones most worth knowing:

- **Background threads, not a process.** Measured: neither runtime blocks other Python
  threads during inference.
- **`thinking` is derived.** There is no such device state, so it is inferred between the
  final `stt` message and the first TTS audio.
- **The reply emotion now survives speaking.** The old code reset to neutral at the start
  of speech. This also changes the GIF window.
- **Model files are not committed.** They are AGPL-3.0 and this repository is MIT.
- **`detect_objects` is on by default**, and the preview keeps the webcam on whenever the
  GUI runs. Set `VISION.PREVIEW.ENABLED` to false for on-demand use only.

## Bugs found on the way, fixed

- All MCP tools failed to register on your venv, on `main` as well, because `vl_camera`
  imported `httpx`, which `openai` 3.x no longer installs. The cloud connect timeout is
  now 30 s instead of 10 s as a result.
- A configured audio name was overwritten with the full name including `hw:2,0`, so a USB
  reorder after reboot would have lost the Jabra. A configured name now stays as written.
- `.python-version` said 3.10, which would have built the wrong environment on the Pi.

## Known limitations and TODOs

- **Licensing:** YOLO11 weights are AGPL-3.0, and exporting them does not remove that. This
  matters if the robot is ever distributed or sold.
- **Bookworm:** the locked PySide6 needs glibc 2.39, so Pi OS must be Trixie. The guide
  gives a Bookworm workaround, which is untested.
- **Autostart is untested.** The service and desktop entry have only had syntax checks.
- **FACE and VISION have no Settings page.** They are edited in `config.json`, and changes
  need a restart. A new tab is the natural next step.
- **Windows volume tools do not work.** `pycaw` changed its API, so they log an error at
  startup. This is on `main` too, does not affect the Pi, and was left alone.
- **Packaging:** `py-xiaozhi.spec` was not updated for ncnn or onnxruntime, so a PyInstaller
  build may need them added.
- **Wake word:** the default model is still `models/zh` with `NUM_THREADS` 5 on the laptop.
  The Pi preset only lowers the threads, so set the model for your language.
- **Face design:** presets were judged from screenshots only. Timing and feel are worth
  tuning by eye in `FacePresets.js` and `RobotFace.qml`.

## Changed and added files

**Face:** `src/ui/gui/qml/face/` (`RobotFace`, `Eye`, `FaceOverlays`, `FacePresets.js`,
`SpeechText`, `FaceControls`, `CameraPreview`), `qml/windows/FaceWindow.qml`,
`qml/face_main.qml`, `qml/components/SettingsHost.qml`, `src/ui/gui/models/face_model.py`,
`src/ui/gui/services/face_state.py`, `src/ui/gui/face_demo.py`.

**Vision:** `src/vision/` (`camera_service`, `detector`, `detection_service`, `runtime`,
`settings`, `coco`), `src/mcp/tools/vision/`, `src/plugins/vision.py`,
`src/ui/gui/services/camera_preview.py`.

**Scripts:** `scripts/export_yolo.py`, `scripts/benchmark_vision.py`,
`scripts/apply_config_preset.py`.

**Deployment:** `deploy/raspberry-pi/` (`config.pi5.json`, `xiaozhi.service`,
`xiaozhi-autostart.desktop`, `install-autostart.sh`).

**Docs:** `docs/` (`PLAN`, `DECISIONS`, `REPORT`, `HARDWARE_CHECKLIST`, `raspberry-pi-5`).

**Tests:** `tests/test_robot_face.py`, `test_vision.py`, `test_camera_preview.py`,
`test_deployment.py`.

**Existing files changed:** `main.py`, `README.md`, `pyproject.toml`, `requirements.txt`,
`uv.lock`, `.gitignore`, `.gitattributes`, `.python-version`,
`src/utils/config_manager.py` (FACE and VISION sections), `src/utils/audio_device.py`,
`src/bootstrap/container.py`, `src/bootstrap/plugin_wiring.py`, `src/plugins/mcp.py`,
`src/plugins/ui.py`, `src/plugins/ui_presenter.py`,
`src/mcp/tools/camera/capture_backend.py`, `src/mcp/tools/camera/register.py`,
`src/mcp/tools/camera/vl_camera.py`, `src/ui/gui/manager.py`,
`src/ui/gui/main_controller.py`, `src/ui/gui/qml_host.py`,
`src/ui/gui/services/emotion_service.py`, `src/ui/gui/services/__init__.py`,
`src/ui/gui/models/__init__.py`, `src/ui/gui/qml/main.qml`,
`src/ui/gui/qml/components/qmldir`. `LICENSE` is untouched.
