"""STT hallucination filter, the turn gate, the microphone VAD and their web commands."""

import asyncio
import json

import numpy as np
import pytest

from src.audio_processing.stt_filter import (
    DEFAULT_PATTERNS,
    SttFilter,
    check_text,
    clean_pattern,
    fold_words,
)
from src.audio_processing.vad_gate import (
    EnergyDetector,
    MicVadGate,
    VadSettings,
    default_model_path,
)
from src.bootstrap.session import ConversationSession
from src.constants.constants import DeviceState, ListeningMode
from src.core.event_bus import EventBus, Events
from src.core.protocol_manager import ProtocolTransport
from src.core.state_manager import StateManager
from src.core.stt_gate import SttGate
from src.web_panel.backend import AppState, RobotBackend
from src.web_panel.commands import CommandError, parse_command


class Config:
    def __init__(self, **values):
        self.values = dict(values)
        self.saved = []

    def get_config(self, path, default=None):
        return self.values.get(path, default)

    def update_configs(self, updates):
        self.values.update(updates)
        self.saved.append(dict(updates))
        return True


# ----- Fuzzy matching -----

IGNORED = [
    "La La School",
    "lala school.",
    "La la scool!",
    "LA LA SCHOOL",
    "Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn",
    "ghien mi go",
    "Ghiền mì gõ.",
    "subscribe",
    "Subcribe kênh YouTube nhé!",
    "Đăng ký kênh",
    "đăng kí kênh cho mình nhé",
    "Theo dõi kênh",
    "Cảm ơn các bạn đã theo dõi",
    "cam on cac ban da theo doi",
    "Cảm ơn các bạn đã theo dõi và hẹn gặp lại các bạn trong những video tiếp theo",
    "Hẹn gặp lại các bạn trong những video tiếp theo.",
    "Các bạn hãy đăng kí kênh để ủng hộ mình nhé",
    "Thank you for watching",
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    # Heard by the robot on 2026-11-23 from a quiet room and from test music
    "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
    "Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn",
    "Hãy subscribe cho kênh La La School Để không bỏ lỡ những video hấp dẫn",
    "...",
    "",
    "ừ",
]

ANSWERED = [
    "mở nhạc La La School",
    "Mở bài La La School cho tôi",
    "subscribe là gì",
    "cảm ơn bạn",
    "cảm ơn",
    "Xin chào",
    "có",
    "tăng âm lượng",
    "bật phụ đề",
    "theo dõi tôi đi",
    "đăng ký học tiếng anh ở đâu",
    "hôm nay trời đẹp quá",
    "kể chuyện cho tôi nghe",
    "quay đầu sang trái",
]


@pytest.mark.parametrize("text", IGNORED)
def test_hallucinations_are_ignored(text):
    assert check_text(text, DEFAULT_PATTERNS).ignored


@pytest.mark.parametrize("text", ANSWERED)
def test_real_requests_are_answered(text):
    result = check_text(text, DEFAULT_PATTERNS)
    assert not result.ignored, result


def test_request_with_a_pattern_keeps_the_request_words():
    result = check_text("mở nhạc La La School", DEFAULT_PATTERNS)
    assert not result.ignored
    assert result.patterns == ("La La School",)
    assert result.leftover == "mo nhac"


def test_folding_removes_accents_case_and_punctuation():
    assert fold_words("Đăng KÝ, kênh!!") == ["dang", "ky", "kenh"]
    assert "".join(fold_words("La-la  SCHOOL")) == "lalaschool"


def test_short_patterns_must_match_exactly():
    # "kênh" (4 letters) must not fuzzily match "kem"
    assert not check_text("ăn kem", ["kênh"]).ignored
    assert check_text("kênh", ["kênh"]).ignored
    assert not check_text("kenk", ["kênh"]).ignored


def test_a_typo_never_swallows_a_whole_extra_word():
    # The long outro allows 3 edits; "và" next to it must not be eaten instead of the
    # thank-you pattern matching its own words
    result = check_text(
        "Cảm ơn các bạn đã theo dõi và hẹn gặp lại các bạn trong những video tiếp theo",
        DEFAULT_PATTERNS,
    )
    assert result.ignored and len(result.patterns) == 2


