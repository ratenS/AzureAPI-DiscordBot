from __future__ import annotations

from openai import AsyncAzureOpenAI

from app.config import Settings
from app.models import ConversationTurn, MemoryRecord


class ChatService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    async def generate_reply(
        self,
        prompt: str,
        recent_turns: list[ConversationTurn],
        memories: list[MemoryRecord],
    ) -> str:
        history = [{"role": turn.role, "content": turn.content} for turn in recent_turns]
        memory_text = "\n".join(f"- [{memory.memory_kind.value}] {memory.memory_text}" for memory in memories) or "None"
        messages = [
            {"role": "system", "content": self._settings.system_prompt_base},
            {"role": "system", "content": self._settings.bot_persona},
            {
                "role": "system",
                "content": f"Relevant stored memories:\n{memory_text}\nTreat memories as helpful context, not absolute truth.",
            },
            *history,
            {"role": "user", "content": prompt},
        ]

        response = await self._client.chat.completions.create(
            model=self._settings.azure_openai_chat_deployment,
            messages=messages,
            temperature=0.7,
        )
        if not response.choices:
            return "I could not generate a response."

        content = response.choices[0].message.content
        if isinstance(content, str):
            content = content.strip()
            if content:
                return content
        return "I could not generate a response."
