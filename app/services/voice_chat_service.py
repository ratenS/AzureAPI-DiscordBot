from __future__ import annotations

import asyncio
import audioop
import base64
import io
from dataclasses import dataclass, field

import discord
import structlog
from discord.ext import voice_recv
from openai import AsyncOpenAI

from app.config import Settings
from app.models import ScopeRef

logger = structlog.get_logger(__name__)

DISCORD_SAMPLE_RATE = 48000
DISCORD_SAMPLE_WIDTH = 2
DISCORD_CHANNELS = 2


@dataclass(slots=True)
class VoiceSession:
    guild_id: int
    channel_id: int
    text_channel_id: int
    scope: ScopeRef
    voice_client: voice_recv.VoiceRecvClient
    sink: "AzureVoiceReceiveSink"
    audio_queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    playback_queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    connection_task: asyncio.Task[None] | None = None
    playback_task: asyncio.Task[None] | None = None
    last_transcript: str = ""
    last_response_transcript: str = ""
    status: str = "connecting"


class AzureVoiceReceiveSink(voice_recv.AudioSink):
    def __init__(self, service: "VoiceChatService", session: VoiceSession) -> None:
        super().__init__()
        self._service = service
        self._session = session

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.User | discord.Member | None, data: voice_recv.VoiceData) -> None:
        if user is None or getattr(user, "bot", False) or not data.pcm:
            return
        self._service.handle_voice_packet(self._session.guild_id, user.display_name, data.pcm)

    def cleanup(self) -> None:
        return None


