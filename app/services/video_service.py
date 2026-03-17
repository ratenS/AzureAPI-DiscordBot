from __future__ import annotations

from typing import Any, Dict

from openai import AsyncAzureOpenAI
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ScopeRef
from app.repositories.memory_repository import MemoryRepository


class VideoService:
    def __init__(self, settings: Settings, repository: MemoryRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    async def generate_video(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        moderation_result: Dict[str, Any],
    ) -> str:
        try:
            operation = await self._client.responses.create(
                model=self._settings.azure_openai_video_deployment,
                input=prompt,
                extra_body={"modalities": ["video"]},
            )
            output_url = self._extract_output_url(operation)
            status = "completed" if output_url else getattr(operation, "status", "submitted")
        except Exception:
            self._repository.persist_video_generation(
                session=session,
                scope=scope,
                requester_user_id=requester_user_id,
                prompt=prompt,
                output_url=None,
                model_deployment=self._settings.azure_openai_video_deployment,
                moderation_result=moderation_result,
                status="failed",
            )
            raise

        self._repository.persist_video_generation(
            session=session,
            scope=scope,
            requester_user_id=requester_user_id,
            prompt=prompt,
            output_url=output_url,
            model_deployment=self._settings.azure_openai_video_deployment,
            moderation_result=moderation_result,
            status=status,
        )

        if not output_url:
            response_id = getattr(operation, "id", "unknown")
            return f"Video request submitted. Response ID: {response_id}"

        return output_url

    @staticmethod
    def _extract_output_url(operation: Any) -> str | None:
        output = getattr(operation, "output", None) or []
        for item in output:
            item_url = getattr(item, "url", None)
            if item_url:
                return item_url

            content_items = getattr(item, "content", None) or []
            for content in content_items:
                content_url = getattr(content, "url", None)
                if content_url:
                    return content_url

                file_url = getattr(content, "file_url", None)
                if file_url:
                    return file_url

        return None
