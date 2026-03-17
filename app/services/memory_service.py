from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models import ConversationTurn, MemoryKind, MemoryRecord, ScopeRef
from app.repositories.memory_repository import MemoryRepository


@dataclass(slots=True)
class AssistantMessageRecord:
    discord_message_id: int
    content: str


@dataclass(slots=True)
class ConversationMessageRecord:
    discord_message_id: int | None
    author_user_id: int
    role: str
    content: str


class MemoryService:
    def __init__(self, repository: MemoryRepository, retention_days: int, sync_heuristics_enabled: bool) -> None:
        self._repository = repository
        self._retention_days = retention_days
        self._sync_heuristics_enabled = sync_heuristics_enabled

    def get_scope_settings(self, session: Session, scope: ScopeRef) -> Dict[str, Any]:
        return self._repository.fetch_scope_settings(session, scope)

    def persist_user_message(
        self,
        session: Session,
        scope: ScopeRef,
        author_user_id: int,
        content: str,
        discord_message_id: int | None,
        moderation_result: Dict[str, Any],
    ) -> None:
        self._repository.persist_message(
            session,
            scope,
            author_user_id,
            "user",
            content,
            discord_message_id,
            moderation_result,
            self._retention_days,
        )

    def persist_assistant_message(
        self,
        session: Session,
        scope: ScopeRef,
        content: str,
        discord_message_id: int,
        moderation_result: Dict[str, Any],
    ) -> None:
        self._repository.persist_message(
            session,
            scope,
            author_user_id=0,
            role="assistant",
            content=content,
            discord_message_id=discord_message_id,
            moderation_result=moderation_result,
            retention_days=self._retention_days,
        )

    def get_recent_turns(self, session: Session, scope: ScopeRef) -> List[ConversationTurn]:
        return self._repository.fetch_recent_turns(session, scope)

    def get_relevant_memories(self, session: Session, scope: ScopeRef) -> List[MemoryRecord]:
        return self._repository.fetch_relevant_memories(session, scope)

    def get_latest_assistant_message(self, session: Session, scope: ScopeRef) -> AssistantMessageRecord | None:
        row = self._repository.fetch_latest_assistant_message(session, scope)
        if row is None:
            return None
        return AssistantMessageRecord(discord_message_id=row["discord_message_id"], content=row["content"])

    def get_assistant_message_by_discord_id(
        self,
        session: Session,
        scope: ScopeRef,
        discord_message_id: int,
    ) -> AssistantMessageRecord | None:
        row = self._repository.fetch_assistant_message_by_discord_id(session, scope, discord_message_id)
        if row is None:
            return None
        return AssistantMessageRecord(discord_message_id=row["discord_message_id"], content=row["content"])

    def delete_assistant_message_by_discord_id(self, session: Session, scope: ScopeRef, discord_message_id: int) -> bool:
        return self._repository.delete_assistant_message_by_discord_id(session, scope, discord_message_id)

    def get_recent_conversation_messages(self, session: Session, scope: ScopeRef, limit: int = 20) -> List[ConversationMessageRecord]:
        rows = self._repository.fetch_recent_conversation_messages(session, scope, limit=limit)
        return [
            ConversationMessageRecord(
                discord_message_id=row["discord_message_id"],
                author_user_id=row["author_user_id"],
                role=row["role"],
                content=row["content"],
            )
            for row in rows
        ]

    def delete_message_by_discord_id(self, session: Session, scope: ScopeRef, discord_message_id: int) -> bool:
        return self._repository.delete_message_by_discord_id(session, scope, discord_message_id)

    def maybe_extract_memories(self, session: Session, scope: ScopeRef, content: str) -> None:
        if not self._sync_heuristics_enabled:
            return

        candidate = content.strip()
        lowered = candidate.lower()
        if len(candidate) < 20:
            return

        if "remember" in lowered or "my preference" in lowered or "i prefer" in lowered:
            kind = MemoryKind.PREFERENCE
        elif any(token in lowered for token in ("i am", "my name is", "i work", "i live")):
            kind = MemoryKind.FACT
        else:
            return

        self._repository.store_memory(session, scope, candidate[:500], kind)

    def inspect_memories(self, session: Session, scope: ScopeRef, include_raw: bool = False) -> str:
        memories = self.get_relevant_memories(session, scope)
        if not memories:
            return "No memories stored for this scope."

        if include_raw:
            return "\n".join(f"- [{memory.memory_kind.value}] {memory.memory_text}" for memory in memories)

        summary = "; ".join(memory.memory_text for memory in memories)
        return f"Summary of stored memories: {summary}"

    def clear_scope_memories(self, session: Session, scope: ScopeRef) -> None:
        self._repository.clear_memories(session, scope)

    def clear_scope_context(self, session: Session, scope: ScopeRef) -> None:
        self._repository.clear_conversation_messages(session, scope)
        self._repository.clear_memories(session, scope)

    def set_scope_memory_enabled(self, session: Session, scope: ScopeRef, enabled: bool) -> None:
        self._repository.set_memory_enabled(session, scope, enabled, self._retention_days)

    def set_scope_bot_enabled(self, session: Session, scope: ScopeRef, enabled: bool) -> None:
        self._repository.set_bot_enabled(session, scope, enabled, self._retention_days)

    def set_scope_image_enabled(self, session: Session, scope: ScopeRef, enabled: bool) -> None:
        self._repository.set_image_enabled(session, scope, enabled, self._retention_days)

    def set_scope_video_enabled(self, session: Session, scope: ScopeRef, enabled: bool) -> None:
        self._repository.set_video_enabled(session, scope, enabled, self._retention_days)

    def set_scope_speech_enabled(self, session: Session, scope: ScopeRef, enabled: bool) -> None:
        self._repository.set_speech_enabled(session, scope, enabled, self._retention_days)

    def cleanup_expired_messages(self, session: Session) -> None:
        self._repository.cleanup_expired_messages(session)
