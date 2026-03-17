from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ScopeType(str, Enum):
    CHANNEL = "channel"
    THREAD = "thread"
    DM = "dm"
    USER_PROFILE = "user_profile"


class MemoryKind(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    SUMMARY = "summary"


@dataclass(slots=True)
class ScopeRef:
    scope_type: ScopeType
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    dm_user_id: Optional[int] = None


@dataclass(slots=True)
class ConversationTurn:
    role: str
    content: str
    created_at: datetime


@dataclass(slots=True)
class MemoryRecord:
    memory_text: str
    memory_kind: MemoryKind
    score: float = 0.0


@dataclass(slots=True)
class VideoGenerationResult:
    video_id: str | None
    status: str
    progress: int | None = None
    output_url: str | None = None
    error_message: str | None = None
    file_name: str | None = None
    video_bytes: bytes | None = None
    requested_prompt: str | None = None
    requested_size: str | None = None
    requested_seconds: int | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status in {"failed", "cancelled"}

    @property
    def has_file(self) -> bool:
        return bool(self.file_name and self.video_bytes)

    def user_message(self) -> str:
        if self.has_file:
            return "Video generation completed and the MP4 file is attached."
        if self.is_completed and self.output_url:
            return f"Video generation completed: {self.output_url}"
        if self.is_completed:
            return "Video generation completed, but no downloadable video URL was returned."
        if self.is_failed:
            detail = f" Details: {self.error_message}" if self.error_message else ""
            return f"Video generation failed with status `{self.status}`.{detail}"
        progress_text = f" ({self.progress}% complete)" if self.progress is not None else ""
        if self.video_id:
            return f"Video request submitted with ID `{self.video_id}`. Current status: `{self.status}`{progress_text}."
        return f"Video request submitted. Current status: `{self.status}`{progress_text}."
