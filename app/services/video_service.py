from __future__ import annotations

from typing import Any, Dict

from openai import AsyncOpenAI
from sqlalchemy.orm import Session
import structlog

from app.config import Settings
from app.models import ScopeRef
from app.repositories.memory_repository import MemoryRepository

logger = structlog.get_logger(__name__)


class VideoService:
    def __init__(self, settings: Settings, repository: MemoryRepository) -> None:
        self._settings = settings
        self._repository = repository
        video_base_url = f"{settings.azure_openai_endpoint.rstrip('/')}/openai/v1/videos"
        self._client = AsyncOpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=video_base_url,
        )
        logger.info(
            "video_client_configured",
            endpoint=settings.azure_openai_endpoint,
            base_url=video_base_url,
            deployment=settings.azure_openai_video_deployment,
        )

    async def generate_video(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        moderation_result: Dict[str, Any],
    ) -> str:
        request_body = {
            "model": self._settings.azure_openai_video_deployment,
            "prompt": prompt,
        }
        logger.info(
            "video_generation_request_prepared",
            base_url=getattr(self._client, "base_url", None),
            deployment=self._settings.azure_openai_video_deployment,
            request_body=request_body,
        )

        try:
            operation = await self._client.post("", cast_to=dict, body=request_body)
            output_url = self._extract_output_url(operation)
            status = operation.get("status", "submitted") if isinstance(operation, dict) else getattr(operation, "status", "submitted")
        except Exception as exc:
            logger.exception(
                "video_generation_request_failed",
                error=str(exc),
                base_url=getattr(self._client, "base_url", None),
                deployment=self._settings.azure_openai_video_deployment,
                request_body=request_body,
            )
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

        logger.info(
            "video_generation_response_received",
            response_id=operation.get("id") if isinstance(operation, dict) else getattr(operation, "id", None),
            status=status,
            output_url=output_url,
        )

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
            response_id = operation.get("id", "unknown") if isinstance(operation, dict) else getattr(operation, "id", "unknown")
            return f"Video request submitted. Response ID: {response_id}"

        return output_url

    @staticmethod
    def _extract_output_url(operation: Any) -> str | None:
        if isinstance(operation, dict):
            for key in ("url", "file_url", "output_url"):
                value = operation.get(key)
                if isinstance(value, str) and value:
                    return value

            output = operation.get("output") or []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for key in ("url", "file_url", "output_url"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        return value
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    for key in ("url", "file_url", "output_url"):
                        value = content.get(key)
                        if isinstance(value, str) and value:
                            return value
            return None

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
