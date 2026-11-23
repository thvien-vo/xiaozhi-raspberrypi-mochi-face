"""Audio plugin.

Handles audio capture, encoding, playback, and transmission.
AudioCodec is published via Events.AUDIO_CODEC_CHANGED without coupling directly to MusicPlayer.
"""

import asyncio
import os
import time
from typing import TYPE_CHECKING

from src.audio_codecs.audio_codec import AudioCodec
from src.logging import get_logger
from src.plugins.base import Plugin

if TYPE_CHECKING:
    from src.bootstrap.protocols import PluginCommands, PluginContext

logger = get_logger()

MAX_CONCURRENT_AUDIO_SENDS = 4
AUDIO_RETRY_INTERVAL_S = 3.0


class AudioPlugin(Plugin):
    name = "audio"
    priority = 10  # Highest priority; other plugins depend on audio_codec

    def __init__(self) -> None:
        super().__init__()
        self.codec: AudioCodec | None = None
        self._send_sem = asyncio.Semaphore(MAX_CONCURRENT_AUDIO_SENDS)
        self._in_silence_period = False
        self._retry_task = None

    async def setup(self, ctx: "PluginContext", cmd: "PluginCommands") -> None:
        await super().setup(ctx, cmd)

        if os.getenv("XIAOZHI_DISABLE_AUDIO") == "1":
            logger.warning("XIAOZHI_DISABLE_AUDIO=1, running audio plugin in disabled mode")
            return

        retry_s = self._init_retry_s(ctx)
        try:
            await self._open_codec(ctx)
        except Exception as e:
            self.codec = None
            if retry_s > 0:
                # Kiosk: keep the app (face, vision) up and keep trying for the audio device
                logger.warning(
                    f"AudioPlugin: audio device not ready ({e}); retrying for up to {retry_s:.0f}s"
                )
                self._retry_task = cmd.spawn(self._retry_open(ctx, retry_s), name="audio:init-retry")
                return
            logger.error(f"Failed to initialize audio plugin: {e}", exc_info=True)
            self.mark_failed()
            raise

    @staticmethod
    def _init_retry_s(ctx) -> float:
        try:
            return float(ctx.get_config().get_config("AUDIO_DEVICES.init_retry_s", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    async def _open_codec(self, ctx) -> None:
        codec = AudioCodec()
        await codec.initialize()
        codec.set_encoded_callback(self._on_encoded_audio)
        self.codec = codec
        # Master / voice / music levels (web panel, voice commands) act on this codec's mixer
        from src.audio_codecs.levels import get_audio_levels

        get_audio_levels(ctx.get_config()).attach(codec)

        from src.core.event_bus import Events

        ctx.event_bus.on(Events.CONFIG_CHANGED, self._on_config_changed)
        ctx.event_bus.on(Events.AUDIO_DEVICES_REFRESH_REQUEST, self._on_devices_refresh_request)
        # Codec is published in start(): MusicPlayer subscribes to EventBus in McpPlugin.setup

    async def _retry_open(self, ctx, window_s: float) -> None:
        from src.utils.audio_utils import refresh_portaudio_devices

        deadline = time.monotonic() + window_s
        attempt = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(AUDIO_RETRY_INTERVAL_S)
            attempt += 1
            try:
                refresh_portaudio_devices(reinitialize=True)
                await self._open_codec(ctx)
            except Exception as e:
                logger.warning(f"AudioPlugin: waiting for audio device (attempt {attempt}): {e}")
                self.codec = None
                continue
            logger.info(f"AudioPlugin: audio device opened after {attempt} retries")
            await self._publish_audio_codec(self.codec)
            return
        logger.error(f"AudioPlugin: no usable audio device after {window_s:.0f}s, giving up")

    @property
    def audio_ready(self) -> bool:
        """True once the codec exists and its streams are running."""
        return self.codec is not None

    async def start(self) -> None:
        await super().start()
        if self.codec and not self.failed:
            await self._publish_audio_codec(self.codec)

    async def _publish_audio_codec(self, codec) -> None:
        """Publish AudioCodec instance or None to subscribers (such as MusicPlayer)."""
        if not self._ctx or not self._ctx.event_bus:
            logger.warning(
                "Cannot publish AUDIO_CODEC_CHANGED: PluginContext / EventBus not ready"
            )
            return
        from src.core.event_bus import Events

        try:
            await self._ctx.event_bus.emit(Events.AUDIO_CODEC_CHANGED, codec)
        except Exception as e:
            logger.warning(f"Failed to publish AUDIO_CODEC_CHANGED: {e}", exc_info=True)

    async def _on_config_changed(self, data=None):
        """Reload audio devices on configuration change (including PortAudio re-enumeration)."""
        if self.codec:
            logger.info("AudioPlugin: received config change event, reloading audio devices")
            await self._publish_audio_codec(None)
            if await self.codec.reload_devices(reenumerate=True):
                await self._publish_audio_codec(self.codec)

    async def _on_devices_refresh_request(self, data=None):
        """Settings page requested device list refresh: stop streams -> re-enumerate -> reopen streams.

        Payload may be an asyncio.Future; when complete, set_result(list_audio_devices result).
        """
        from src.utils.audio_utils import list_audio_devices, refresh_portaudio_devices

        future = data if isinstance(data, asyncio.Future) else None
        result = {"input": [], "output": []}
        try:
            if self.codec:
                await self._publish_audio_codec(None)
                # Must stop streams before safely calling _terminate on PortAudio
                self.codec.stop_streams_for_enumeration()
                refresh_portaudio_devices(reinitialize=True)
                result = list_audio_devices(include_virtual=True)
                # Reopen streams with current config (name match may point to a new index)
                ok = await self.codec.reload_devices(reenumerate=False)
                if not ok:
                    logger.error("AudioPlugin: failed to reopen audio streams after device refresh")
                else:
                    await self._publish_audio_codec(self.codec)
            else:
                # Without codec (audio disabled), still enumerate on a best-effort basis for settings UI
                refresh_portaudio_devices(reinitialize=True)
                result = list_audio_devices(include_virtual=True)
            logger.info(
                "AudioPlugin: device refresh completed "
                f"in={len(result.get('input', []))} out={len(result.get('output', []))}"
            )
        except Exception as e:
            logger.error(f"AudioPlugin: device refresh failed: {e}", exc_info=True)
        finally:
            if future is not None and not future.done():
                future.set_result(result)

    async def on_device_state_changed(self, state):
        """
        Handle device state changes.
        """
        if not self.codec:
            return

        from src.constants.constants import DeviceState

        # Keep music low for the whole reply, not only while speech samples are queued
        if hasattr(self.codec, "set_music_ducked"):
            self.codec.set_music_ducked(state == DeviceState.SPEAKING)

        if state == DeviceState.LISTENING:
            self._in_silence_period = True
            try:
                await asyncio.sleep(0.2)
            finally:
                self._in_silence_period = False

    async def on_incoming_json(self, message) -> None:
        """
        Handle TTS events.
        """
        if not isinstance(message, dict):
            return

        try:
            if message.get("type") == "tts":
                state = message.get("state")
                if state == "start":
                    if self._music_parallel_enabled():
                        logger.debug("TTS started (parallel mode): music continues with ducking")
                    else:
                        await self._pause_music_for_tts()
                elif state == "stop":
                    # Also send resume in parallel mode as a fallback if music started during TTS was paused
                    await self._resume_music_after_tts()
        except Exception as e:
            logger.error(f"Failed to handle TTS event: {e}", exc_info=True)

    def _music_parallel_enabled(self) -> bool:
        """Determine parallel playback: music keeps playing, ducked, while the assistant speaks."""
        try:
            from src.mcp.tools.music.config import plays_through_speech

            return plays_through_speech(self._ctx.get_config(), self.codec)
        except Exception:
            return False

    async def on_incoming_audio(self, data: bytes) -> None:
        """
        Receive and play audio data.
        """
        if self.codec:
            try:
                await self.codec.write_audio(data)
            except Exception as e:
                logger.debug(f"Failed to write audio data: {e}")

    async def _pause_music_for_tts(self):
        """Pause music when TTS starts (separate TTS/music queues mix without dropping frames; remaining music fades out naturally)."""
        try:
            from src.core.event_bus import Events
            from src.mcp.tools.music.events import MusicControlRequest

            logger.info("TTS started, sending music pause request")
            await self._ctx.event_bus.emit(
                Events.MUSIC_PAUSE_REQUEST, MusicControlRequest(source="tts")
            )
        except Exception as e:
            logger.warning(f"Failed to send music pause request: {e}", exc_info=True)

    async def _resume_music_after_tts(self):
        """Resume music after TTS finishes (fallback only in parallel mode, usually no actual pause occurred)."""
        try:
            from src.core.event_bus import Events
            from src.mcp.tools.music.events import MusicControlRequest

            log = logger.debug if self._music_parallel_enabled() else logger.info
            log("TTS playback finished, sending music resume request")
            await self._ctx.event_bus.emit(
                Events.MUSIC_RESUME_REQUEST, MusicControlRequest(source="tts")
            )
        except Exception as e:
            logger.error(f"Failed to send music resume request: {e}", exc_info=True)

    def register_resources(self, pool) -> None:
        codec = self.codec
        if codec:

            async def _cleanup():
                """Full audio codec cleanup: notify subscribers to clear codec first, then close."""
                import gc

                try:
                    # Music stops itself upon receiving None; full detach is handled by mcp/container
                    await self._publish_audio_codec(None)
                except Exception as e:
                    logger.debug(f"Failed to publish codec clear event: {e}", exc_info=True)
                gc.collect()
                from src.audio_codecs.levels import get_audio_levels

                get_audio_levels().attach(None)
                await codec.close()

            pool.register("audio.codec", _cleanup)

    def _on_encoded_audio(self, encoded_data: bytes) -> None:
        """
        Audio encoding callback (invoked from the audio thread).
        """
        try:
            if not self._cmd:
                return
            self._cmd.schedule_command_nowait(self._send_audio_async, encoded_data)
        except Exception as e:
            logger.error(f"Failed to schedule audio send: {e}", exc_info=True)

    async def _send_audio_async(self, encoded_data: bytes) -> None:
        """
        Send audio data asynchronously.
        """
        async with self._send_sem:
            try:
                if not self._ctx.is_audio_channel_opened():
                    return
                if self._should_send_microphone_audio():
                    await self._cmd.send_audio(encoded_data)
            except Exception as e:
                logger.error(f"Failed to send audio data: {e}", exc_info=True)

    def _should_send_microphone_audio(self) -> bool:
        """
        Determine whether microphone audio should be sent.
        """
        try:
            if self._in_silence_period:
                return False
            return self._ctx.should_capture_audio()
        except Exception as e:
            logger.warning(
                f"Failed to check if microphone audio should be sent, defaulting to False: {e}",
                exc_info=True,
            )
            return False