def test_min_chars_zero_keeps_empty_text_out_of_the_short_rule():
    assert not check_text("ừ", DEFAULT_PATTERNS, min_chars=0).ignored
    assert check_text("ừ", DEFAULT_PATTERNS, min_chars=2).reason == "short"


def test_very_long_text_is_never_ignored():
    text = " ".join(["subscribe"] * 31)
    assert not check_text(text, DEFAULT_PATTERNS).ignored


def test_clean_pattern_validates():
    assert clean_pattern("  la   la  ") == "la la"
    for bad in ("", "   ", "!!!", 5, None, "x" * 121):
        with pytest.raises(ValueError):
            clean_pattern(bad)


# ----- Filter store -----


def test_filter_reads_and_saves_its_config():
    cfg = Config(**{"STT_FILTER.PATTERNS": ["La La School"], "STT_FILTER.MIN_CHARS": 3})
    f = SttFilter.from_config(cfg)
    assert f.patterns == ["La La School"] and f.min_chars == 3 and f.enabled
    f.add_pattern("ghiền mì gõ")
    with pytest.raises(ValueError):
        f.add_pattern("GHIEN MI GO")  # The same pattern once folded
    f.remove_pattern("La La School")
    with pytest.raises(ValueError):
        f.remove_pattern("La La School")
    assert cfg.values["STT_FILTER.PATTERNS"] == ["ghiền mì gõ"]
    f.set_enabled(False)
    assert cfg.values["STT_FILTER.ENABLED"] is False
    assert not f.check("ghien mi go").ignored  # Off: nothing is ignored
    f.reset_patterns()
    assert cfg.values["STT_FILTER.PATTERNS"] == list(DEFAULT_PATTERNS)
    assert f.set_min_chars(99) == 20


def test_default_config_has_the_full_list():
    from src.utils.config_manager import ConfigManager

    section = ConfigManager.DEFAULT_CONFIG["STT_FILTER"]
    assert section["ENABLED"] is True and section["MIN_CHARS"] == 2
    assert section["PATTERNS"] == list(DEFAULT_PATTERNS)
    vad = ConfigManager.DEFAULT_CONFIG["VAD"]
    assert vad["ENABLED"] is True and vad["PREROLL_MS"] == 300


def test_recent_keeps_the_last_twenty():
    f = SttFilter()
    for i in range(25):
        f.record_ignored(f"La La School {i}", check_text("La La School", DEFAULT_PATTERNS))
    snap = f.snapshot()
    assert snap["ignored_total"] == 25
    assert len(snap["recent"]) == 20
    assert snap["recent"][0]["text"] == "La La School 24"  # Newest first
    assert snap["recent"][0]["pattern"] == "La La School"


# ----- Turn gate -----


def hallucinated_turn(text="Hãy subscribe cho kênh Ghiền Mì Gõ"):
    """What the official server sends for one recognized sentence (audio as 'AUDIO')."""
    return [
        {"type": "stt", "text": text},
        {"type": "tts", "state": "start"},
        {"type": "llm", "emotion": "happy", "text": "😀"},
        {"type": "mcp", "payload": {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                    "params": {"name": "self.music.play", "arguments": {}}}},
        {"type": "tts", "state": "sentence_start", "text": "Cảm ơn bạn đã ủng hộ!"},
        "AUDIO", "AUDIO", "AUDIO",
        {"type": "tts", "state": "sentence_end", "text": "Cảm ơn bạn đã ủng hộ!"},
        {"type": "tts", "state": "stop"},
    ]


def run_gate(gate, items):
    delivered = []
    for item in items:
        if item == "AUDIO":
            if gate.filter_audio():
                delivered.append(item)
        elif gate.filter_json(item):
            delivered.append(item)
    return delivered


def test_gate_swallows_the_whole_hallucinated_turn():
    gate = SttGate(SttFilter())
    events = []
    gate.on_drop = lambda turn, text, result: events.append(("drop", turn, text))
    gate.on_resume = lambda turn: events.append(("resume", turn))
    gate.on_tool_blocked = lambda payload: events.append(("tool", payload["id"]))

    delivered = run_gate(gate, hallucinated_turn())

    assert delivered == []
    assert events == [("drop", 1, "Hãy subscribe cho kênh Ghiền Mì Gõ"), ("tool", 7), ("resume", 1)]
    assert gate.dropped_audio_frames == 3
    assert gate.mic_blocked  # Until the session reopens listening
    gate.release_mic(1)
    assert not gate.mic_blocked


