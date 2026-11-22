"""aiohttp server for the web panel, on its own thread and event loop.

Running apart from the app's qasync loop means a slow browser, a JPEG encode or a burst
of commands can never stall the face animation or the audio. Commands reach the robot
through RobotBackend (thread-safe); state goes out to every browser about 20 times a
second, and only when it changed.

Routes: /  page, /login, /ws  WebSocket (JSON; binary messages are face snapshots),
/camera.mjpg  MJPEG stream, /api/state  snapshot as JSON, /face_presets.js  the robot
face's eye shapes (qml/face/FacePresets.js as an ES module, for the page's fallback
drawing), /static/*  web_assets (three.js, model, page code), /api/music/*  the music
library, queue, covers and uploads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import json
import mimetypes
import secrets
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from src.logging import get_logger
from src.music.library import LibraryError
from src.web_panel.backend import RobotBackend
from src.web_panel.commands import CommandError, RateLimiter, parse_command
from src.web_panel.settings import WebSettings

logger = get_logger()

COOKIE = "xz_panel"
BROADCAST_INTERVAL_S = 0.05  # At most 20 state messages per second
KEEPALIVE_S = 5.0  # Resend state this often even without changes (lets pages see they are live)
MJPEG_BOUNDARY = "frame"
UPLOAD_HEADER = "X-Xiaozhi-Upload"  # Only same-origin scripts can set it without a preflight
UPLOAD_BLOCK = 512 * 1024
UPLOAD_STALL_S = 30.0
MAX_PARALLEL_UPLOADS = 3
MUSIC_RESULT_TIMEOUT_S = 20.0
FACE_PRESETS = Path(__file__).resolve().parents[1] / "ui" / "gui" / "qml" / "face" / "FacePresets.js"


def presets_module(source: str) -> str:
    """FacePresets.js is a QML JavaScript library: drop the pragma, export the API."""
    lines = [line for line in source.splitlines() if not line.strip().startswith(".pragma")]
    return "\n".join(lines) + "\nexport { EMOTIONS, activeName, compose };\n"

# Windows maps .js to text/plain in some registries; modules then refuse to load
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("text/css", ".css")


def ensure_certificate(directory: Path) -> tuple[Path, Path] | None:
    """Self-signed certificate for HTTPS, made once with openssl. None if impossible."""
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "cert.pem", directory / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    host = socket.gethostname().split(".")[0] or "robot"
    names = [f"DNS:{host}.local", f"DNS:{host}", "DNS:localhost", "IP:127.0.0.1"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            names.append(f"IP:{s.getsockname()[0]}")
    except OSError:
        pass
    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650",
        "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={host}.local",
        "-addext", "subjectAltName=" + ",".join(names),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Web panel: cannot create an HTTPS certificate with openssl: %s", e)
        return None
    try:
        key.chmod(0o600)
    except OSError:
        pass
    logger.info("Web panel: created a self-signed certificate in %s", directory)
    return cert, key


def same_origin(request: web.Request) -> bool:
    """Reject WebSocket/API use from other sites' pages (cross-site WebSocket hijacking)."""
    origin = request.headers.get("Origin")
    if not origin:
        return True  # Not a browser page (curl, tests)
    host = request.headers.get("Host", "")
    return origin.split("://", 1)[-1].rstrip("/") == host


