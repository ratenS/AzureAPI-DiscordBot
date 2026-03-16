from __future__ import annotations

from typing import Any, Dict

from openai import AsyncAzureOpenAI
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ScopeRef
from app.repositories.memory_repository import MemoryRepository


class ImageService:
    def __init__(self, settings: Settings, repository: MemoryRepository) -> None:
        self._settings = settings
        self._repository = repository
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    async def generate_image(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        moderation_result: Dict[str, Any],
    ) -> str:
        result = await self._client.images.generate(
            model=self._settings.azure_openai_image_deployment,
            prompt=prompt,
            size="1024x1024",
        )

        image_url = None
        revised_prompt = None
        if result.data:
            image_url = getattr(result.data[0], "url", None)
            revised_prompt = getattr(result.data[0], "revised_prompt", None)

        self._repository.persist_image_generation(
            session=session,
            scope=scope,
            requester_user_id=requester_user_id,
            prompt=prompt,
            revised_prompt=revised_prompt,
            output_url=image_url,
            model_deployment=self._settings.azure_openai_image_deployment,
            moderation_result=moderation_result,
            status="completed" if image_url else "empty",
        )

        if not image_url:
            raise RuntimeError("Image generation returned no URL")

        return image_url
