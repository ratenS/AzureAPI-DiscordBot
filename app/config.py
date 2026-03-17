from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="azure-discord-bot", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")

    discord_bot_token: str = Field(alias="DISCORD_BOT_TOKEN")
    discord_application_id: str = Field(alias="DISCORD_APPLICATION_ID")
    discord_admin_user_ids_raw: str = Field(default="", alias="DISCORD_ADMIN_USER_IDS")

    database_url: str = Field(alias="DATABASE_URL")

    azure_openai_endpoint: str = Field(alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str = Field(alias="AZURE_OPENAI_API_KEY")
    azure_openai_api_version: str = Field(alias="AZURE_OPENAI_API_VERSION")
    azure_openai_chat_deployment: str = Field(alias="AZURE_OPENAI_CHAT_DEPLOYMENT")
    azure_openai_embedding_deployment: str = Field(alias="AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    azure_openai_image_deployment: str = Field(alias="AZURE_OPENAI_IMAGE_DEPLOYMENT")
    azure_openai_video_deployment: str = Field(alias="AZURE_OPENAI_VIDEO_DEPLOYMENT")
    azure_openai_video_size: str = Field(default="1280x720", alias="AZURE_OPENAI_VIDEO_SIZE")
    azure_openai_video_seconds: int = Field(default=4, alias="AZURE_OPENAI_VIDEO_SECONDS")
    azure_openai_video_poll_interval_seconds: int = Field(default=20, alias="AZURE_OPENAI_VIDEO_POLL_INTERVAL_SECONDS")
    azure_openai_video_poll_max_attempts: int = Field(default=18, alias="AZURE_OPENAI_VIDEO_POLL_MAX_ATTEMPTS")
    azure_openai_video_download_enabled: bool = Field(default=True, alias="AZURE_OPENAI_VIDEO_DOWNLOAD_ENABLED")
    azure_openai_speech_deployment: str = Field(alias="AZURE_OPENAI_SPEECH_DEPLOYMENT")
    azure_openai_speech_voice: str = Field(default="alloy", alias="AZURE_OPENAI_SPEECH_VOICE")
    speech_key: str = Field(alias="SPEECH_KEY")
    speech_region: str = Field(alias="SPEECH_REGION")
    azure_openai_realtime_deployment: str = Field(alias="AZURE_OPENAI_REALTIME_DEPLOYMENT")
    voice_chat_realtime_voice: str = Field(default="alloy", alias="VOICE_CHAT_REALTIME_VOICE")
    voice_chat_realtime_vad_type: str = Field(default="server_vad", alias="VOICE_CHAT_REALTIME_VAD_TYPE")
    voice_chat_realtime_vad_threshold: float = Field(default=0.5, alias="VOICE_CHAT_REALTIME_VAD_THRESHOLD")
    voice_chat_realtime_prefix_padding_ms: int = Field(default=300, alias="VOICE_CHAT_REALTIME_PREFIX_PADDING_MS")
    voice_chat_realtime_silence_duration_ms: int = Field(default=200, alias="VOICE_CHAT_REALTIME_SILENCE_DURATION_MS")
    voice_chat_realtime_chunk_ms: int = Field(default=100, alias="VOICE_CHAT_REALTIME_CHUNK_MS")
    voice_chat_realtime_sample_rate: int = Field(default=24000, alias="VOICE_CHAT_REALTIME_SAMPLE_RATE")
    voice_chat_stop_phrases_raw: str = Field(default="stop,goodbye", alias="VOICE_CHAT_STOP_PHRASES")

    allow_dms: bool = Field(default=True, alias="ALLOW_DMS")
    default_raw_log_retention_days: int = Field(default=30, alias="DEFAULT_RAW_LOG_RETENTION_DAYS")
    memory_sync_heuristics_enabled: bool = Field(default=True, alias="MEMORY_SYNC_HEURISTICS_ENABLED")
    max_prompt_chars: int = Field(default=4000, alias="MAX_PROMPT_CHARS")
    rate_limit_requests_per_minute: int = Field(default=10, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")
    bot_persona: str = Field(default="You are a neutral, helpful assistant.", alias="BOT_PERSONA")
    system_prompt_base: str = Field(
        default="Follow server policy, be concise, and avoid unsafe or disallowed content.",
        alias="SYSTEM_PROMPT_BASE",
    )

    @property
    def discord_admin_user_ids(self) -> List[int]:
        if not self.discord_admin_user_ids_raw.strip():
            return []
        return [int(item.strip()) for item in self.discord_admin_user_ids_raw.split(",") if item.strip()]

    @property
    def voice_chat_stop_phrases(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.voice_chat_stop_phrases_raw.split(",")
            if item.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