def test_late_audio_of_a_dropped_turn_is_not_played():
    gate = SttGate(SttFilter())
    run_gate(gate, hallucinated_turn())
    # Frames that arrive after the turn's tts stop (network jitter) stay blocked...
    assert run_gate(gate, ["AUDIO", "AUDIO"]) == []
    # ...until the next real turn starts speaking
    real = [{"type": "stt", "text": "kể chuyện cười"}, {"type": "tts", "state": "start"}, "AUDIO",
            {"type": "tts", "state": "stop"}]
    assert run_gate(gate, real) == real


def test_real_sentence_during_a_drop_is_delivered():
    gate = SttGate(SttFilter())
    run_gate(gate, hallucinated_turn()[:3])
    assert gate.suppressing
    msg = {"type": "stt", "text": "mở nhạc La La School"}
    assert gate.filter_json(msg)
    assert not gate.suppressing and not gate.mic_blocked


def test_real_turn_passes_untouched():
    gate = SttGate(SttFilter())
    turn = hallucinated_turn("quay đầu sang trái")
    assert run_gate(gate, turn) == turn


def test_typed_text_is_never_filtered():
    gate = SttGate(SttFilter())
    gate.note_typed("La La School")
    assert gate.filter_json({"type": "stt", "text": "la la school"})


def test_drop_ends_by_itself_when_the_server_never_says_stop():
    t = [100.0]
    gate = SttGate(SttFilter(), clock=lambda: t[0], max_suppress_s=5)
    resumed = []
    gate.on_resume = resumed.append
    run_gate(gate, hallucinated_turn()[:3])
    t[0] += 6
    assert gate.filter_json({"type": "hello"})
    assert resumed == [1] and not gate.suppressing


def test_other_messages_pass_during_a_drop():
    gate = SttGate(SttFilter())
    run_gate(gate, hallucinated_turn()[:2])
    assert gate.filter_json({"type": "goodbye"})
    assert gate.filter_json({"type": "mcp", "payload": {"method": "tools/list", "id": 1}})


# ----- Whole path: transport + gate + session -----


class FakeProtocol:
    def __init__(self):
        self.opened = True
        self.calls = []

    def is_audio_channel_opened(self):
        return self.opened

    async def send_abort_speaking(self, reason):
        self.calls.append(("abort", reason))

    async def send_start_listening(self, mode):
        self.calls.append(("listen", mode))

    async def send_mcp_message(self, payload):
        self.calls.append(("mcp", payload))
        return True


class FakeCodec:
    def __init__(self):
        self.cleared = 0

    async def clear_audio_queue(self):
        self.cleared += 1


class FakePlugins:
    def __init__(self):
        self.codec = FakeCodec()
        self.json = []

    def get_plugin(self, name):
        return self if name == "audio" else None

    async def notify_device_state_changed(self, _state):
        pass

    async def notify_incoming_json(self, message):
        self.json.append(message)


async def wired(monkeypatch):
    import src.bootstrap.session as session_mod

    monkeypatch.setattr(session_mod, "STT_RESUME_FALLBACK_S", 0.05)
    bus = EventBus()
    state = StateManager(bus)
    protocol = FakeProtocol()
    plugins = FakePlugins()
    session = ConversationSession(state, protocol, plugins, bus)
    session.bind_events(bus)
    gate = SttGate(SttFilter())
    transport = ProtocolTransport(bus)
    transport.set_stt_gate(gate)
    played = []

    async def play(data):
        played.append(data)

    transport.set_audio_handler(play)
    session.attach_stt_gate(gate, lambda coro, name: asyncio.get_running_loop().create_task(coro, name=name))
    state.set_listening_mode(ListeningMode.AUTO_STOP)
    state.set_keep_listening(True)
    await state.set_device_state(DeviceState.LISTENING)
    ignored = []

    async def on_ignored(data):
        ignored.append(data)

    bus.on(Events.STT_IGNORED, on_ignored)
    return transport, session, state, protocol, plugins, gate, played, ignored


def feed(transport, items):
    for item in items:
        if item == "AUDIO":
            transport._on_incoming_audio(b"\x00opus")
        else:
            transport._on_incoming_json(item)


