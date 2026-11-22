"""Robot music: library, fuzzy search, uploads, queue, beat, dance, player, MCP, web panel."""

import asyncio
import io
import json
import os
import socket
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from src.music.beat import estimate
from src.music.dance import MAX_SHARE, MusicDancer, dance_bpm
from src.music.library import LibraryError, MusicLibrary, magic_matches, safe_filename, valid_track_id
from src.music.matching import normalize, score, strip_request, wants_anything
from src.music.play_queue import PlayQueue
from src.servo.motion import SOURCE_MUSIC, SOURCE_VOICE, SOURCE_WEB, ControlArbiter
from src.servo.settings import ServoSettings
from src.web_panel.commands import CommandError, parse_command


def wav_bytes(seconds=1.0, rate=8000, freq=440.0) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.sin(2 * np.pi * freq * t) * 9000).astype("<i2").tobytes())
    return out.getvalue()


def add(library, name, seconds=1.0):
    up = library.begin_upload(name)
    up.write(wav_bytes(seconds))
    return up.commit()


@pytest.fixture
def library(tmp_path):
    return MusicLibrary(tmp_path / "music", min_free_mb=0)


SONGS = ("Sơn Tùng - Chạy Ngay Đi.wav", "Sơn Tùng - Nơi Này Có Anh.wav", "Hà Trần - Đêm Nằm Mơ Phố.wav", "Shape of You.wav")


# ----- Matching -----

def test_normalize_ignores_case_and_marks():
    assert normalize("  ĐÊM Nằm, Mơ Phố!! ") == "dem nam mo pho"


def test_strip_request_removes_the_words_around_a_name():
    assert strip_request("mở bài Chạy ngay đi cho tôi nghe") == "chay ngay"  # "đi" is also filler
    assert strip_request("bật bài hát Nơi này có anh đi") == "noi nay co anh"
    assert strip_request("bật nhạc đi") == ""


def test_wants_anything():
    for q in ("", "nhạc", "bật nhạc đi", "bài nào cũng được", "một bài bất kỳ"):
        assert wants_anything(q), q
    assert not wants_anything("bài Nơi này có anh")


@pytest.mark.parametrize("query", ["noi nay co anh", "NƠI NÀY CÓ ANH", "bài nơi này có em anh", "nơi lày có anh"])
def test_misheard_names_score_high(query):
    assert score(query, "Nơi Này Có Anh") >= 0.66
    assert score(query, "Shape of You") < 0.5


# ----- Library -----

def test_library_reads_names_and_searches_without_marks(library):
    for name in SONGS:
        add(library, name)
    assert len(library.tracks()) == 4
    assert library.search("chay ngay di")[0][1].title == "Chạy Ngay Đi"
    assert library.search("shape of you")[0][1].title == "Shape of You"
    assert library.search("dem nam mo pho ha tran")[0][1].artist == "Hà Trần"
    assert library.search("bài không tồn tại xyz") == []
    assert {t.title for t in library.by_artist("nhạc của sơn tùng")} == {"Chạy Ngay Đi", "Nơi Này Có Anh"}


def test_library_reads_tags_and_cover(library):
    from mutagen.id3 import APIC, TALB, TIT2, TPE1
    from mutagen.wave import WAVE
    from PIL import Image

    track = add(library, "x.wav")
    png = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(png, "PNG")
    audio = WAVE(track.path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Bài <b>Thử</b>"))
    audio.tags.add(TPE1(encoding=3, text="Ca Sĩ"))
    audio.tags.add(TALB(encoding=3, text="Album"))
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, data=png.getvalue()))
    audio.save()
    library.scan(force=True)
    t = library.get(track.id)
    assert (t.title, t.artist, t.album, t.has_cover) == ("Bài <b>Thử</b>", "Ca Sĩ", "Album", True)
    assert 0.9 < t.duration < 1.1
    assert library.cover(t.id)[:2] == b"\xff\xd8"  # Re-encoded as JPEG
    assert library.cover("0" * 16) is None


