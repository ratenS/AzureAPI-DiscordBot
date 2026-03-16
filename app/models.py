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
