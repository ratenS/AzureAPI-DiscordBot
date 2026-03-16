from __future__ import annotations

from typing import Optional

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from app.config import Settings
from app.db import Database
from app.models import ScopeRef, ScopeType
from app.services.chat_service import ChatService
from app.services.image_service import ImageService
from app.services.memory_service import MemoryService
from app.services.rate_limit_service import RateLimitExceeded, RateLimitService
from app.services.safety_service import SafetyService, SafetyViolation

logger = structlog.get_logger(__name__)


class AzureDiscordBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        database: Database,
        chat_service: ChatService,
        image_service: ImageService,
        memory_service: MemoryService,
        safety_service: SafetyService,
        rate_limit_service: RateLimitService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, application_id=int(settings.discord_application_id))
        self.settings = settings
        self.database = database
        self.chat_service = chat_service
        self.image_service = image_service
        self.memory_service = memory_service
        self.safety_service = safety_service
        self.rate_limit_service = rate_limit_service

    async def setup_hook(self) -> None:
        self.tree.add_command(ImageCommand(self).command)
        self.tree.add_command(MemoryGroup(self).group)
        self.tree.add_command(ProfileGroup(self).group)
        self.tree.add_command(BotAdminGroup(self).group)
        await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("discord_bot_ready", user=str(self.user))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            if not self.settings.allow_dms:
                return
            prompt = message.content.strip()
        else:
            if self.user is None or self.user not in message.mentions:
                return
            prompt = message.content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
            if not prompt:
                return

        await self._handle_chat_message(message, prompt)

    async def _handle_chat_message(self, message: discord.Message, prompt: str) -> None:
        scope = self._resolve_scope(message)
        if not self._is_scope_enabled(scope):
            await message.reply("This bot is not enabled for this channel or scope.")
            return

        if len(prompt) > self.settings.max_prompt_chars:
            await message.reply("Prompt too long. Please shorten your message.")
            return

        try:
            self.rate_limit_service.check(f"user:{message.author.id}")
            if message.guild:
                self.rate_limit_service.check(f"guild:{message.guild.id}")

            moderation_result = await self.safety_service.analyze_text(prompt)

            with self.database.session() as session:
                self.memory_service.persist_user_message(
                    session,
                    scope,
                    message.author.id,
                    prompt,
                    message.id,
                    moderation_result,
                )
                recent_turns = self.memory_service.get_recent_turns(session, scope)
                memories = self.memory_service.get_relevant_memories(session, scope)

            reply = await self.chat_service.generate_reply(prompt, recent_turns, memories)
            output_moderation = await self.safety_service.analyze_text(reply)

            with self.database.session() as session:
                self.memory_service.persist_assistant_message(session, scope, reply, output_moderation)
                self.memory_service.maybe_extract_memories(session, scope, prompt)

            await message.reply(reply)
        except SafetyViolation:
            await message.reply("The request was blocked by content safety policy.")
        except RateLimitExceeded:
            await message.reply("Rate limit exceeded. Please wait a minute and try again.")
        except Exception as exc:
            logger.exception("chat_request_failed", error=str(exc))
            await message.reply("The bot failed to process the request.")

    def _resolve_scope(self, message: discord.Message) -> ScopeRef:
        if isinstance(message.channel, discord.DMChannel):
            return ScopeRef(scope_type=ScopeType.DM, dm_user_id=message.author.id)

        if isinstance(message.channel, discord.Thread):
            return ScopeRef(
                scope_type=ScopeType.THREAD,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.parent_id,
                thread_id=message.channel.id,
            )

        return ScopeRef(
            scope_type=ScopeType.CHANNEL,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
        )

    def _is_scope_enabled(self, scope: ScopeRef) -> bool:
        if scope.scope_type == ScopeType.DM:
            return self.settings.allow_dms

        with self.database.session() as session:
            settings = self.memory_service.get_scope_settings(session, scope)
            return bool(settings.get("bot_enabled", False))

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.discord_admin_user_ids