class VoiceChatService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[int, VoiceSession] = {}

    async def join(
        self,
        *,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel | discord.StageChannel,
        text_channel: discord.TextChannel | discord.Thread,
        scope: ScopeRef,
    ) -> str:
        existing = self._sessions.get(guild.id)
        guild_voice_client = guild.voice_client
        logger.info(
            "voice_join_requested",
            guild_id=guild.id,
            channel_id=voice_channel.id,
            channel_type=type(voice_channel).__name__,
            text_channel_id=text_channel.id,
            has_existing_session=existing is not None,
            existing_session_channel_id=getattr(existing, "channel_id", None),
            guild_has_voice_client=guild_voice_client is not None,
            guild_voice_client_channel_id=getattr(getattr(guild_voice_client, "channel", None), "id", None),
            guild_voice_client_connected=(guild_voice_client.is_connected() if guild_voice_client is not None else None),
        )
        if existing is not None:
            if existing.channel_id == voice_channel.id:
                return "Already connected to this voice channel."
            await self.leave(guild.id)

        logger.info(
            "voice_connect_attempt",
            guild_id=guild.id,
            channel_id=voice_channel.id,
            channel_type=type(voice_channel).__name__,
        )
        try:
            voice_client = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
        except Exception as exc:
            logger.exception(
                "voice_connect_failed",
                guild_id=guild.id,
                channel_id=voice_channel.id,
                channel_type=type(voice_channel).__name__,
                error=str(exc),
            )
            raise

        await asyncio.sleep(1.0)
        is_connected = voice_client.is_connected()
        listen_supported = hasattr(voice_client, "listen")
        logger.info(
            "voice_connect_succeeded",
            guild_id=guild.id,
            channel_id=voice_channel.id,
            channel_type=type(voice_channel).__name__,
            voice_client_type=type(voice_client).__name__,
            voice_client_channel_id=getattr(getattr(voice_client, "channel", None), "id", None),
            is_connected=is_connected,
            listen_supported=listen_supported,
            guild_voice_client_is_same_object=(guild.voice_client is voice_client),
            guild_voice_client_type=(type(guild.voice_client).__name__ if guild.voice_client is not None else None),
            guild_voice_client_connected=(guild.voice_client.is_connected() if guild.voice_client is not None else None),
        )
        if not is_connected or not listen_supported:
            logger.error(
                "voice_connect_unusable_client",
                guild_id=guild.id,
                channel_id=voice_channel.id,
                channel_type=type(voice_channel).__name__,
                voice_client_type=type(voice_client).__name__,
                voice_client_repr=repr(voice_client),
                voice_client_channel_id=getattr(getattr(voice_client, "channel", None), "id", None),
                is_connected=is_connected,
                listen_supported=listen_supported,
                guild_voice_client_type=(type(guild.voice_client).__name__ if guild.voice_client is not None else None),
                guild_voice_client_channel_id=getattr(getattr(guild.voice_client, "channel", None), "id", None),
                guild_voice_client_connected=(guild.voice_client.is_connected() if guild.voice_client is not None else None),
            )
            try:
                await voice_client.disconnect(force=True)
            except Exception as exc:
                logger.warning(
                    "voice_connect_cleanup_failed",
                    guild_id=guild.id,
                    channel_id=voice_channel.id,
                    error=str(exc),
                )
            return "Failed to establish a usable Discord voice connection. The Discord voice handshake closed before audio listening could start."
        session = VoiceSession(
            guild_id=guild.id,
            channel_id=voice_channel.id,
            text_channel_id=text_channel.id,
            scope=scope,
            voice_client=voice_client,
            sink=None,
        )
        sink = AzureVoiceReceiveSink(self, session)
        session.sink = sink
        voice_client.listen(sink)
        session.connection_task = asyncio.create_task(self._run_realtime_session(session))
        session.playback_task = asyncio.create_task(self._playback_worker(session))
        self._sessions[guild.id] = session
        logger.info("voice_session_joined", guild_id=guild.id, channel_id=voice_channel.id, text_channel_id=text_channel.id)
        return f"Joined voice channel '{voice_channel.name}' and started Realtime listening."

    async def leave(self, guild_id: int) -> str:
        session = self._sessions.pop(guild_id, None)
        if session is None:
            return "The bot is not connected to a voice channel in this guild."

        await session.audio_queue.put(None)
        await session.playback_queue.put(None)

        if session.connection_task is not None:
            session.connection_task.cancel()
            try:
                await session.connection_task
            except asyncio.CancelledError:
                pass

        if session.playback_task is not None:
            session.playback_task.cancel()
            try:
                await session.playback_task
            except asyncio.CancelledError:
                pass

        if session.voice_client.is_listening():
            session.voice_client.stop_listening()
        if session.voice_client.is_connected():
            await session.voice_client.disconnect(force=True)
        logger.info("voice_session_left", guild_id=guild_id, channel_id=session.channel_id)
        return "Left the voice channel and stopped Realtime listening."

    def get_status(self, guild_id: int) -> str:
        session = self._sessions.get(guild_id)
        if session is None:
            return "The bot is not connected to a voice channel in this guild."
        transcript_text = session.last_transcript or "None yet"
        response_text = session.last_response_transcript or "None yet"
        return (
            f"Connected to voice channel ID {session.channel_id} from text channel ID {session.text_channel_id}. "
            f"Status: {session.status}. Last user transcript: {transcript_text}. "
            f"Last assistant transcript: {response_text}."
        )

    async def shutdown(self) -> None:
        for guild_id in list(self._sessions):
            await self.leave(guild_id)

    def handle_voice_packet(self, guild_id: int, speaker_name: str, pcm: bytes) -> None:
        session = self._sessions.get(guild_id)
        if session is None:
            return

        pcm24 = self._convert_discord_pcm_to_realtime_pcm(pcm)
        if not pcm24:
            return

        try:
            session.audio_queue.put_nowait(pcm24)
            session.status = f"streaming audio from {speaker_name}"
        except asyncio.QueueFull:
            logger.warning("voice_audio_queue_full", guild_id=guild_id, speaker_name=speaker_name)

    async def _run_realtime_session(self, session: VoiceSession) -> None:
        base_url = self._settings.azure_openai_endpoint.replace("https://", "wss://").rstrip("/") + "/openai/v1"
        client = AsyncOpenAI(websocket_base_url=base_url, api_key=self._settings.azure_openai_api_key)

        try:
            async with client.realtime.connect(model=self._settings.azure_openai_realtime_deployment) as connection:
                session.status = "connected"
                await connection.session.update(session=self._build_realtime_session_config())
                sender_task = asyncio.create_task(self._send_audio_loop(session, connection))
                try:
                    async for event in connection:
                        await self._handle_realtime_event(session, event)
                finally:
                    sender_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender_task
        except asyncio.CancelledError:
            session.status = "stopped"
            raise
        except Exception as exc:
            session.status = "error"
            logger.exception("voice_realtime_session_failed", guild_id=session.guild_id, error=str(exc))
        finally:
            await session.playback_queue.put(None)

    async def _send_audio_loop(self, session: VoiceSession, connection) -> None:
        while True:
            pcm_chunk = await session.audio_queue.get()
            if pcm_chunk is None:
                break
            await connection.input_audio_buffer.append(audio=base64.b64encode(pcm_chunk).decode("ascii"))

    async def _handle_realtime_event(self, session: VoiceSession, event) -> None:
        event_type = getattr(event, "type", None)
        if event_type == "session.created":
            session.status = "session created"
            logger.info("voice_realtime_session_created", guild_id=session.guild_id, session_id=event.session.id)
            return
        if event_type == "session.updated":
            session.status = "listening"
            logger.info("voice_realtime_session_updated", guild_id=session.guild_id, session_id=event.session.id)
            return
        if event_type == "input_audio_buffer.speech_started":
            session.status = "speech detected"
            return
        if event_type == "input_audio_buffer.speech_stopped":
            session.status = "awaiting response"
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = getattr(event, "transcript", "") or ""
            if transcript:
                if transcript.lower().strip() in self._settings.voice_chat_stop_phrases:
                    session.last_transcript = transcript
                    session.status = "stop phrase heard"
                    return
                session.last_transcript = transcript
            return
        if event_type in {"response.output_audio_transcript.delta", "response.audio_transcript.delta"}:
            delta = getattr(event, "delta", "") or ""
            if delta:
                session.last_response_transcript += delta
            return
        if event_type in {"response.output_audio.delta", "response.audio.delta"}:
            delta = getattr(event, "delta", "") or ""
            if delta:
                await session.playback_queue.put(base64.b64decode(delta))
            return
        if event_type == "response.done":
            session.status = "listening"
            transcript = session.last_response_transcript.strip()
            if transcript:
                logger.info(
                    "voice_realtime_response_done",
                    guild_id=session.guild_id,
                    transcript=transcript,
                )
            session.last_response_transcript = ""
            return
        if event_type == "error":
            session.status = "error"
            error = getattr(event, "error", None)
            logger.error(
                "voice_realtime_error",
                guild_id=session.guild_id,
                code=getattr(error, "code", None),
                message=getattr(error, "message", None),
                event_id=getattr(error, "event_id", None),
            )

    async def _playback_worker(self, session: VoiceSession) -> None:
        while True:
            pcm_chunk = await session.playback_queue.get()
            if pcm_chunk is None:
                break
            wav_bytes = self._pcm16_to_wav_bytes(pcm_chunk, self._settings.voice_chat_realtime_sample_rate)
            await self._play_wav_bytes(session.voice_client, wav_bytes)

    def _build_realtime_session_config(self) -> dict:
        return {
            "type": "realtime",
            "instructions": self._settings.system_prompt_base + " " + self._settings.bot_persona,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "transcription": {
                        "model": "whisper-1",
                    },
                    "format": {
                        "type": "audio/pcm",
                        "rate": self._settings.voice_chat_realtime_sample_rate,
                    },
                    "turn_detection": {
                        "type": self._settings.voice_chat_realtime_vad_type,
                        "threshold": self._settings.voice_chat_realtime_vad_threshold,
                        "prefix_padding_ms": self._settings.voice_chat_realtime_prefix_padding_ms,
                        "silence_duration_ms": self._settings.voice_chat_realtime_silence_duration_ms,
                        "create_response": True,
                    },
                },
                "output": {
                    "voice": self._settings.voice_chat_realtime_voice,
                    "format": {
                        "type": "audio/pcm",
                        "rate": self._settings.voice_chat_realtime_sample_rate,
                    },
                },
            },
        }

    def _convert_discord_pcm_to_realtime_pcm(self, pcm: bytes) -> bytes:
        mono = audioop.tomono(pcm, DISCORD_SAMPLE_WIDTH, 0.5, 0.5)
        converted, _ = audioop.ratecv(
            mono,
            DISCORD_SAMPLE_WIDTH,
            1,
            DISCORD_SAMPLE_RATE,
            self._settings.voice_chat_realtime_sample_rate,
            None,
        )
        return converted

    def _pcm16_to_wav_bytes(self, pcm: bytes, sample_rate: int) -> bytes:
        buffer = io.BytesIO()
        import wave

        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm)
        return buffer.getvalue()

    async def _play_wav_bytes(self, voice_client: voice_recv.VoiceRecvClient, wav_bytes: bytes) -> None:
        while voice_client.is_playing():
            await asyncio.sleep(0.01)

        source = discord.FFmpegPCMAudio(
            source="pipe:0",
            pipe=True,
            before_options="-f wav",
            options="-vn",
            stdin=io.BytesIO(wav_bytes),
        )
        loop = asyncio.get_running_loop()
        finished = loop.create_future()

        def _after_playback(error: Exception | None) -> None:
            if error is not None:
                logger.exception("voice_playback_failed", error=str(error))
            if not finished.done():
                loop.call_soon_threadsafe(finished.set_result, None)

        voice_client.play(source, after=_after_playback)
        await finished


import contextlib