async def settle():
    for _ in range(10):
        await asyncio.sleep(0.02)


async def test_hallucinated_turn_is_silent_and_runs_no_tool(monkeypatch):
    transport, session, state, protocol, plugins, gate, played, ignored = await wired(monkeypatch)
    states = []
    state._event_bus.on(Events.DEVICE_STATE_CHANGED, lambda d: _record(states, d))

    feed(transport, hallucinated_turn())
    await settle()

    assert played == []  # No TTS audio reached the player
    assert plugins.json == []  # No plugin saw stt/tts/llm/mcp (no tool, no face text)
    assert states == []  # The face never left "listening"
    assert state.device_state == DeviceState.LISTENING
    assert ("abort", None) in protocol.calls
    assert ("listen", ListeningMode.AUTO_STOP) in protocol.calls  # Listening reopened
    tool_answers = [c[1] for c in protocol.calls if c[0] == "mcp"]
    assert tool_answers and tool_answers[0]["id"] == 7 and tool_answers[0]["result"]["isError"]
    assert ignored and ignored[0]["text"] == "Hãy subscribe cho kênh Ghiền Mì Gõ"
    assert not gate.mic_blocked
    # Resumed once only, although both tts stop and the fallback timer fired
    assert protocol.calls.count(("listen", ListeningMode.AUTO_STOP)) == 1

    # The next real sentence is answered normally
    real = [{"type": "stt", "text": "kể chuyện cười"}, {"type": "tts", "state": "start"}, "AUDIO"]
    feed(transport, real)
    await settle()
    assert played == [b"\x00opus"]
    assert [m["type"] for m in plugins.json] == ["stt", "tts"]
    await transport._stop_audio_consumer()


async def _record(states, data):
    states.append(data)


async def test_resume_falls_back_when_no_tts_stop_comes(monkeypatch):
    transport, session, state, protocol, plugins, gate, played, ignored = await wired(monkeypatch)
    feed(transport, hallucinated_turn()[:3])  # stt, tts start, llm; then nothing
    await settle()
    assert ("listen", ListeningMode.AUTO_STOP) in protocol.calls
    assert not gate.mic_blocked
    await transport._stop_audio_consumer()


async def test_microphone_is_held_while_a_turn_is_dropped(monkeypatch):
    from src.core import stt_gate as gate_mod
    from src.plugins.audio import AudioPlugin

    gate = SttGate(SttFilter())
    monkeypatch.setattr(gate_mod, "_shared", gate)

    class Ctx:
        def should_capture_audio(self):
            return True

    plugin = AudioPlugin()
    plugin._ctx = Ctx()
    assert plugin._should_send_microphone_audio()
    gate.filter_json({"type": "stt", "text": "La La School"})
    assert not plugin._should_send_microphone_audio()
    gate.release_mic()
    assert plugin._should_send_microphone_audio()


# ----- VAD -----

SR = 16000


def _resonator(x, f, bw):
    r = np.exp(-np.pi * bw / SR)
    th = 2 * np.pi * f / SR
    a1, a2 = -2 * r * np.cos(th), r * r
    y = np.zeros_like(x)
    y1 = y2 = 0.0
    for i, v in enumerate(x):
        yi = v - a1 * y1 - a2 * y2
        y[i] = yi
        y2, y1 = y1, yi
    return y


def synthetic_voice(seconds, level=0.3):
    """Voiced speech stand-in: glottal pulses through vowel formants, syllable rhythm."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    phase = np.cumsum((120 + 20 * np.sin(2 * np.pi * 0.7 * t)) / SR)
    pulses = (np.diff(np.floor(phase), prepend=0) > 0).astype(float)
    vowels = [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480)]
    out = np.zeros(n)
    seg = int(SR * 0.25)
    rng = np.random.default_rng(0)
    for k in range(0, n, seg):
        f1, f2, f3 = vowels[(k // seg) % len(vowels)]
        x = pulses[k:k + seg] + 0.02 * rng.standard_normal(len(pulses[k:k + seg]))
        out[k:k + seg] = _resonator(x, f1, 80) + 0.6 * _resonator(x, f2, 100) + 0.3 * _resonator(x, f3, 150)
    out *= 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t) ** 2
    return (out / np.max(np.abs(out)) * level).astype(np.float32)


def frames(signal):
    for i in range(0, len(signal) - 319, 320):
        yield signal[i:i + 320]


def push_all(gate, signal, tag="f"):
    sent = []
    for i, frame in enumerate(frames(signal)):
        sent.extend(gate.push(frame, f"{tag}{i}".encode()))
    return sent


class ScriptedDetector:
    """Speech exactly where the frame's first sample is positive."""

    name = "scripted"

    def set_threshold(self, _):
        pass

    def reset(self):
        pass

    def is_speech(self, pcm, level_db):
        return bool(pcm[0] > 0)


