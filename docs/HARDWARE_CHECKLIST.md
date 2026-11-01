# Hardware checklist

Everything here needs the real hardware, so none of it could be checked on the Windows
laptop. Setup steps are in [raspberry-pi-5.md](raspberry-pi-5.md).

Measure with the app running normally, in kiosk mode, on an actively cooled Pi.

## Installation

- [ ] OS is Trixie, or the Bookworm change in section 1 of the guide has been made
- [ ] `uv sync` completes on the Pi with no missing wheels
- [ ] The import check in section 3 prints OK
- [ ] Python is 3.12 from uv, not the system 3.13
- [ ] The app starts on the locked versions (tested on Windows, not on ARM)

## Display and face

- [ ] Kiosk window fills the screen with no title bar or visible cursor
- [ ] Resolution is 1024x600, or the configured one, and the image is not stretched
- [ ] `FACE.SHOW_FPS` shows close to 60 FPS with the preview on and while speaking
- [ ] OpenGL ES works on Wayland (`main.py` selects the OpenGL backend)
- [ ] Eyes look smooth, without tearing, on the Waveshare panel
- [ ] Text is readable at arm's length, and long sentences fit
- [ ] Vietnamese, Chinese and emoji display correctly, if you use them
- [ ] Hearts, tears and "z" appear in `--face-demo`
- [ ] If the panel has touch: tapping starts a conversation and wakes the face
- [ ] Esc quits with a keyboard attached

## Audio (Jabra)

- [ ] The app selects the Jabra for input and output, confirmed in the log
- [ ] It is still selected after a reboot with other USB devices attached
- [ ] Wake word triggers at normal speaking distance
- [ ] Wake word with `NUM_THREADS` 2 keeps up, with no lag
- [ ] No echo of the assistant reaches the server with software AEC off
- [ ] **No stuttering in TTS while detection and the preview run.** This is the most
  important check. The laptop showed inference does not block Python threads, but the Pi
  has far less CPU to spare. If it stutters, follow Tuning in the guide.
- [ ] Interrupting the assistant by voice works

## Camera

- [ ] The USB camera opens with `CAMERA.backend` `opencv` over V4L2
- [ ] Requested 640x480 at 15 FPS is what the log reports
- [ ] The right node is used (cameras often expose several `/dev/video*`)
- [ ] **Unplug and replug during use**: the preview hides, `detect_objects` reports the
  camera problem, and both recover by themselves. Simulated in tests on the laptop only.
- [ ] Unplugging does not hang the app (a stuck V4L2 read is limited by a timeout)
- [ ] The app starts normally with no camera attached

## Vision

- [ ] `benchmark_vision.py` results, ncnn at 320: _____ ms, onnxruntime at 320: _____ ms
- [ ] `VISION.BACKEND` is set to the faster one. The laptop cannot predict this.
- [ ] ncnn at 640 is usable, if you want the extra range: _____ ms
- [ ] Idle-thread settings work on ARM: CPU during normal running is _____ %. Spinning
  threads were reduced on Windows. Check `OMP_WAIT_POLICY` has the same effect here.
- [ ] CPU temperature stays below throttling (`vcgencmd measure_temp`) after 30 minutes
- [ ] `vcgencmd get_throttled` reports `0x0`, otherwise check the power supply
- [ ] Preview runs at about 12 FPS with boxes in the right places

## With a real server

- [ ] Asking "what's in front of you?" makes the assistant call `detect_objects`
- [ ] It replies in a natural sentence, without reading out confidence numbers
- [ ] "Take a photo" and reading text use `take_photo`, not `detect_objects`
- [ ] `take_photo` works while detection runs, and without vision enabled
- [ ] Left and right feel correct for how the camera is mounted
- [ ] TTS text matches what is spoken, and the typewriter speed feels right
- [ ] The reply emotion shows during speech (the order of `llm` and `tts` messages from a
  real server was assumed from the protocol, not observed)
- [ ] `thinking` shows between your sentence and the reply

## Boot and recovery

- [ ] The app starts by itself after a cold boot, with no keyboard attached
- [ ] Audio and camera are ready when it starts (no race at boot)
- [ ] `kill -9` on the process restarts it within seconds
- [ ] Esc leaves it closed
- [ ] It reconnects after the network drops
- [ ] 24-hour soak: no memory growth, face still animating
