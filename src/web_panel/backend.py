"""What the web panel can see and do, independent of aiohttp and of the Qt app.

RobotBackend executes validated commands (servo, tracking, gestures, recordings, face,
chat) and builds the state snapshot sent to browsers. The app-specific parts (face,
listening state, sending text) come in through AppHooks and AppState, so the same
backend runs inside the app (src/plugins/web_panel.py) and in the stand-alone dev server
with a fake servo (scripts/web_panel_dev.py).

Thread safety: commands arrive on the web server's thread; app state is written from the
app's event loop. Everything shared is behind a lock or is itself thread-safe (the servo
controller, the tracker's flags).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from src.logging import get_logger
from src.music.library import LibraryError
from src.servo.motion import (
    GESTURE_LABELS,
    SOURCE_WEB,
    ControlArbiter,
    MotionLibrary,
    MotionPlayer,
    MotionRecorder,
    start_gesture,
)
from src.ui.face_color import (
    EMOTION_COLORS,
    RAINBOW_PERIOD_S,
    RAINBOW_SATURATION,
    active_expression,
    palette_snapshot,
)
from src.web_panel.commands import Command, CommandError

logger = get_logger()

CHAT_HISTORY = 60

DEVICE_STATE_LABELS = {
    "idle": "Rảnh",
    "listening": "Đang nghe",
    "speaking": "Đang nói",
    "notifying": "Đang báo",
    "connecting": "Đang kết nối",
}


@dataclass
class AppHooks:
    """App actions; each must be safe to call from any thread and must not block."""

    set_emotion: Callable[[str], None] | None = None
    set_auto_mode: Callable[[bool], None] | None = None
    send_text: Callable[[str], None] | None = None
    face_wake: Callable[[], None] | None = None


@dataclass
class AppState:
    """App-side state the panel shows; written by the app, read by the web thread."""

    device_state: str = "idle"
    emotion: str = "neutral"
    # What the face itself shows (from the GUI FaceModel when there is one)
    face_visual: str = "idle"
    face_activity: str = "awake"
    emotions: tuple[str, ...] = ()
    auto_mode: bool | None = None
    always_listen: bool = False
    face_available: bool = False
    text_supported: bool = False
    protocol_connected: bool = False
    _chat: deque = field(default_factory=lambda: deque(maxlen=CHAT_HISTORY))
    _chat_seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    on_change: Callable[[], None] | None = None

    def update(self, **values) -> None:
        changed = False
        with self._lock:
            for key, value in values.items():
                if getattr(self, key) != value:
                    setattr(self, key, value)
                    changed = True
        if changed:
            self._notify()

    def add_chat(self, role: str, text: str, note: str = "") -> None:
        """role: user, robot, or ignored (a transcript the STT filter dropped)."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            # The server repeats a sentence (sentence_start/end); keep one copy
            if self._chat and self._chat[-1]["role"] == role and self._chat[-1]["text"] == text:
                return
            self._chat_seq += 1
            entry = {"id": self._chat_seq, "role": role, "text": text[:1000], "time": time.time()}
            if note:
                entry["note"] = note[:200]
            self._chat.append(entry)
        self._notify()

    def chat_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [m for m in self._chat if m["id"] > seq]

    def _notify(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                logger.debug("AppState change callback failed", exc_info=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "device_state": self.device_state,
                "device_state_label": DEVICE_STATE_LABELS.get(self.device_state, self.device_state),
                "emotion": self.emotion,
                "expression": active_expression(self.emotion, self.face_visual, self.face_activity),
                "face_visual": self.face_visual,
                "face_activity": self.face_activity,
                "emotions": list(self.emotions),
                "auto_mode": self.auto_mode,
                "always_listen": self.always_listen,
                "face_available": self.face_available,
                "text_supported": self.text_supported,
                "protocol_connected": self.protocol_connected,
                "chat_seq": self._chat_seq,
            }


class RobotBackend:
    def __init__(
        self,
        controller=None,
        tracker=None,
        arbiter: ControlArbiter | None = None,
        player: MotionPlayer | None = None,
        library: MotionLibrary | None = None,
        app_state: AppState | None = None,
        hooks: AppHooks | None = None,
        camera=None,
        save_setting: Callable[[str, object], None] | None = None,
        eye_colors=None,
        face_frames=None,
        music=None,
        levels=None,
        rest=None,
        stt_filter=None,
        stt_gate=None,
        vad=None,
    ) -> None:
        self.controller = controller
        self.tracker = tracker
        hold = controller.settings.manual_hold_sec if controller is not None else 5.0
        self.arbiter = arbiter or ControlArbiter(tracker, hold)
        if player is None and controller is not None:
            player = MotionPlayer(controller, self.arbiter)
        self.player = player
        self.recorder = MotionRecorder()
        self.library = library
        self.app = app_state or AppState()
        self.hooks = hooks or AppHooks()
        self.camera = camera
        self._save_setting = save_setting
        self.eye_colors = eye_colors  # src.ui.face_color.EyeColorState
        self.face_frames = face_frames  # src.web_panel.face_stream.FaceFrameHub
        self.music = music  # src.web_panel.music.MusicControl
        self.levels = levels  # src.audio_codecs.levels.AudioLevels
        self.rest = rest  # src.core.rest_mode.RestController
        self.stt_filter = stt_filter  # src.audio_processing.stt_filter.SttFilter
        self.stt_gate = stt_gate  # src.core.stt_gate.SttGate
        self.vad = vad  # src.audio_processing.vad_gate.MicVadGate
        if stt_filter is not None:
            stt_filter.add_listener(self._changed)
        if vad is not None:
            vad.add_listener(self._changed)
        if rest is not None:
            rest.add_listener(lambda _resting, _source: self._changed())
        if levels is not None:
            levels.add_listener(self._changed)
        if eye_colors is not None:
            eye_colors.add_listener(lambda _color, _mode: self._changed())
        self._listeners: list[Callable[[], None]] = []
        self._motions_cache: list[dict] | None = None
        if self.player is not None:
            self.player.on_change = self._changed
        self.app.on_change = self._changed

    # ----- Change notification (wakes the broadcaster) -----

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _changed(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                logger.debug("Backend listener failed", exc_info=True)

    # ----- Limits -----

    @property
    def pan_limit(self) -> float:
        return self.controller.settings.pan_limit if self.controller is not None else 90.0

    @property
    def tilt_limit(self) -> float:
        return self.controller.settings.tilt_limit if self.controller is not None else 45.0

    # ----- Commands -----

    def handle(self, cmd: Command) -> dict:
        """Execute one command; returns {"ok": bool, "message": str, ...}, or for music a
        concurrent Future of it."""
        if cmd.type.startswith("music_"):
            return self._handle_music(cmd)
        handler = getattr(self, f"_cmd_{cmd.type}", None)
        if handler is None:
            raise CommandError(f"lệnh không hỗ trợ: {cmd.type}")
        result = handler(**cmd.args)
        return result if isinstance(result, dict) else {"ok": True}

    def _need_servo(self) -> None:
        if self.controller is None:
            raise CommandError("servo đang tắt (SERVO.ENABLED=false)")

    def _handle_music(self, cmd: Command):
        if self.music is None:
            return {"ok": False, "message": "chức năng nhạc không chạy"}
        try:
            return getattr(self.music, cmd.type.removeprefix("music_"))(**cmd.args)
        except LibraryError as e:
            return {"ok": False, "message": str(e)}

    def _cmd_volume(self, channel: str, volume: float, save: bool) -> dict:
        if self.levels is None:
            return {"ok": False, "message": "không chỉnh được âm lượng"}
        self.levels.set(channel, volume, persist=save)
        return {"ok": True}

    def _cmd_volume_step(self, channel: str, delta: float) -> dict:
        if self.levels is None:
            return {"ok": False, "message": "không chỉnh được âm lượng"}
        self.levels.change(channel, delta)
        return {"ok": True}

    def _cmd_mute(self, muted: bool | None) -> dict:
        if self.levels is None:
            return {"ok": False, "message": "không chỉnh được âm lượng"}
        now = self.levels.toggle_muted() if muted is None else self.levels.set_muted(muted)
        return {"ok": True, "message": "Đã tắt tiếng" if now else "Đã bật tiếng"}

    # ----- STT hallucination filter and VAD -----

    def _need_filter(self):
        if self.stt_filter is None:
            raise CommandError("bộ lọc câu ảo giác không chạy")
        return self.stt_filter

    def _cmd_stt_filter(
        self,
        enabled: bool | None,
        min_chars: int | None,
        reply_filter: bool | None = None,
        reset_context: bool | None = None,
    ) -> dict:
        f = self._need_filter()
        if enabled is not None:
            f.set_enabled(enabled)
        if min_chars is not None:
            f.set_min_chars(min_chars)
        if reply_filter is not None:
            f.set_reply_enabled(reply_filter)
        if reset_context is not None:
            f.set_reset_context(reset_context)
        if enabled is not None:
            return {"ok": True, "message": "Đã bật bộ lọc" if enabled else "Đã tắt bộ lọc"}
        if reply_filter is not None:
            return {"ok": True, "message": "Đã bật lọc câu trả lời" if reply_filter else "Đã tắt lọc câu trả lời"}
        if reset_context is not None:
            return {"ok": True, "message": "Sẽ làm mới phiên server sau lượt bị bỏ" if reset_context else "Không làm mới phiên server"}
        return {"ok": True, "message": f"Câu ngắn hơn {f.min_chars} chữ cái sẽ bị bỏ qua"}

    def _cmd_stt_pattern_add(self, pattern: str) -> dict:
        try:
            added = self._need_filter().add_pattern(pattern)
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Đã thêm mẫu: {added}"}

    def _cmd_stt_pattern_remove(self, pattern: str) -> dict:
        try:
            removed = self._need_filter().remove_pattern(pattern)
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Đã xóa mẫu: {removed}"}

    def _cmd_stt_patterns_reset(self) -> dict:
        self._need_filter().reset_patterns()
        return {"ok": True, "message": "Đã đặt lại danh sách mẫu mặc định"}

    def _cmd_stt_recent_clear(self) -> dict:
        self._need_filter().clear_recent()
        return {"ok": True}

    def _cmd_vad(self, save: bool = True, **values) -> dict:
        if self.vad is None:
            return {"ok": False, "message": "VAD không chạy (không có micro)"}
        new = self.vad.update(save=save, **values)
        self._changed()
        if values.get("enabled") is not None:
            return {"ok": True, "message": "Đã bật VAD" if new.enabled else "Đã tắt VAD: gửi micro liên tục"}
        return {"ok": True}

    def stt_filter_snapshot(self) -> dict | None:
        if self.stt_filter is None:
            return None
        snap = self.stt_filter.snapshot()
        if self.stt_gate is not None:
            snap["gate"] = self.stt_gate.snapshot()
        return snap

    @property
    def resting(self) -> bool:
        return bool(self.rest is not None and self.rest.resting)

    def _cmd_rest(self, enabled: bool) -> dict:
        if self.rest is None:
            return {"ok": False, "message": "chế độ nghỉ không chạy"}
        result = self.rest.request(enabled, "web")
        self._changed()
        return result

    def _cmd_ping(self) -> dict:
        return {"ok": True, "pong": time.time()}

    def _cmd_set_angles(self, pan: float | None, tilt: float | None) -> dict:
        self._need_servo()
        if not self.arbiter.request(SOURCE_WEB):
            return {"ok": False, "message": "đang bị điều khiển bởi nguồn ưu tiên cao hơn"}
        if self.player is not None and self.player.playing:
            self.player.stop()
        pan_now, tilt_now = self.controller.set_angles(pan, tilt)
        self.recorder.add(pan_now, tilt_now)
        self._changed()
        return {"ok": True}

    def _cmd_center(self) -> dict:
        return self._cmd_set_angles(0.0, 0.0)

    def _cmd_release(self) -> dict:
        """The user let go of the head: tracking may resume after the usual hold."""
        self.arbiter.release(SOURCE_WEB)
        return {"ok": True}

    def _cmd_tracking(self, enabled: bool) -> dict:
        if enabled and self.resting:
            return {"ok": False, "message": "robot đang nghỉ: thức dậy rồi mới bật bám người"}
        if self.tracker is None:
            return {"ok": False, "message": "không có bám người (camera/YOLO hoặc servo đang tắt)"}
        self.tracker.set_enabled(enabled)
        self._changed()
        return {"ok": True, "message": "Đã bật bám người" if enabled else "Đã tắt bám người"}

    def _cmd_tracking_params(self, kp_pan, kp_tilt, deadband, save: bool) -> dict:
        self._need_servo()
        s = self.controller.settings  # The tracker shares this object
        if kp_pan is not None:
            s.kp_pan = kp_pan
        if kp_tilt is not None:
            s.kp_tilt = kp_tilt
        if deadband is not None:
            s.deadband = deadband
        if save and self._save_setting is not None:
            self._save_setting("SERVO.KP_PAN", round(s.kp_pan, 2))
            self._save_setting("SERVO.KP_TILT", round(s.kp_tilt, 2))
            self._save_setting("SERVO.DEADBAND", round(s.deadband, 3))
        self._changed()
        return {"ok": True, "message": "Đã lưu vào cấu hình" if save else "Đã áp dụng"}

    def _cmd_gesture(self, name: str) -> dict:
        self._need_servo()
        ok, message = start_gesture(self.player, self.controller, name, SOURCE_WEB)
        return {"ok": ok, "message": message}

    def _cmd_stop_motion(self) -> dict:
        if self.player is not None:
            self.player.stop()
        return {"ok": True}

    def _cmd_record_start(self) -> dict:
        self._need_servo()
        pan, tilt = self.controller.angles
        self.recorder.start(pan, tilt)
        self._changed()
        return {"ok": True, "message": "Đang ghi: kéo đầu robot để ghi động tác"}

    def _cmd_record_cancel(self) -> dict:
        self.recorder.cancel()
        self._changed()
        return {"ok": True}

    def _cmd_record_stop(self, name: str) -> dict:
        if not self.recorder.recording:
            return {"ok": False, "message": "chưa bắt đầu ghi"}
        frames = self.recorder.stop()
        self._changed()
        if self.library is None:
            return {"ok": False, "message": "không có thư mục lưu động tác"}
        if len(frames) < 2 or frames[-1].t < 0.2:
            return {"ok": False, "message": "động tác quá ngắn, chưa lưu"}
        saved = self.library.save(name, frames)
        self._motions_cache = None
        self._changed()
        return {"ok": True, "message": f"Đã lưu '{saved}' ({frames[-1].t:.1f} s)"}

    def _cmd_play_motion(self, name: str) -> dict:
        self._need_servo()
        if self.library is None:
            return {"ok": False, "message": "không có thư mục lưu động tác"}
        try:
            frames = self.library.load(name, self.pan_limit, self.tilt_limit)
        except FileNotFoundError:
            return {"ok": False, "message": f"không có động tác '{name}'"}
        except ValueError as e:
            return {"ok": False, "message": f"file động tác hỏng: {e}"}
        if not self.player.play(name, frames, SOURCE_WEB):
            return {"ok": False, "message": "đang bị điều khiển bởi nguồn ưu tiên cao hơn"}
        return {"ok": True, "message": f"Đang phát '{name}'"}

    def _cmd_delete_motion(self, name: str) -> dict:
        if self.library is None or not self.library.delete(name):
            return {"ok": False, "message": f"không có động tác '{name}'"}
        self._motions_cache = None
        self._changed()
        return {"ok": True, "message": f"Đã xóa '{name}'"}

    def _cmd_emotion(self, name: str) -> dict:
        if self.hooks.set_emotion is None:
            return {"ok": False, "message": "mặt robot không chạy (cần chế độ GUI)"}
        self.hooks.set_emotion(name)
        self.app.update(emotion=name)
        return {"ok": True}

    def _cmd_eye_color(self, color: str | None, mode: str | None, save: bool) -> dict:
        if self.eye_colors is None:
            return {"ok": False, "message": "mặt robot không chạy (cần chế độ GUI)"}
        try:
            self.eye_colors.set(color=color, mode=mode, persist=save)
        except ValueError as e:
            raise CommandError(str(e)) from e
        return {"ok": True}

    def _cmd_face_stream(self, enabled: bool) -> dict:
        # Per-page subscription: the server handles it before reaching here
        return {"ok": True}

    def _cmd_auto_mode(self, enabled: bool) -> dict:
        if self.app.always_listen:
            return {"ok": False, "message": "FACE.ALWAYS_LISTEN đang bật: luôn ở chế độ tự động"}
        if self.hooks.set_auto_mode is None:
            return {"ok": False, "message": "không đổi được chế độ"}
        self.hooks.set_auto_mode(enabled)
        return {"ok": True}

    def _cmd_face_wake(self) -> dict:
        if self.resting:
            return {"ok": False, "message": "robot đang nghỉ"}
        if self.hooks.face_wake is None:
            return {"ok": False, "message": "mặt robot không chạy"}
        self.hooks.face_wake()
        return {"ok": True}

    def _cmd_send_text(self, text: str) -> dict:
        if self.resting:
            return {"ok": False, "message": "robot đang nghỉ, không nhận tin nhắn: bấm Thức dậy trước"}
        if self.hooks.send_text is None or not self.app.text_supported:
            return {"ok": False, "message": "không gửi được tin nhắn chữ"}
        self.hooks.send_text(text)
        self.app.add_chat("user", text)
        return {"ok": True}

    # ----- Snapshot -----

    def motions(self) -> list[dict]:
        if self.library is None:
            return []
        if self._motions_cache is None:
            self._motions_cache = self.library.list()
        return self._motions_cache

    def servo_snapshot(self) -> dict:
        c = self.controller
        if c is None:
            return {"enabled": False, "connected": False}
        pan, tilt = c.angles
        current = c.estimated_angles()
        acked = c.acked_angles
        return {
            "enabled": True,
            "connected": c.connected,
            "port": c.port,
            "target": {"pan": round(pan, 2), "tilt": round(tilt, 2)},
            "current": None if current is None else {"pan": round(current[0], 2), "tilt": round(current[1], 2)},
            "acked": None if acked is None else {"pan": round(acked[0], 2), "tilt": round(acked[1], 2)},
            "last_reply": c.last_reply,
            "limits": {"pan": self.pan_limit, "tilt": self.tilt_limit},
            "owner": self.arbiter.owner,
            "hold_remaining": round(self.arbiter.remaining(), 1),
        }

    def tracking_snapshot(self) -> dict:
        t = self.tracker
        s = self.controller.settings if self.controller is not None else None
        return {
            "available": t is not None,
            "enabled": bool(t and t.enabled),
            "holding": bool(t and t.holding),
            "kp_pan": None if s is None else s.kp_pan,
            "kp_tilt": None if s is None else s.kp_tilt,
            "deadband": None if s is None else s.deadband,
        }

    def snapshot(self) -> dict:
        player = self.player.status() if self.player is not None else {"playing": None}
        return {
            "type": "state",
            "servo": self.servo_snapshot(),
            "tracking": self.tracking_snapshot(),
            "app": self.app.snapshot(),
            "motion": {
                **player,
                "recording": self.recorder.recording,
                "record_elapsed": round(self.recorder.elapsed(), 1),
                "library": self.motions(),
                "gestures": [{"name": k, "label": v} for k, v in GESTURE_LABELS.items()],
            },
            "camera": {"available": bool(self.camera is not None and self.camera.available())},
            "face": self.face_snapshot(),
            "music": self.music.snapshot() if self.music is not None else None,
            "volume": self.levels.snapshot() if self.levels is not None else None,
            "rest": self.rest.snapshot() if self.rest is not None else None,
            "stt_filter": self.stt_filter_snapshot(),
            "vad": self.vad.snapshot() if self.vad is not None else None,
        }

    def face_snapshot(self) -> dict:
        eye = self.eye_colors.snapshot() if self.eye_colors is not None else None
        frames = self.face_frames
        return {"eye": eye, "stream": bool(frames is not None and frames.available)}

    @staticmethod
    def face_constants() -> dict:
        """Sent once per page (hello): the color choices and the emotion mode's colors."""
        return {
            "palette": palette_snapshot(),
            "emotion_colors": EMOTION_COLORS,
            "rainbow_period_s": RAINBOW_PERIOD_S,
            "rainbow_saturation": RAINBOW_SATURATION,
        }