def scripted(pattern_ms):
    """[(speech?, ms), ...] -> loud frames, positive first sample where speech."""
    out = []
    for speech, ms in pattern_ms:
        for _ in range(ms // 20):
            out.append(np.full(320, 0.2 if speech else -0.2, dtype=np.float32))
    return np.concatenate(out)


def test_gate_opens_after_min_speech_with_preroll_and_closes_after_hangover():
    gate = MicVadGate(VadSettings(min_speech_ms=300, preroll_ms=300, hangover_ms=400), detector=ScriptedDetector())
    signal = scripted([(False, 1000), (True, 1000), (False, 1000)])
    sent = push_all(gate, signal)
    names = [int(p[1:]) for p in sent]
    # Speech starts at frame 50; opening after 15 frames sends frames 35..64 at once
    assert names[0] == 35
    assert names == list(range(35, 50 + 50 + 20))  # Speech + 400 ms hangover, nothing after
    assert gate.opens == 1 and not gate.is_open


def test_short_noise_bursts_never_open_the_gate():
    gate = MicVadGate(VadSettings(min_speech_ms=300), detector=ScriptedDetector())
    signal = scripted([(False, 200), (True, 200), (False, 400), (True, 200), (False, 400)] * 5)
    assert push_all(gate, signal) == []


def test_quiet_frames_do_not_count_as_speech():
    gate = MicVadGate(VadSettings(energy_db=-30.0), detector=ScriptedDetector())
    quiet = np.concatenate([np.full(320, 0.005, dtype=np.float32)] * 100)  # About -46 dBFS
    assert push_all(gate, quiet) == []


def test_reset_closes_and_forgets_the_preroll():
    gate = MicVadGate(VadSettings(min_speech_ms=100, preroll_ms=300), detector=ScriptedDetector())
    push_all(gate, scripted([(True, 400)]), tag="a")
    assert gate.is_open
    gate.reset()
    sent = push_all(gate, scripted([(False, 200), (True, 100)]), tag="b")
    assert sent and all(p.startswith(b"b") for p in sent)


def test_disabled_vad_sends_everything():
    gate = MicVadGate(VadSettings(enabled=False), detector=ScriptedDetector())
    assert len(push_all(gate, np.zeros(320 * 10, dtype=np.float32))) == 10


def test_energy_detector_ignores_steady_noise():
    gate = MicVadGate(VadSettings(), detector=EnergyDetector())
    noise = (np.random.default_rng(1).standard_normal(SR * 3) * 0.01).astype(np.float32)
    assert push_all(gate, noise) == []


def test_settings_are_clamped_and_saved():
    saved = []
    gate = MicVadGate(save=saved.append)
    new = gate.update(threshold=5, hangover_ms=10, preroll_ms=250.4, bogus=1)
    assert new.threshold == 0.95 and new.hangover_ms == 200 and new.preroll_ms == 250
    assert saved[-1]["THRESHOLD"] == 0.95
    cfg = Config(**{"VAD.ENABLED": False, "VAD.MIN_SPEECH_MS": 5000, "VAD.ENERGY_DB": "x"})
    s = VadSettings.from_config(cfg)
    assert not s.enabled and s.min_speech_ms == 2000 and s.energy_db == -50.0


silero = pytest.mark.skipif(not default_model_path().exists(), reason="no Silero model")


@silero
@pytest.mark.parametrize(
    "name,signal",
    [
        ("silence", np.zeros(SR * 3, dtype=np.float32)),
        ("noise", (np.random.default_rng(2).standard_normal(SR * 3) * 0.03).astype(np.float32)),
        ("tone", (0.3 * np.sin(2 * np.pi * 440 * np.arange(SR * 3) / SR)).astype(np.float32)),
        ("chords", (0.1 * sum(np.sin(2 * np.pi * f * np.arange(SR * 3) / SR) for f in (262, 330, 392))).astype(np.float32)),
    ],
)
def test_silero_sends_nothing_for_non_speech(name, signal):
    gate = MicVadGate(VadSettings())
    assert push_all(gate, signal) == [], name
    assert gate.backend == "silero"


@silero
def test_silero_sends_speech_with_its_start():
    gate = MicVadGate(VadSettings())
    silence = np.zeros(SR, dtype=np.float32)
    noise = (np.random.default_rng(3).standard_normal(SR * 6) * 0.002).astype(np.float32)
    signal = np.concatenate([silence, synthetic_voice(1.5), np.zeros(SR * 2, dtype=np.float32)])
    signal = signal + noise[: len(signal)]
    sent = [int(p[1:]) for p in push_all(gate, signal)]
    assert gate.opens >= 1
    # Speech starts at frame 50: the pre-roll reaches back to before it
    assert sent[0] <= 50
    # Most of the 75 speech frames went out, and the long silence after did not
    assert len([i for i in sent if 50 <= i < 125]) >= 60
    assert max(sent) < 125 + 900 // 20 + 20


# ----- Web commands -----


def test_web_commands_are_validated():
    assert parse_command(json.dumps({"type": "stt_pattern_add", "pattern": "  la  la "}), 90, 45).args == {"pattern": "la la"}
    for bad in ({"type": "stt_pattern_add", "pattern": ""}, {"type": "stt_pattern_add", "pattern": 3},
                {"type": "stt_pattern_add", "pattern": "???"}, {"type": "stt_filter"},
                {"type": "stt_filter", "enabled": "yes"}, {"type": "vad"}, {"type": "vad", "threshold": "x"},
                {"type": "stt_pattern_remove"}, {"type": "stt_nope"}):
        with pytest.raises(CommandError):
            parse_command(json.dumps(bad), 90, 45)
    cmd = parse_command(json.dumps({"type": "vad", "threshold": 9, "hangover_ms": 1, "save": False}), 90, 45)
    assert cmd.args["threshold"] == 0.95 and cmd.args["hangover_ms"] == 200 and cmd.args["save"] is False
    cmd = parse_command(json.dumps({"type": "stt_filter", "min_chars": 3.6}), 90, 45)
    assert cmd.args == {"enabled": None, "min_chars": 4}


def test_web_commands_edit_the_filter_and_vad():
    cfg = Config()
    f = SttFilter.from_config(cfg)
    vad = MicVadGate(detector=ScriptedDetector())
    backend = RobotBackend(app_state=AppState(), stt_filter=f, stt_gate=SttGate(f), vad=vad)
    changes = []
    backend.add_listener(lambda: changes.append(1))

    def run(obj):
        return backend.handle(parse_command(json.dumps(obj), 90, 45))

    assert run({"type": "stt_pattern_add", "pattern": "xem video vui"})["ok"]
    assert not run({"type": "stt_pattern_add", "pattern": "XEM VIDEO VUI"})["ok"]
    assert "xem video vui" in backend.snapshot()["stt_filter"]["patterns"]
    assert run({"type": "stt_pattern_remove", "pattern": "xem video vui"})["ok"]
    assert not run({"type": "stt_pattern_remove", "pattern": "xem video vui"})["ok"]
    assert run({"type": "stt_filter", "enabled": False})["ok"]
    assert backend.snapshot()["stt_filter"]["enabled"] is False
    assert run({"type": "stt_patterns_reset"})["ok"]
    assert backend.snapshot()["stt_filter"]["patterns"] == list(DEFAULT_PATTERNS)
    assert run({"type": "vad", "min_speech_ms": 400, "save": False})["ok"]
    snap = backend.snapshot()["vad"]
    assert snap["min_speech_ms"] == 400 and snap["backend"] == "scripted"
    assert changes  # Pages are told
    json.dumps(backend.snapshot())  # The snapshot is JSON


def test_ignored_sentence_shows_in_chat_with_its_own_role():
    state = AppState()
    state.add_chat("ignored", "La La School", note="La La School")
    (msg,) = state.chat_since(0)
    assert msg["role"] == "ignored" and msg["note"] == "La La School"