def test_delete_and_revision(library):
    track = add(library, "a.wav")
    rev = library.revision
    assert library.delete(track.id).title == "a"
    assert library.revision > rev and not track.path.exists()
    assert library.delete(track.id) is None


def test_ids_from_outside_never_become_paths(library, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(wav_bytes())
    add(library, "a.wav")
    for bad in ("../secret.wav", "..%2fsecret.wav", "/etc/passwd", "", None, 5, "G" * 16, "0" * 17, "0" * 16 + "\n"):
        assert not valid_track_id(bad)
        assert library.get(bad) is None and library.delete(bad) is None and library.cover(bad) is None
    assert outside.exists()


def test_symlinks_are_ignored(library, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(wav_bytes())
    library.root.mkdir(parents=True)
    try:
        os.symlink(outside, library.root / "link.wav")
        os.symlink(tmp_path, library.root / "dir", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert library.tracks() == []


# ----- Uploads -----

@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("../../etc/Bài hát.MP3", "Bài hát.mp3"),
        ("C:\\x\\y\\song.wav", "song.wav"),
        ("a\x00b\n<c>|?.flac", "abc.flac"),
        (".hidden.ogg", "hidden.ogg"),
    ],
)
def test_safe_filename(name, safe):
    assert safe_filename(name) == safe


@pytest.mark.parametrize("name", ["", "x.exe", "x.mp3.sh", "....mp3", "/", "con.mp3", ".mp3", "x.html"])
def test_bad_filenames_are_refused(name):
    with pytest.raises(LibraryError):
        safe_filename(name)


def test_long_filename_is_cut():
    assert len(safe_filename("ă" * 500 + ".mp3").encode()) < 200


@pytest.mark.parametrize("data", [b"", b"<html><script>", b"RIFF1234WAVE" + b"\0" * 50, b"\x7fELF" + b"\0" * 99, b"OggS" + b"x" * 99])
def test_upload_must_be_audio(library, data):
    up = library.begin_upload("x.wav")
    up.write(data) if data else None
    with pytest.raises(LibraryError):
        up.commit()
    assert library.tracks() == [] and list(library.work_dir.iterdir()) == []


def test_upload_content_must_match_the_extension(library):
    up = library.begin_upload("x.mp3")
    up.write(wav_bytes())
    with pytest.raises(LibraryError):
        up.commit()
    assert magic_matches(b"ID3\x04", ".mp3") and not magic_matches(b"RIFF", ".mp3")


def test_upload_size_limits(tmp_path, monkeypatch):
    lib = MusicLibrary(tmp_path / "m", max_upload_mb=1, max_total_mb=1, min_free_mb=0)
    up = lib.begin_upload("big.wav")
    with pytest.raises(LibraryError, match="quá lớn"):
        for _ in range(3):
            up.write(b"\0" * 600_000)
    up.abort()
    assert list(lib.work_dir.iterdir()) == []
    monkeypatch.setattr(lib, "usage", lambda: 2**30)
    with pytest.raises(LibraryError, match="hết chỗ"):
        lib.begin_upload("a.wav")


def test_same_name_does_not_overwrite(library):
    a, b = add(library, "a.wav"), add(library, "a.wav", 2.0)
    assert a.id != b.id and a.path.exists() and b.path.name == "a (2).wav"


def test_leftover_uploads_are_removed(library):
    up = library.begin_upload("a.wav")
    up.write(b"123")
    up._file.close()
    library.clean_work_dir()
    assert list(library.work_dir.glob("upload-*")) == []
    assert library.tracks() == []


# ----- Queue -----

def test_queue_order_repeat_and_previous():
    q = PlayQueue()
    assert q.load(["a", "b", "c"], "b").track_id == "b"
    assert q.next().track_id == "c"
    assert q.next() is None
    q.set_repeat("all")
    assert q.next().track_id == "a"
    assert q.previous().track_id == "c"
    q.set_repeat("one")
    assert q.next(auto=True).track_id == "c"  # Ended by itself: again
    assert q.next().track_id == "a"  # Asked for: moves on


def test_queue_shuffle_keeps_current_and_restores_order():
    q = PlayQueue()
    ids = [str(i) for i in range(30)]
    q.load(ids, "5")
    q.set_shuffle(True)
    assert q.current.track_id == "5" and sorted(it.track_id for it in q.items) == sorted(ids)
    assert [it.track_id for it in q.items] != ids
    q.set_shuffle(False)
    assert [it.track_id for it in q.items] == ids and q.current.track_id == "5"


def test_queue_add_remove_select_clear():
    q = PlayQueue()
    q.load(["a", "b"], "a")
    n = q.add("n", play_next=True)
    assert [it.track_id for it in q.items] == ["a", "n", "b"]
    assert q.remove(n.uid) and not q.remove(n.uid)
    assert q.select(q.items[1].uid).track_id == "b" and q.select(9999) is None
    q.clear()
    assert [it.track_id for it in q.items] == ["b"]


# ----- Beat and dance -----

@pytest.mark.parametrize("bpm", [90, 120])
def test_beat_of_a_click_track(bpm):
    rate, seconds = 11025, 20
    t = np.arange(rate * seconds) / rate
    phase = (t - 0.2) % (60 / bpm)
    x = np.where(t >= 0.2, np.exp(-30 * phase) * np.sin(2 * np.pi * 900 * t), 0.0)
    r = estimate(x, rate)
    ratio = r["bpm"] / bpm
    assert min(abs(ratio - k) for k in (0.5, 1, 2)) < 0.03
    assert r["confidence"] > 0.2


def test_beat_of_silence_is_the_fallback():
    assert estimate(np.zeros(11025 * 5))["confidence"] == 0


def test_dance_bpm_is_followable():
    assert all(62 <= dance_bpm(b) <= 125 for b in (40, 60, 100, 128, 180, 200, float("nan"), 0))


class Servo:
    def __init__(self):
        self.settings = ServoSettings(enabled=True)
        self.angles = (0.0, 0.0)
        self.calls = []

    def set_angles(self, pan=None, tilt=None):
        self.angles = (pan, tilt)
        self.calls.append(self.angles)
        return self.angles


@pytest.mark.parametrize("base", [(0, 0), (90, 45), (-90, -45), (30, -20)])
def test_dance_stays_inside_the_limits(base):
    servo = Servo()
    s = servo.settings
    d = MusicDancer(servo, ControlArbiter(None, 1.0), lambda: None)
    for i in range(1500):
        pan, tilt = d.target(base, i * 0.05)
        assert abs(pan) <= s.pan_limit and abs(tilt) <= s.tilt_limit
        centre = max(-s.pan_limit * (1 - MAX_SHARE), min(s.pan_limit * (1 - MAX_SHARE), base[0]))
        assert abs(pan - centre) <= MAX_SHARE * s.pan_limit * 2


def test_dance_moves_only_with_music_and_returns():
    servo, playing = Servo(), [None]
    d = MusicDancer(servo, ControlArbiter(None, 1.0), lambda: playing[0], enabled=True)
    time.sleep(0.3)
    assert not servo.calls
    start = time.monotonic()
    d._playing = lambda: (time.monotonic() - start, 120.0, 0.0)
    time.sleep(0.9)
    assert d.dancing and len(servo.calls) > 5
    d._playing = lambda: None
    time.sleep(0.5)
    assert not d.dancing and servo.angles == (0.0, 0.0)
    d.shutdown()


def test_dance_yields_to_voice_and_web():
    arbiter = ControlArbiter(None, 1.0)
    for source in (SOURCE_WEB, SOURCE_VOICE):
        a = ControlArbiter(None, 1.0)
        assert a.request(source, hold=5)
        assert not a.request(SOURCE_MUSIC, hold=1)
    assert arbiter.request(SOURCE_MUSIC, hold=5) and arbiter.request(SOURCE_VOICE, hold=1)


# ----- Player (real decoder replaced) -----

class Codec:
    output_running = True
    output_device_name = "Test"
    music_backlog_samples = 0

    def __init__(self):
        self.gain = None
        self.cleared = 0
        self.written = 0

    def set_music_gain(self, gain):
        self.gain = gain

    async def clear_music_queue(self):
        self.cleared += 1

    async def write_pcm_direct(self, pcm):
        self.written += len(pcm)
        await asyncio.sleep(len(pcm) / 24000)


@pytest.fixture
def make_player(library, monkeypatch):
    from src.mcp.tools.music import music_player as module

    saved = {}

    class Config:
        online = False

        def get_config(self, key, default=None):
            return self.online if key == "MUSIC.ONLINE_ENABLED" else default

        def update_config(self, key, value):
            saved[key] = value
            return True

    monkeypatch.setattr("src.utils.config_manager.get_config", lambda: Config())
    monkeypatch.setattr(module, "analyze_file", lambda path: {"bpm": 120.0, "offset": 0.0, "confidence": 0.9})
    monkeypatch.setattr(module, "BEAT_DELAY_S", 0)
    started = []

    async def build(codec="default"):
        p = module.MusicPlayer()
        p.library = library
        p.saved, p.started = saved, started
        await p._bus.on_audio_codec_changed(Codec() if codec == "default" else codec)
        p.load_settings()

        async def start_playback(source, position=0.0):
            started.append(str(source))
            p._engine.is_playing, p._engine.paused = True, False
            return not str(source).endswith("broken.wav")

        async def stop():
            p._engine.is_playing = False
            return {"status": "success", "message": "stopped"}

        monkeypatch.setattr(p._engine, "start_playback", start_playback)
        monkeypatch.setattr(p._engine, "stop", stop)
        return p

    return build


def test_voice_request_plays_from_the_library_first(library, make_player):
    for name in SONGS:
        add(library, name)

    async def run():
        p = await make_player()
        r = await p.search_and_play("nơi này có anh")
        assert r["status"] == "success" and "Nơi Này Có Anh" in r["message"]
        assert p.started[-1].endswith("Nơi Này Có Anh.wav") and len(p.queue) == 4
        r = await p.search_and_play("nhạc của sơn tùng")
        assert r["status"] == "success" and len(p.queue) == 2
        r = await p.search_and_play("bật nhạc đi")
        assert r["status"] == "success" and len(p.queue) == 4
        await asyncio.sleep(0.1)
        assert p.snapshot()["state"] == "playing" and p.snapshot()["bpm"] == 120

    asyncio.run(run())


def test_unknown_song_is_never_reported_as_playing(library, make_player):
    async def run():
        p = await make_player()
        r = await p.search_and_play("Lạc Trôi")
        assert r["status"] == "error" and "CHƯA" in r["message"] and "trống" in r["message"]
        await asyncio.to_thread(add, library, SONGS[0])
        r = await p.search_and_play("Bản giao hưởng số chín")
        assert r["status"] == "error" and "CHƯA" in r["message"]
        assert p.started == [] and not p.is_playing

    asyncio.run(run())


def test_no_speaker_and_broken_file_give_a_clear_error(library, make_player):
    add(library, "broken.wav")

    async def run():
        p = await make_player(codec=None)
        r = await p.search_and_play("broken")
        assert r["status"] == "error" and "thiết bị âm thanh" in r["message"] and p.started == []
        assert p.snapshot()["audio"]["ok"] is False
        p = await make_player()
        r = await p.search_and_play("broken")
        assert r["status"] == "error" and not p.is_playing and "CHƯA" in r["message"]
        assert p.snapshot()["error"]

    asyncio.run(run())


def test_next_previous_and_automatic_advance(library, make_player):
    for name in ("a.wav", "b.wav", "c.wav"):
        add(library, name)

    async def run():
        p = await make_player()
        ids = {t.title: t.id for t in library.tracks()}
        assert (await p.play_track(ids["a"]))["status"] == "success"
        assert "b" in (await p.next_track())["message"]
        assert "a" in (await p.previous_track())["message"]
        p._engine.is_playing = False  # The song ends
        p.playback_finished(False)
        await asyncio.sleep(0.05)
        assert p.track_id == ids["b"] and p.is_playing
        # A finish that arrives after someone chose another song changes nothing
        token = p._play_token
        await p.play_track(ids["a"])
        await p._advance(token, False)
        assert p.track_id == ids["a"]
        await p.next_track()
        await p.next_track()
        assert (await p.next_track())["status"] == "error"

    asyncio.run(run())


def test_files_that_cannot_be_decoded_stop_after_a_few(library, make_player):
    for name in ("a.wav", "b.wav", "c.wav", "d.wav", "e.wav"):
        add(library, name)

    async def run():
        p = await make_player()
        p.set_modes(repeat="all")
        await p.play()
        for _ in range(10):
            if not p.is_playing:
                break
            p._engine.is_playing = False
            p.playback_finished(True)
            await asyncio.sleep(0.05)
        assert not p.is_playing and len(p.started) <= 4 and p.last_error

    asyncio.run(run())


def test_volume_is_applied_clamped_and_saved(make_player):
    async def run():
        p = await make_player()
        codec = p._engine.audio_codec
        assert codec.gain == pytest.approx(0.49)
        p.set_volume(50)
        assert codec.gain == pytest.approx(0.25) and p.saved["MUSIC.VOLUME"] == 50
        assert p.change_volume(-80)["volume"] == 0 and codec.gain == 0
        assert p.set_volume(400)["volume"] == 100
        other = Codec()
        await p._bus.on_audio_codec_changed(other)
        assert other.gain == 1.0

    asyncio.run(run())


def test_deleting_the_playing_track_stops_it(library, make_player):
    track = add(library, "a.wav")

    async def run():
        p = await make_player()
        await p.play_track(track.id)
        assert (await p.delete_track(track.id))["status"] == "success"
        assert not p.is_playing and len(p.queue) == 0 and p.snapshot()["track"] is None

    asyncio.run(run())


# ----- MCP tools -----

def test_music_tools(library, make_player):
    from src.mcp.mcp_server import McpServer
    from src.mcp.tools.music import register_music_tools

    for name in SONGS:
        add(library, name)

    async def run():
        p = await make_player()
        server = McpServer()
        register_music_tools(server.add_tool, p)
        tools = {t.name: t for t in server.tools}
        assert {f"music_player.{n}" for n in (
            "search_and_play", "pause", "resume", "stop", "seek", "next", "previous", "set_volume", "set_mode",
            "dance", "get_local_playlist", "get_status",
        )} <= set(tools)

        async def call(name, **args):
            tool = tools[f"music_player.{name}"]
            values = {prop.name: prop.default_value for prop in tool.properties.properties if prop.has_default_value}
            return await tool.callback({**values, **args})

        assert "Chạy Ngay Đi" in await call("search_and_play", song_name="chay ngay di")
        assert "CHƯA" in await call("search_and_play", song_name="zzzz qqqq wwww")
        assert "Đang phát" in await call("search_and_play")
        assert "85" in await call("set_volume", change=15)
        assert "30" in await call("set_volume", volume=30)
        assert "trộn bài bật" in await call("set_mode", shuffle="on")
        assert "repeat" in await call("set_mode", repeat="forever")
        assert "servo" in await call("dance")
        assert "4 bài" in await call("get_local_playlist")

    asyncio.run(run())


# ----- Web panel -----

def parse(obj):
    return parse_command(json.dumps(obj), 90, 45, ())


def test_music_commands_are_validated():
    tid = "0123456789abcdef"
    assert parse({"type": "music_play"}).args == {"track_id": None}
    assert parse({"type": "music_play", "track_id": tid}).args == {"track_id": tid}
    assert parse({"type": "music_volume", "volume": 50, "save": False}).args == {"volume": 50, "save": False}
    assert parse({"type": "music_mode", "repeat": "one"}).args == {"repeat": "one", "shuffle": None}
    for bad in (
        {"type": "music_play", "track_id": "../x"},
        {"type": "music_play", "track_id": 5},
        {"type": "music_delete"},
        {"type": "music_delete", "track_id": "0123456789ABCDEF"},
        {"type": "music_seek", "position": "1"},
        {"type": "music_volume"},
        {"type": "music_volume", "volume": True},
        {"type": "music_mode"},
        {"type": "music_mode", "repeat": "forever"},
        {"type": "music_mode", "shuffle": "yes"},
        {"type": "music_dequeue", "uid": -1},
        {"type": "music_dequeue", "uid": True},
        {"type": "music_queue_play", "uid": "1"},
        {"type": "music_dance"},
        {"type": "music_unknown"},
        {"type": "music_submit"},
        {"type": "music___init__"},
    ):
        with pytest.raises(CommandError):
            parse(bad)


def test_out_of_range_values_are_clamped():
    assert parse({"type": "music_volume", "volume": 900}).args["volume"] == 100
    assert parse({"type": "music_seek", "position": -5}).args["position"] == 0


def web_server(library, make_player, password=""):
    from src.utils.resource_finder import get_app_root
    from src.web_panel.backend import RobotBackend
    from src.web_panel.music import MusicControl
    from src.web_panel.server import WebPanelServer
    from src.web_panel.settings import WebSettings

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    player = asyncio.run_coroutine_threadsafe(make_player(), loop).result(5)
    backend = RobotBackend(None, None, music=MusicControl(player, lambda: loop))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = WebSettings(enabled=True, host="127.0.0.1", port=port, password=password)
    server = WebPanelServer(backend, settings, get_app_root() / "web_assets")
    assert server.start()
    return server, loop, f"http://127.0.0.1:{port}", player


UP = {"X-Xiaozhi-Upload": "1"}


def test_upload_play_and_sync_over_the_web(library, make_player):
    import aiohttp

    server, loop, base, player = web_server(library, make_player)

    async def run():
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{base}/api/music/upload", params={"name": "../../Bài Mới.wav"}, data=wav_bytes(), headers=UP) as r:
                body = await r.json()
                assert r.status == 200 and body["track"]["file"] == "Bài Mới.wav"
            tid = body["track"]["id"]
            async with http.get(f"{base}/api/music/library") as r:
                data = await r.json()
                assert [t["id"] for t in data["tracks"]] == [tid]
            for name, payload, headers, status in (
                ("x.exe", wav_bytes(), UP, 400),
                ("x.wav", b"<html>", UP, 400),
                ("x.mp3", wav_bytes(), UP, 400),
                ("x.wav", wav_bytes(), {}, 403),
                ("x.wav", wav_bytes(), {**UP, "Origin": "http://evil.example"}, 403),
            ):
                async with http.post(f"{base}/api/music/upload", params={"name": name}, data=payload, headers=headers) as r:
                    assert r.status == status, (name, status, r.status)
            for path in ("cover/../../config.json", "cover/%2e%2e%2fx", f"cover/{'0' * 16}", f"cover/{tid}"):
                async with http.get(f"{base}/api/music/{path}") as r:
                    assert r.status == 404
            async with http.ws_connect(f"{base}/ws") as ws:
                async def until(test):
                    for _ in range(60):
                        m = await asyncio.wait_for(ws.receive_json(), 5)
                        if test(m):
                            return m
                    raise AssertionError("not seen")

                await ws.send_json({"type": "music_play", "track_id": tid, "id": "1"})
                m = await until(lambda m: m.get("id") == "1" and m["type"] == "result")
                assert m["ok"] and "Bài Mới" in m["message"]
                m = await until(lambda m: m["type"] == "state" and m["music"]["state"] == "playing")
                assert m["music"]["track"]["id"] == tid
                # Started by voice instead: the page sees the volume change as well
                loop.call_soon_threadsafe(player.set_volume, 12)
                await until(lambda m: m["type"] == "state" and m["music"]["volume"] == 12)
                await ws.send_json({"type": "music_play", "track_id": "../../etc/passwd", "id": "2"})
                assert not (await until(lambda m: m.get("id") == "2"))["ok"]
                await ws.send_json({"type": "music_delete", "track_id": tid, "id": "3"})
                assert (await until(lambda m: m.get("id") == "3"))["ok"]
            async with http.get(f"{base}/api/music/library") as r:
                assert (await r.json())["tracks"] == []

    try:
        asyncio.run(run())
    finally:
        server.stop()
        loop.call_soon_threadsafe(loop.stop)
    assert list(library.work_dir.iterdir()) == [] or not list(library.work_dir.glob("upload-*"))


def test_too_big_uploads_and_missing_login_are_refused(library, make_player, monkeypatch):
    import aiohttp

    server, loop, base, _ = web_server(library, make_player, password="secret-pass")
    library.max_upload = 300_000

    async def run():
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            for path in ("library", "queue", f"cover/{'0' * 16}"):
                async with http.get(f"{base}/api/music/{path}", allow_redirects=False) as r:
                    assert r.status == 401
            async with http.post(f"{base}/api/music/upload", params={"name": "a.wav"}, data=wav_bytes(), headers=UP) as r:
                assert r.status == 401

    async def too_big():
        with pytest.raises(LibraryError):
            up = library.begin_upload("a.wav")
            try:
                up.write(b"\0" * 400_000)
            finally:
                up.abort()

    try:
        asyncio.run(run())
        asyncio.run(too_big())
    finally:
        server.stop()
        loop.call_soon_threadsafe(loop.stop)
    assert library.tracks() == []


def test_big_upload_over_http_stops_early(library, make_player):
    import aiohttp

    server, loop, base, _ = web_server(library, make_player)
    library.max_upload = 300_000

    async def chunks():
        for _ in range(40):
            yield b"\0" * 100_000

    async def run():
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{base}/api/music/upload", params={"name": "a.wav"}, data=wav_bytes(30), headers=UP) as r:
                assert r.status == 400 and "quá lớn" in (await r.json())["message"]
            try:  # No Content-Length: cut off while receiving
                async with http.post(f"{base}/api/music/upload", params={"name": "a.wav"}, data=chunks(), headers=UP) as r:
                    assert r.status == 400
            except aiohttp.ClientError:
                pass

    try:
        asyncio.run(run())
    finally:
        server.stop()
        loop.call_soon_threadsafe(loop.stop)
    assert library.tracks() == [] and not list(library.work_dir.glob("upload-*"))


# ----- Ducking -----

def test_music_gets_quiet_while_the_robot_speaks_and_comes_back():
    from src.audio_codecs import audio_codec as module

    codec = SimpleNamespace(_music_gain=1.0, _music_ducked=False, _music_level=1.0)
    apply = module.AudioCodec._apply_music_level
    one = np.ones(480, dtype=np.float32)
    quiet = apply(codec, one, True)
    assert quiet.max() == pytest.approx(module._MUSIC_DUCK_GAIN)  # At once
    levels = [float(apply(codec, one, False)[-1]) for _ in range(20)]
    assert levels == sorted(levels) and levels[0] < 1.0 and levels[-1] == pytest.approx(1.0)  # Ramped back

