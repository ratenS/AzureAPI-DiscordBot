import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ConversationTurn, MemoryKind, MemoryRecord, ScopeRef, ScopeType


class MemoryRepository:
    def _ensure_scope_settings_row(self, session: Session, scope: ScopeRef, retention_days: int) -> None:
        session.execute(
            text(
                """
                INSERT INTO scope_settings (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    bot_enabled, memory_enabled, image_enabled, video_enabled, speech_enabled,
                    retention_days_raw_logs, updated_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    FALSE, TRUE, FALSE, FALSE, FALSE, :retention_days_raw_logs, NOW()
                )
                ON CONFLICT (scope_type, guild_id, channel_id, thread_id, dm_user_id)
                DO NOTHING
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "retention_days_raw_logs": retention_days,
            },
        )

    def persist_message(
        self,
        session: Session,
        scope: ScopeRef,
        author_user_id: int,
        role: str,
        content: str,
        discord_message_id: int | None,
        moderation_result: Dict[str, Any],
        retention_days: int,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)
        session.execute(
            text(
                """
                INSERT INTO conversation_messages (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    author_user_id, role, discord_message_id, content,
                    moderation_result_json, created_at, expires_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    :author_user_id, :role, :discord_message_id, :content,
                    CAST(:moderation_result_json AS JSONB), NOW(), :expires_at
                )
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "author_user_id": author_user_id,
                "role": role,
                "discord_message_id": discord_message_id,
                "content": content,
                "moderation_result_json": json.dumps(moderation_result),
                "expires_at": expires_at,
            },
        )

    def fetch_recent_turns(self, session: Session, scope: ScopeRef, limit: int = 12) -> List[ConversationTurn]:
        rows = session.execute(
            text(
                """
                SELECT role, content, created_at
                FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "limit": limit,
            },
        ).mappings()

        return [
            ConversationTurn(role=row["role"], content=row["content"], created_at=row["created_at"])
            for row in reversed(list(rows))
        ]

    def fetch_relevant_memories(self, session: Session, scope: ScopeRef, limit: int = 5) -> List[MemoryRecord]:
        rows = session.execute(
            text(
                """
                SELECT memory_text, memory_kind
                FROM memory_entries
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                  AND enabled = TRUE
                ORDER BY updated_at DESC
                LIMIT :limit
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "limit": limit,
            },
        ).mappings()

        return [
            MemoryRecord(memory_text=row["memory_text"], memory_kind=MemoryKind(row["memory_kind"]))
            for row in rows
        ]

    def store_memory(self, session: Session, scope: ScopeRef, memory_text: str, memory_kind: MemoryKind) -> None:
        session.execute(
            text(
                """
                INSERT INTO memory_entries (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    memory_kind, memory_text, embedding, confidence_score,
                    enabled, created_at, updated_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    :memory_kind, :memory_text, NULL, 0.5,
                    TRUE, NOW(), NOW()
                )
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "memory_kind": memory_kind.value,
                "memory_text": memory_text,
            },
        )

    def clear_memories(self, session: Session, scope: ScopeRef) -> None:
        session.execute(
            text(
                """
                DELETE FROM memory_entries
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
            },
        )

    def clear_conversation_messages(self, session: Session, scope: ScopeRef) -> None:
        session.execute(
            text(
                """
                DELETE FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
            },
        )

    def set_memory_enabled(self, session: Session, scope: ScopeRef, enabled: bool, retention_days: int) -> None:
        self._ensure_scope_settings_row(session, scope, retention_days)
        session.execute(
            text(
                """
                UPDATE scope_settings
                SET memory_enabled = :memory_enabled,
                    updated_at = NOW()
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "memory_enabled": enabled,
            },
        )

    def set_bot_enabled(self, session: Session, scope: ScopeRef, enabled: bool, retention_days: int) -> None:
        self._ensure_scope_settings_row(session, scope, retention_days)
        session.execute(
            text(
                """
                UPDATE scope_settings
                SET bot_enabled = :bot_enabled,
                    updated_at = NOW()
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "bot_enabled": enabled,
            },
        )

    def set_image_enabled(self, session: Session, scope: ScopeRef, enabled: bool, retention_days: int) -> None:
        self._ensure_scope_settings_row(session, scope, retention_days)
        session.execute(
            text(
                """
                UPDATE scope_settings
                SET image_enabled = :image_enabled,
                    updated_at = NOW()
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "image_enabled": enabled,
            },
        )

    def set_video_enabled(self, session: Session, scope: ScopeRef, enabled: bool, retention_days: int) -> None:
        self._ensure_scope_settings_row(session, scope, retention_days)
        session.execute(
            text(
                """
                UPDATE scope_settings
                SET video_enabled = :video_enabled,
                    updated_at = NOW()
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "video_enabled": enabled,
            },
        )

    def set_speech_enabled(self, session: Session, scope: ScopeRef, enabled: bool, retention_days: int) -> None:
        self._ensure_scope_settings_row(session, scope, retention_days)
        session.execute(
            text(
                """
                UPDATE scope_settings
                SET speech_enabled = :speech_enabled,
                    updated_at = NOW()
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "speech_enabled": enabled,
            },
        )

    def fetch_scope_settings(self, session: Session, scope: ScopeRef) -> Dict[str, Any]:
        row = session.execute(
            text(
                """
                SELECT bot_enabled, memory_enabled, image_enabled, video_enabled, speech_enabled, retention_days_raw_logs
                FROM scope_settings
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                LIMIT 1
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
            },
        ).mappings().first()

        return dict(row) if row else {}

    def fetch_latest_assistant_message(self, session: Session, scope: ScopeRef) -> Dict[str, Any] | None:
        row = session.execute(
            text(
                """
                SELECT discord_message_id, content
                FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                  AND role = 'assistant'
                  AND discord_message_id IS NOT NULL
                ORDER BY created_at DESC, discord_message_id DESC
                LIMIT 1
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
            },
        ).mappings().first()

        return dict(row) if row else None

    def fetch_assistant_message_by_discord_id(
        self,
        session: Session,
        scope: ScopeRef,
        discord_message_id: int,
    ) -> Dict[str, Any] | None:
        row = session.execute(
            text(
                """
                SELECT discord_message_id, content
                FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                  AND role = 'assistant'
                  AND discord_message_id = :discord_message_id
                LIMIT 1
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "discord_message_id": discord_message_id,
            },
        ).mappings().first()

        return dict(row) if row else None

    def fetch_recent_conversation_messages(
        self,
        session: Session,
        scope: ScopeRef,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        rows = session.execute(
            text(
                """
                SELECT discord_message_id, author_user_id, role, content
                FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "limit": limit,
            },
        ).mappings()

        return [dict(row) for row in rows]

    def delete_assistant_message_by_discord_id(
        self,
        session: Session,
        scope: ScopeRef,
        discord_message_id: int,
    ) -> bool:
        result = session.execute(
            text(
                """
                DELETE FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                  AND role = 'assistant'
                  AND discord_message_id = :discord_message_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "discord_message_id": discord_message_id,
            },
        )
        return bool(result.rowcount)

    def delete_message_by_discord_id(self, session: Session, scope: ScopeRef, discord_message_id: int) -> bool:
        result = session.execute(
            text(
                """
                DELETE FROM conversation_messages
                WHERE scope_type = :scope_type
                  AND guild_id IS NOT DISTINCT FROM :guild_id
                  AND channel_id IS NOT DISTINCT FROM :channel_id
                  AND thread_id IS NOT DISTINCT FROM :thread_id
                  AND dm_user_id IS NOT DISTINCT FROM :dm_user_id
                  AND discord_message_id = :discord_message_id
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "discord_message_id": discord_message_id,
            },
        )
        return bool(result.rowcount)

    def persist_image_generation(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        revised_prompt: str | None,
        output_url: str | None,
        model_deployment: str,
        moderation_result: Dict[str, Any],
        status: str,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO image_generations (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    requester_user_id, prompt, revised_prompt, output_url,
                    model_deployment, moderation_result_json, status, created_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    :requester_user_id, :prompt, :revised_prompt, :output_url,
                    :model_deployment, CAST(:moderation_result_json AS JSONB), :status, NOW()
                )
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "requester_user_id": requester_user_id,
                "prompt": prompt,
                "revised_prompt": revised_prompt,
                "output_url": output_url,
                "model_deployment": model_deployment,
                "moderation_result_json": json.dumps(moderation_result),
                "status": status,
            },
        )

    def persist_video_generation(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        prompt: str,
        output_url: str | None,
        model_deployment: str,
        moderation_result: Dict[str, Any],
        status: str,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO video_generations (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    requester_user_id, prompt, output_url,
                    model_deployment, moderation_result_json, status, created_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    :requester_user_id, :prompt, :output_url,
                    :model_deployment, CAST(:moderation_result_json AS JSONB), :status, NOW()
                )
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "requester_user_id": requester_user_id,
                "prompt": prompt,
                "output_url": output_url,
                "model_deployment": model_deployment,
                "moderation_result_json": json.dumps(moderation_result),
                "status": status,
            },
        )

    def persist_speech_generation(
        self,
        session: Session,
        scope: ScopeRef,
        requester_user_id: int,
        input_text: str,
        output_file_path: str | None,
        model_deployment: str,
        voice: str,
        moderation_result: Dict[str, Any],
        status: str,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO speech_generations (
                    scope_type, guild_id, channel_id, thread_id, dm_user_id,
                    requester_user_id, input_text, output_file_path,
                    model_deployment, voice, moderation_result_json, status, created_at
                ) VALUES (
                    :scope_type, :guild_id, :channel_id, :thread_id, :dm_user_id,
                    :requester_user_id, :input_text, :output_file_path,
                    :model_deployment, :voice, CAST(:moderation_result_json AS JSONB), :status, NOW()
                )
                """
            ),
            {
                "scope_type": scope.scope_type.value,
                "guild_id": scope.guild_id,
                "channel_id": scope.channel_id,
                "thread_id": scope.thread_id,
                "dm_user_id": scope.dm_user_id,
                "requester_user_id": requester_user_id,
                "input_text": input_text,
                "output_file_path": output_file_path,
                "model_deployment": model_deployment,
                "voice": voice,
                "moderation_result_json": json.dumps(moderation_result),
                "status": status,
            },
        )

    def cleanup_expired_messages(self, session: Session) -> None:
        session.execute(text("DELETE FROM conversation_messages WHERE expires_at <= NOW()"))
