from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from openai import AsyncAzureOpenAI
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ScopeRef
from app.repositories.memory_repository import MemoryRepository


class SpeechService:
    def __init__(self, settings: Settings, repository: MemoryRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    async def generate_speech(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        text: str,
        moderation_result: Dict[str, Any],
    ) -> tuple[str, bytes]:
        try:
            response = await self._client.audio.speech.create(
                model=self._settings.azure_openai_speech_deployment,
                voice=self._settings.azure_openai_speech_voice,
                input=text,
            )
            audio_bytes = response.read()
        except Exception:
            self._repository.persist_speech_generation(
                session=session,
                scope=scope,
                requester_user_id=requester_user_id,
                input_text=text,
                output_file_path=None,
                model_deployment=self._settings.azure_openai_speech_deployment,
                voice=self._settings.azure_openai_speech_voice,
                moderation_result=moderation_result,
                status="failed",
            )
            raise

        file_name = f"speech-{requester_user_id}-{abs(hash(text)) % 100000000}.mp3"
        output_file_path = str(Path("generated") / file_name)

        self._repository.persist_speech_generation(
            session=session,
            scope=scope,
            requester_user_id=requester_user_id,
            input_text=text,
            output_file_path=output_file_path,
            model_deployment=self._settings.azure_openai_speech_deployment,
            voice=self._settings.azure_openai_speech_voice,
            moderation_result=moderation_result,
            status="completed",
        )

        return file_name, audio_bytes
