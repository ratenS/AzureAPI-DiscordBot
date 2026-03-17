from __future__ import annotations

import asyncio
from typing import Any, Dict

from openai import AsyncOpenAI
from sqlalchemy.orm import Session
import structlog

from app.config import Settings
from app.models import ScopeRef, VideoGenerationResult
from app.repositories.memory_repository import MemoryRepository

logger = structlog.get_logger(__name__)

SUPPORTED_VIDEO_SIZES = {
    "480x480",
    "480x854",
    "854x480",
    "720x720",
    "720x1280",
    "1280x720",
    "1080x1080",
    "1080x1920",
    "1920x1080",
}
SUPPORTED_VIDEO_SECONDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20}


class VideoService:
    def __init__(self, settings: Settings, repository: MemoryRepository) -> None:
        self._settings = settings
        self._repository = repository
        video_base_url = f"{settings.azure_openai_endpoint.rstrip('/')}/openai/v1/"
        self._client = AsyncOpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=video_base_url,
        )
        logger.info(
            "video_client_configured",
            endpoint=settings.azure_openai_endpoint,
            base_url=video_base_url,
            deployment=settings.azure_openai_video_deployment,
            default_size=settings.azure_openai_video_size,
            default_seconds=settings.azure_openai_video_seconds,
        )

    async def generate_video(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        moderation_result: Dict[str, Any],
    ) -> VideoGenerationResult:
        size = self._validated_size(self._settings.azure_openai_video_size)
        seconds = self._validated_seconds(self._settings.azure_openai_video_seconds)
        request_body = {
            "model": self._settings.azure_openai_video_deployment,
            "prompt": prompt,
            "size": size,
            "seconds": seconds,
        }
        logger.info(
            "video_generation_request_prepared",
            base_url=str(getattr(self._client, "base_url", "")),
            deployment=self._settings.azure_openai_video_deployment,
            request_body=request_body,
        )

        try:
            video = await self._client.videos.create(**request_body)
            result = await self._poll_video_completion(video.id, prompt=prompt, size=size, seconds=seconds)
        except Exception as exc:
            error_message = self._friendly_error_message(exc)
            logger.exception(
                "video_generation_request_failed",
                error=str(exc),
                base_url=str(getattr(self._client, "base_url", "")),
                deployment=self._settings.azure_openai_video_deployment,
                request_body=request_body,
            )
            failed_result = VideoGenerationResult(
                video_id=None,
                status="failed",
                requested_prompt=prompt,
                requested_size=size,
                requested_seconds=seconds,
                error_message=error_message,
            )
            self._persist_video_generation(
                session=session,
                scope=scope,
                requester_user_id=requester_user_id,
                prompt=prompt,
                moderation_result=moderation_result,
                result=failed_result,
            )
            return failed_result

        logger.info(
            "video_generation_response_received",
            response_id=result.video_id,
            status=result.status,
            output_url=result.output_url,
            progress=result.progress,
            has_video_bytes=bool(result.video_bytes),
        )

        self._persist_video_generation(
            session=session,
            scope=scope,
            requester_user_id=requester_user_id,
            prompt=prompt,
            moderation_result=moderation_result,
            result=result,
        )
        return result

    async def _poll_video_completion(self, video_id: str, prompt: str, size: str, seconds: int) -> VideoGenerationResult:
        latest_video = None
        max_attempts = self._settings.azure_openai_video_poll_max_attempts
        poll_interval_seconds = self._settings.azure_openai_video_poll_interval_seconds

        for attempt in range(1, max_attempts + 1):
            latest_video = await self._client.videos.retrieve(video_id)
            status = getattr(latest_video, "status", "queued") or "queued"
            progress = self._coerce_progress(getattr(latest_video, "progress", None))
            logger.info(
                "video_generation_status_polled",
                video_id=video_id,
                attempt=attempt,
                status=status,
                progress=progress,
            )

            if status == "completed":
                output_url = self._extract_output_url(latest_video)
                file_name = self._build_video_filename(video_id)
                video_bytes = await self._download_video_bytes(video_id)
                return VideoGenerationResult(
                    video_id=video_id,
                    status=status,
                    progress=progress,
                    output_url=output_url,
                    file_name=file_name if video_bytes else None,
                    video_bytes=video_bytes,
                    error_message=self._extract_error_message(latest_video),
                    requested_prompt=prompt,
                    requested_size=size,
                    requested_seconds=seconds,
                )

            if status in {"failed", "cancelled"}:
                return VideoGenerationResult(
                    video_id=video_id,
                    status=status,
                    progress=progress,
                    output_url=self._extract_output_url(latest_video),
                    error_message=self._extract_error_message(latest_video),
                    requested_prompt=prompt,
                    requested_size=size,
                    requested_seconds=seconds,
                )

            await asyncio.sleep(poll_interval_seconds)

        return VideoGenerationResult(
            video_id=video_id,
            status=getattr(latest_video, "status", "timeout") if latest_video else "timeout",
            progress=self._coerce_progress(getattr(latest_video, "progress", None)) if latest_video else None,
            output_url=self._extract_output_url(latest_video) if latest_video else None,
            error_message="Video generation timed out while waiting for Azure OpenAI to finish processing.",
            requested_prompt=prompt,
            requested_size=size,
            requested_seconds=seconds,
        )

    async def _download_video_bytes(self, video_id: str) -> bytes | None:
        if not self._settings.azure_openai_video_download_enabled:
            return None

        try:
            content = await self._client.videos.download_content(video_id, variant="video")
            if hasattr(content, "read"):
                data = content.read()
                if asyncio.iscoroutine(data):
                    data = await data
                return data if isinstance(data, bytes) and data else None
            if isinstance(content, bytes):
                return content or None
            if hasattr(content, "content"):
                raw = getattr(content, "content")
                return raw if isinstance(raw, bytes) and raw else None
        except Exception as exc:
            logger.warning(
                "video_download_failed",
                video_id=video_id,
                error=str(exc),
            )
        return None

    def _persist_video_generation(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        moderation_result: Dict[str, Any],
        result: VideoGenerationResult,
    ) -> None:
        persistence_payload = {
            **moderation_result,
            "video_id": result.video_id,
            "progress": result.progress,
            "error_message": result.error_message,
            "requested_size": result.requested_size,
            "requested_seconds": result.requested_seconds,
            "has_video_bytes": bool(result.video_bytes),
            "file_name": result.file_name,
        }
        self._repository.persist_video_generation(
            session=session,
            scope=scope,
            requester_user_id=requester_user_id,
            prompt=prompt,
            output_url=result.output_url,
            model_deployment=self._settings.azure_openai_video_deployment,
            moderation_result=persistence_payload,
            status=result.status,
        )

    @staticmethod
    def _coerce_progress(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_error_message(video: Any) -> str | None:
        if video is None:
            return None

        error = getattr(video, "error", None)
        if isinstance(error, str) and error:
            return error
        if error is not None:
            message = getattr(error, "message", None)
            if isinstance(message, str) and message:
                return message
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                return code
        return None

    @staticmethod
    def _extract_output_url(video: Any) -> str | None:
        if video is None:
            return None

        for attribute in ("url", "file_url", "output_url"):
            value = getattr(video, attribute, None)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _build_video_filename(video_id: str) -> str:
        safe_video_id = video_id.replace("/", "-").replace("\\", "-")
        return f"{safe_video_id}.mp4"

    @staticmethod
    def _validated_size(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_VIDEO_SIZES:
            raise ValueError(
                f"Unsupported Azure OpenAI video size '{value}'. Supported sizes: {', '.join(sorted(SUPPORTED_VIDEO_SIZES))}."
            )
        return normalized

    @staticmethod
    def _validated_seconds(value: int) -> int:
        if value not in SUPPORTED_VIDEO_SECONDS:
            raise ValueError("Azure OpenAI video duration must be between 1 and 20 seconds.")
        return value

    @staticmethod
    def _friendly_error_message(exc: Exception) -> str:
        message = str(exc)
        lowered = message.lower()
        if "401" in lowered or "unauthorized" in lowered:
            return "Azure OpenAI rejected the request because the video credentials are invalid or expired."
        if "403" in lowered or "forbidden" in lowered:
            return "Azure OpenAI denied access to video generation for the configured resource or identity."
        if "404" in lowered or "not found" in lowered:
            return "The Azure OpenAI video deployment or endpoint could not be found."
        if "429" in lowered or "rate limit" in lowered:
            return "Azure OpenAI is rate limiting video generation requests right now."
        if "400" in lowered or "bad request" in lowered:
            return "Azure OpenAI rejected the video request. Check prompt safety, supported settings, and deployment compatibility."
        if "timed out" in lowered or "timeout" in lowered:
            return "Azure OpenAI video generation did not finish before the configured bot timeout."
        return message or "Video generation failed due to an unexpected Azure OpenAI error."