class WebPanelServer:
    def __init__(
        self,
        backend: RobotBackend,
        settings: WebSettings,
        static_dir: Path,
        cert_dir: Path | None = None,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.static_dir = Path(static_dir)
        self.cert_dir = cert_dir
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._started = threading.Event()
        self._sessions: set[str] = set()
        self._sockets: set[web.WebSocketResponse] = set()
        self._changed: asyncio.Event | None = None
        self._presets_js: str | None = None
        self._uploads = 0
        self._reply_tasks: set[asyncio.Task] = set()
        self._face_tasks: dict[web.WebSocketResponse, asyncio.Task] = {}
        self.urls: list[str] = []
        self.https_active = False
        self.start_error: str | None = None
        backend.add_listener(self._on_backend_change)

    # ----- Lifecycle (called from the app thread) -----

    def start(self, timeout: float = 10.0) -> bool:
        if self._thread is not None:
            return True
        self._thread = threading.Thread(target=self._thread_main, name="web-panel", daemon=True)
        self._thread.start()
        self._started.wait(timeout)
        return self.start_error is None and self._started.is_set()

    def stop(self, timeout: float = 5.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout)
        except Exception as e:
            logger.debug("Web panel shutdown: %s", e)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        self._thread = None
        logger.info("Web panel stopped")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._startup())
        except Exception as e:
            self.start_error = str(e)
            logger.error("Web panel failed to start: %s", e)
            self._started.set()
            loop.close()
            return
        self._started.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _startup(self) -> None:
        self._changed = asyncio.Event()
        app = self.make_app()
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        s = self.settings
        await web.TCPSite(self._runner, s.host, s.port).start()
        self.urls.append(f"http://{socket.gethostname()}.local:{s.port}")
        if s.https_enabled and self.cert_dir is not None:
            pair = await asyncio.get_running_loop().run_in_executor(None, ensure_certificate, self.cert_dir)
            if pair is not None:
                ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                ctx.load_cert_chain(str(pair[0]), str(pair[1]))
                await web.TCPSite(self._runner, s.host, s.https_port, ssl_context=ctx).start()
                self.https_active = True
                self.urls.append(f"https://{socket.gethostname()}.local:{s.https_port}")
        self._broadcast_task = asyncio.get_running_loop().create_task(self._broadcaster())
        logger.info("Web panel listening on %s (password %s)", ", ".join(self.urls), "on" if s.password else "off")

    async def _shutdown(self) -> None:
        task = getattr(self, "_broadcast_task", None)
        if task is not None:
            task.cancel()
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    def _on_backend_change(self) -> None:
        loop, event = self._loop, self._changed
        if loop is not None and event is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

    # ----- App -----

    def make_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/", self.index)
        app.router.add_get("/login", self.login_page)
        app.router.add_post("/login", self.login)
        app.router.add_get("/logout", self.logout)
        app.router.add_get("/ws", self.websocket)
        app.router.add_get("/camera.mjpg", self.camera)
        app.router.add_get("/api/state", self.api_state)
        app.router.add_get("/healthz", self.health)
        app.router.add_get("/face_presets.js", self.face_presets)
        app.router.add_get("/api/music/library", self.music_library)
        app.router.add_get("/api/music/queue", self.music_queue)
        app.router.add_get("/api/music/cover/{track_id}", self.music_cover)
        app.router.add_post("/api/music/upload", self.music_upload)
        app.router.add_static("/static", self.static_dir, show_index=False, append_version=False)
        return app

    def authorized(self, request: web.Request) -> bool:
        if not self.settings.password:
            return True
        token = request.cookies.get(COOKIE, "")
        return any(hmac.compare_digest(token, t) for t in self._sessions)

    async def index(self, request: web.Request) -> web.StreamResponse:
        if not self.authorized(request):
            raise web.HTTPFound("/login")
        return web.FileResponse(self.static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    async def login_page(self, request: web.Request) -> web.StreamResponse:
        if not self.settings.password:
            raise web.HTTPFound("/")
        return web.FileResponse(self.static_dir / "login.html", headers={"Cache-Control": "no-cache"})

    async def login(self, request: web.Request) -> web.StreamResponse:
        form = await request.post()
        given = str(form.get("password", ""))
        if self.settings.password and hmac.compare_digest(given.encode(), self.settings.password.encode()):
            token = secrets.token_urlsafe(24)
            self._sessions.add(token)
            response = web.HTTPFound("/")
            response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=30 * 86400)
            raise response
        await asyncio.sleep(1.0)  # Slow down guessing
        raise web.HTTPFound("/login?error=1")

    async def logout(self, request: web.Request) -> web.StreamResponse:
        self._sessions.discard(request.cookies.get(COOKIE, ""))
        response = web.HTTPFound("/login" if self.settings.password else "/")
        response.del_cookie(COOKIE)
        raise response

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def face_presets(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        if self._presets_js is None:
            try:
                self._presets_js = presets_module(FACE_PRESETS.read_text(encoding="utf-8"))
            except OSError:
                raise web.HTTPNotFound() from None
        return web.Response(text=self._presets_js, content_type="text/javascript", headers={"Cache-Control": "no-cache"})

    async def api_state(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response(self.backend.snapshot())

    # ----- Music -----

    def _music(self, request: web.Request):
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        if self.backend.music is None:
            raise web.HTTPServiceUnavailable(text="no music")
        return self.backend.music

    async def music_library(self, request: web.Request) -> web.Response:
        data = await asyncio.get_running_loop().run_in_executor(None, self._music(request).library_json)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def music_queue(self, request: web.Request) -> web.Response:
        data = await asyncio.get_running_loop().run_in_executor(None, self._music(request).queue_json)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def music_cover(self, request: web.Request) -> web.Response:
        music = self._music(request)
        data = await asyncio.get_running_loop().run_in_executor(None, music.cover, request.match_info["track_id"])
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    async def music_upload(self, request: web.Request) -> web.Response:
        """One file as the raw request body, its name in ?name=. Checked before it shows up."""
        music = self._music(request)
        if not same_origin(request) or UPLOAD_HEADER not in request.headers:
            raise web.HTTPForbidden(text="cross-origin upload refused")

        def refuse(message: str, status: int = 400) -> web.Response:
            response = web.json_response({"ok": False, "message": message}, status=status)
            response.force_close()  # The rest of the body is not wanted
            return response

        if self._uploads >= MAX_PARALLEL_UPLOADS:
            return refuse("đang nhận nhiều file cùng lúc, thử lại sau", 429)
        self._uploads += 1
        loop = asyncio.get_running_loop()
        upload = None
        try:
            upload = await loop.run_in_executor(None, music.begin_upload, request.query.get("name", ""))
            if request.content_length is not None and request.content_length > upload.limit:
                raise LibraryError(upload.too_big)
            block = bytearray()
            while True:
                chunk = await asyncio.wait_for(request.content.readany(), UPLOAD_STALL_S)
                block += chunk
                if block and (len(block) >= UPLOAD_BLOCK or not chunk):
                    await loop.run_in_executor(None, upload.write, bytes(block))
                    block.clear()
                if not chunk and request.content.at_eof():
                    break
            track = await loop.run_in_executor(None, upload.commit)
            upload = None
            self._on_backend_change()
            return web.json_response({"ok": True, "message": f"Đã thêm '{track.display}'", "track": track.to_json()})
        except LibraryError as e:
            return refuse(str(e))
        except asyncio.TimeoutError:
            return refuse("tải lên bị ngắt quãng", 408)
        except OSError as e:
            logger.warning("Music upload failed: %s", e)
            return refuse(f"không ghi được file: {e.strerror or e}", 500)
        finally:
            self._uploads -= 1
            if upload is not None:  # Refused, or the page went away
                upload.abort()

    # ----- WebSocket -----

    def hello(self) -> dict:
        s = self.settings
        return {
            "type": "hello",
            "https_port": s.https_port if self.https_active else None,
            "camera_fps": s.camera_stream_fps,
            "password": bool(s.password),
            "face": self.backend.face_constants(),
        }

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        if not same_origin(request):
            raise web.HTTPForbidden(text="cross-origin WebSocket refused")
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=8192)
        await ws.prepare(request)
        self._sockets.add(ws)
        limiter = RateLimiter(self.settings.max_commands_per_s)
        peer = request.remote
        logger.info("Web panel: client connected from %s (%d open)", peer, len(self._sockets))
        try:
            await ws.send_json(self.hello())
            await ws.send_str(json.dumps(self.backend.snapshot(), ensure_ascii=False))
            history = self.backend.app.chat_since(0)
            if history:
                await ws.send_json({"type": "chat", "messages": history})
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type == WSMsgType.ERROR:
                        break
                    continue
                await self._handle_message(ws, msg.data, limiter)
        finally:
            self._sockets.discard(ws)
            self._face_subscribe(ws, False)
            # A closed page must not keep the head claimed
            self.backend.arbiter.release("web")
            logger.info("Web panel: client %s left (%d open)", peer, len(self._sockets))
        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, data: str, limiter: RateLimiter) -> None:
        request_id = None
        try:
            try:
                request_id = json.loads(data).get("id") if len(data) < 8192 else None
            except (ValueError, AttributeError):
                request_id = None
            if not isinstance(request_id, (int, str)) or (isinstance(request_id, str) and len(request_id) > 40):
                request_id = None
            if not limiter.allow():
                result = {"ok": False, "message": "gửi lệnh quá nhanh", "rate_limited": True}
            else:
                emotions = tuple(self.backend.app.emotions)
                cmd = parse_command(data, self.backend.pan_limit, self.backend.tilt_limit, emotions)
                if cmd.type == "face_stream":
                    result = self._face_subscribe(ws, cmd.args["enabled"])
                else:
                    result = self.backend.handle(cmd)
        except CommandError as e:
            result = {"ok": False, "message": str(e)}
        except Exception as e:
            logger.error("Web panel command failed", exc_info=True)
            result = {"ok": False, "message": f"lỗi: {e}"}
        if isinstance(result, concurrent.futures.Future):
            # Music runs on the app's loop: answer when done, keep reading messages
            task = asyncio.get_running_loop().create_task(self._reply_later(ws, request_id, result))
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        await self._reply(ws, request_id, result)

    async def _reply_later(self, ws: web.WebSocketResponse, request_id, future: concurrent.futures.Future) -> None:
        try:
            result = await asyncio.wait_for(asyncio.wrap_future(future), MUSIC_RESULT_TIMEOUT_S)
        except asyncio.TimeoutError:
            result = {"ok": False, "message": "robot chưa trả lời, thử lại"}
        except Exception as e:
            logger.error("Web panel music command failed", exc_info=True)
            result = {"ok": False, "message": f"lỗi: {e}"}
        try:
            await self._reply(ws, request_id, result)
        except (ConnectionError, RuntimeError):
            pass

    @staticmethod
    async def _reply(ws: web.WebSocketResponse, request_id, result: dict) -> None:
        if request_id is not None or not result.get("ok", True):
            await ws.send_json({"type": "result", "id": request_id, **result})

    async def _broadcaster(self) -> None:
        last_text = None
        last_sent = 0.0
        chat_seq = self._chat_seq_now()
        while True:
            try:
                await asyncio.wait_for(self._changed.wait(), BROADCAST_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            self._changed.clear()
            if not self._sockets:
                last_text = None
                chat_seq = self._chat_seq_now()
                await asyncio.sleep(BROADCAST_INTERVAL_S)
                continue
            try:
                text = json.dumps(self.backend.snapshot(), ensure_ascii=False)
                new_chat = self.backend.app.chat_since(chat_seq)
            except Exception:
                logger.error("Web panel snapshot failed", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            now = time.monotonic()
            messages = []
            if text != last_text or now - last_sent >= KEEPALIVE_S:
                messages.append(text)
                last_text, last_sent = text, now
            if new_chat:
                chat_seq = new_chat[-1]["id"]
                messages.append(json.dumps({"type": "chat", "messages": new_chat}, ensure_ascii=False))
            for ws in list(self._sockets):
                for message in messages:
                    try:
                        await ws.send_str(message)
                    except (ConnectionError, RuntimeError):
                        self._sockets.discard(ws)
                        break
            await asyncio.sleep(BROADCAST_INTERVAL_S)

    # ----- Face snapshots (binary WebSocket messages) -----

    def _face_subscribe(self, ws: web.WebSocketResponse, enabled: bool) -> dict:
        hub = self.backend.face_frames
        task = self._face_tasks.get(ws)
        if enabled and task is None:
            if hub is None or not hub.available:
                return {"ok": False, "message": "không có hình mặt robot (cần chế độ GUI, FACE.STYLE=robot)", "silent": True}
            hub.add_viewer()
            self._face_tasks[ws] = asyncio.get_running_loop().create_task(self._face_sender(ws, hub))
        elif not enabled and task is not None:
            del self._face_tasks[ws]
            task.cancel()
            if hub is not None:
                hub.remove_viewer()
        return {"ok": True}

    async def _face_sender(self, ws: web.WebSocketResponse, hub) -> None:
        """Send each new snapshot; a slow page just skips frames (only the newest goes)."""
        last = -1
        interval = 1.0 / hub.fps
        try:
            while not ws.closed:
                jpeg, seq = hub.latest()
                if jpeg is not None and seq != last:
                    last = seq
                    await ws.send_bytes(jpeg)
                await asyncio.sleep(interval / 2)
        except (ConnectionError, RuntimeError, asyncio.CancelledError):
            pass

    def _chat_seq_now(self) -> int:
        return self.backend.app.snapshot()["chat_seq"]

    # ----- Camera -----

    async def camera(self, request: web.Request) -> web.StreamResponse:
        if not self.authorized(request):
            raise web.HTTPUnauthorized()
        feed = self.backend.camera
        if feed is None or not feed.available():
            raise web.HTTPServiceUnavailable(text="no camera")
        response = web.StreamResponse(
            headers={
                "Content-Type": f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
                "Cache-Control": "no-cache, no-store, private",
                "Pragma": "no-cache",
            }
        )
        await response.prepare(request)
        loop = asyncio.get_running_loop()
        feed.add_viewer()
        seq = 0
        try:
            while request.transport is not None and not request.transport.is_closing():
                got = await loop.run_in_executor(None, feed.wait_jpeg, seq, 2.0)
                if got is None:
                    continue
                jpeg, seq = got
                head = (
                    f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                ).encode()
                await response.write(head + jpeg + b"\r\n")
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            feed.remove_viewer()
        return response