class ImageCommand:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.command = app_commands.Command(
            name="image",
            description="Generate an image from a prompt.",
            callback=self.image,
        )

    async def image(self, interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(thinking=True)
        scope = self.bot._resolve_interaction_scope(interaction)
        if not self.bot._is_image_allowed(scope):
            await interaction.followup.send("Image generation is not enabled for this scope.")
            return

        try:
            self.bot.rate_limit_service.check(f"user:{interaction.user.id}:image")
            moderation_result = await self.bot.safety_service.analyze_text(prompt)
            with self.bot.database.session() as session:
                image_url = await self.bot.image_service.generate_image(
                    session,
                    scope,
                    interaction.user.id,
                    prompt,
                    moderation_result,
                )
            await interaction.followup.send(image_url)
        except SafetyViolation:
            await interaction.followup.send("The image prompt was blocked by content safety policy.")
        except RateLimitExceeded:
            await interaction.followup.send("Rate limit exceeded for image generation.")
        except Exception as exc:
            logger.exception("image_generation_failed", error=str(exc))
            await interaction.followup.send("Image generation failed.")


class MemoryGroup:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.group = app_commands.Group(name="memory", description="Manage stored bot memory.")
        self.group.command(name="inspect", description="Inspect memories for this scope.")(self.inspect)
        self.group.command(name="clear", description="Clear memories for this scope.")(self.clear)
        self.group.command(name="disable", description="Disable memory for this scope.")(self.disable)
        self.group.command(name="enable", description="Enable memory for this scope.")(self.enable)

    async def inspect(self, interaction: discord.Interaction, raw: Optional[bool] = False) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            summary = self.bot.memory_service.inspect_memories(session, scope, include_raw=bool(raw))
        await interaction.response.send_message(summary, ephemeral=True)

    async def clear(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.clear_scope_memories(session, scope)
        await interaction.response.send_message("Memories cleared for this scope.", ephemeral=True)

    async def disable(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_memory_enabled(session, scope, False)
        await interaction.response.send_message("Memory disabled for this scope.", ephemeral=True)

    async def enable(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_memory_enabled(session, scope, True)
        await interaction.response.send_message("Memory enabled for this scope.", ephemeral=True)


class ProfileGroup:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.group = app_commands.Group(name="profile", description="Manage user profile memory.")
        memory_group = app_commands.Group(name="memory", description="Toggle profile memory.", parent=self.group)
        memory_group.command(name="on", description="Enable your profile memory.")(self.enable)
        memory_group.command(name="off", description="Disable your profile memory.")(self.disable)

    async def enable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Profile memory toggle storage is reserved for a later migration.", ephemeral=True)

    async def disable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Profile memory toggle storage is reserved for a later migration.", ephemeral=True)


class BotAdminGroup:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.group = app_commands.Group(name="bot", description="Manage bot availability.")
        self.group.command(name="enable-channel", description="Enable the bot in this channel.")(self.enable_channel)
        self.group.command(name="disable-channel", description="Disable the bot in this channel.")(self.disable_channel)
        self.group.command(name="help", description="Show usage details.")(self.help)

    async def enable_channel(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_bot_enabled(session, scope, True)
        await interaction.response.send_message("Bot enabled for this scope.", ephemeral=True)

    async def disable_channel(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_bot_enabled(session, scope, False)
        await interaction.response.send_message("Bot disabled for this scope.", ephemeral=True)

    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Mention the bot in approved channels, send direct messages in DMs, or use slash commands like /image and /memory inspect.",
            ephemeral=True,
        )


def _resolve_interaction_scope(self: AzureDiscordBot, interaction: discord.Interaction) -> ScopeRef:
    channel = interaction.channel
    if channel is None:
        return ScopeRef(scope_type=ScopeType.DM, dm_user_id=interaction.user.id)

    if isinstance(channel, discord.DMChannel) or interaction.guild is None:
        return ScopeRef(scope_type=ScopeType.DM, dm_user_id=interaction.user.id)

    if isinstance(channel, discord.Thread):
        return ScopeRef(
            scope_type=ScopeType.THREAD,
            guild_id=interaction.guild.id,
            channel_id=channel.parent_id,
            thread_id=channel.id,
        )

    return ScopeRef(scope_type=ScopeType.CHANNEL, guild_id=interaction.guild.id, channel_id=channel.id)


AzureDiscordBot._resolve_interaction_scope = _resolve_interaction_scope


def _is_image_allowed(self: AzureDiscordBot, scope: ScopeRef) -> bool:
    if scope.scope_type == ScopeType.DM:
        return self.settings.allow_dms

    with self.database.session() as session:
        settings = self.memory_service.get_scope_settings(session, scope)
        return bool(settings.get("image_enabled", False))


AzureDiscordBot._is_image_allowed = _is_image_allowed
