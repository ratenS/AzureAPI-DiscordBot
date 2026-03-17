from __future__ import annotations

from io import BytesIO
from typing import Optional

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from app.config import Settings
from app.db import Database
from app.models import ScopeRef, ScopeType, VideoGenerationResult
from app.services.chat_service import ChatService
from app.services.image_service import ImageService
from app.services.memory_service import MemoryService
from app.services.rate_limit_service import RateLimitExceeded, RateLimitService
from app.services.speech_service import SpeechService
from app.services.video_service import VideoService
from app.services.voice_chat_service import VoiceChatService

logger = structlog.get_logger(__name__)

DISCORD_MESSAGE_CHAR_LIMIT = 2000
DELETE_REACTION_EMOJI = "🗑️"


class AzureDiscordBot(commands.Bot):
    def __init__(
        self,
        settings: Settings,
        database: Database,
        chat_service: ChatService,
        image_service: ImageService,
        video_service: VideoService,
        speech_service: SpeechService,
        voice_chat_service: VoiceChatService,
        memory_service: MemoryService,
        rate_limit_service: RateLimitService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents, application_id=int(settings.discord_application_id))
        self.settings = settings
        self.database = database
        self.chat_service = chat_service
        self.image_service = image_service
        self.video_service = video_service
        self.speech_service = speech_service
        self.voice_chat_service = voice_chat_service
        self.memory_service = memory_service
        self.rate_limit_service = rate_limit_service

    async def setup_hook(self) -> None:
        self.tree.add_command(ImageCommand(self).command)
        self.tree.add_command(VideoCommand(self).command)
        self.tree.add_command(SpeechCommand(self).command)
        self.tree.add_command(VoiceGroup(self).group)
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

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.user is not None and payload.user_id == self.user.id:
            return

        if str(payload.emoji) != DELETE_REACTION_EMOJI:
            return

        try:
            user = self.get_user(payload.user_id) or await self.fetch_user(payload.user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning(
                "bot_admin_reaction_delete_user_unavailable",
                user_id=payload.user_id,
                channel_id=payload.channel_id,
                message_id=payload.message_id,
            )
            return

        if user.bot or not self.is_admin(payload.user_id):
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "bot_admin_reaction_delete_channel_unavailable",
                    channel_id=payload.channel_id,
                    message_id=payload.message_id,
                    user_id=payload.user_id,
                )
                return

        if not isinstance(channel, (discord.DMChannel, discord.TextChannel, discord.Thread)):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            logger.info(
                "bot_admin_reaction_delete_message_not_found",
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                user_id=payload.user_id,
            )
            return
        except discord.Forbidden:
            logger.warning(
                "bot_admin_reaction_delete_message_forbidden",
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                user_id=payload.user_id,
            )
            return
        except discord.HTTPException as exc:
            logger.exception(
                "bot_admin_reaction_delete_message_http_error",
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                user_id=payload.user_id,
                error=str(exc),
            )
            return

        if self.user is None or message.author.id != self.user.id:
            return

        scope = self._resolve_scope(message)
        outcome = await _delete_bot_message_for_scope_from_channel(self, channel, scope, payload.message_id)
        logger.info(
            "bot_admin_reaction_delete_completed",
            channel_id=payload.channel_id,
            message_id=payload.message_id,
            user_id=payload.user_id,
            outcome=outcome,
        )

    async def _handle_chat_message(self, message: discord.Message, prompt: str) -> None:
        scope = self._resolve_scope(message)
        memory_enabled = True
        if scope.scope_type == ScopeType.DM:
            if not self.settings.allow_dms:
                await message.reply("This bot is not enabled for this channel or scope.")
                return
        else:
            with self.database.session() as session:
                scope_settings = self.memory_service.get_scope_settings(session, scope)
            if not bool(scope_settings.get("bot_enabled", False)):
                await message.reply("This bot is not enabled for this channel or scope.")
                return
            memory_enabled = bool(scope_settings.get("memory_enabled", True))

        if len(prompt) > self.settings.max_prompt_chars:
            await message.reply("Prompt too long. Please shorten your message.")
            return

        try:
            self.rate_limit_service.check(f"user:{message.author.id}")
            if message.guild:
                self.rate_limit_service.check(f"guild:{message.guild.id}")

            with self.database.session() as session:
                if memory_enabled:
                    self.memory_service.persist_user_message(
                        session,
                        scope,
                        message.author.id,
                        prompt,
                        message.id,
                        {},
                    )
                    recent_turns = self.memory_service.get_recent_turns(session, scope)
                    memories = self.memory_service.get_relevant_memories(session, scope)
                else:
                    recent_turns = []
                    memories = []

            reply = await self.chat_service.generate_reply(prompt, recent_turns, memories)
            logger.info(
                "chat_reply_pre_send",
                reply_length=len(reply),
                prompt_length=len(prompt),
                recent_turn_count=len(recent_turns),
                memory_count=len(memories),
                message_id=message.id,
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
            )
            reply_parts = _split_discord_message(reply)
            sent_messages: list[discord.Message] = [await message.reply(reply_parts[0])]
            for reply_part in reply_parts[1:]:
                sent_messages.append(await message.channel.send(reply_part))

            with self.database.session() as session:
                if memory_enabled:
                    for sent_message, reply_part in zip(sent_messages, reply_parts):
                        self.memory_service.persist_assistant_message(session, scope, reply_part, sent_message.id, {})
                    self.memory_service.maybe_extract_memories(session, scope, prompt)
        except RateLimitExceeded:
            await message.reply("Rate limit exceeded. Please wait a minute and try again.")
        except Exception as exc:
            logger.exception(
                "chat_request_failed",
                error=str(exc),
                prompt_length=len(prompt),
                message_id=message.id,
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
            )
            await message.reply("The bot failed to process the request.")

    def _resolve_scope(self, message: discord.Message) -> ScopeRef:
        if isinstance(message.channel, discord.DMChannel):
            dm_user_id = _resolve_dm_user_id(self.user.id if self.user else None, message.channel, message.author.id)
            return ScopeRef(scope_type=ScopeType.DM, dm_user_id=dm_user_id)

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
            with self.bot.database.session() as session:
                image_url = await self.bot.image_service.generate_image(
                    session,
                    scope,
                    interaction.user.id,
                    prompt,
                    {},
                )
            await interaction.followup.send(image_url)
        except RateLimitExceeded:
            await interaction.followup.send("Rate limit exceeded for image generation.")
        except Exception as exc:
            logger.exception("image_generation_failed", error=str(exc))
            await interaction.followup.send("Image generation failed.")


class VideoCommand:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.command = app_commands.Command(
            name="video",
            description="Generate a video from a prompt.",
            callback=self.video,
        )

    async def video(self, interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(thinking=True)
        scope = self.bot._resolve_interaction_scope(interaction)
        if not self.bot._is_video_allowed(scope):
            await interaction.followup.send("Video generation is not enabled for this scope.")
            return

        try:
            self.bot.rate_limit_service.check(f"user:{interaction.user.id}:video")
            with self.bot.database.session() as session:
                video_result = await self.bot.video_service.generate_video(
                    session,
                    scope,
                    interaction.user.id,
                    prompt,
                    {},
                )
            await self._send_video_result(interaction, video_result)
        except RateLimitExceeded:
            await interaction.followup.send("Rate limit exceeded for video generation.")
        except Exception as exc:
            logger.exception("video_generation_failed", error=str(exc))
            await interaction.followup.send("Video generation failed.")

    async def _send_video_result(self, interaction: discord.Interaction, video_result: VideoGenerationResult) -> None:
        if video_result.has_file and video_result.file_name and video_result.video_bytes:
            discord_file = discord.File(BytesIO(video_result.video_bytes), filename=video_result.file_name)
            await interaction.followup.send(content=video_result.user_message(), file=discord_file)
            return

        await interaction.followup.send(video_result.user_message())


class SpeechCommand:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.command = app_commands.Command(
            name="speech",
            description="Generate speech audio from text.",
            callback=self.speech,
        )

    async def speech(self, interaction: discord.Interaction, text: str) -> None:
        await interaction.response.defer(thinking=True)
        scope = self.bot._resolve_interaction_scope(interaction)
        if not self.bot._is_speech_allowed(scope):
            await interaction.followup.send("Speech generation is not enabled for this scope.")
            return

        try:
            self.bot.rate_limit_service.check(f"user:{interaction.user.id}:speech")
            with self.bot.database.session() as session:
                file_name, audio_bytes = await self.bot.speech_service.generate_speech(
                    session,
                    scope,
                    interaction.user.id,
                    text,
                    {},
                )
            audio_file = discord.File(BytesIO(audio_bytes), filename=file_name)
            await interaction.followup.send(file=audio_file)
        except RateLimitExceeded:
            await interaction.followup.send("Rate limit exceeded for speech generation.")
        except Exception as exc:
            logger.exception("speech_generation_failed", error=str(exc))
            await interaction.followup.send("Speech generation failed.")


class VoiceGroup:
    def __init__(self, bot: AzureDiscordBot) -> None:
        self.bot = bot
        self.group = app_commands.Group(name="voice", description="Manage voice-channel speech chat.")
        self.group.command(name="join", description="Join your current voice channel and start listening.")(self.join)
        self.group.command(name="leave", description="Leave the current voice channel.")(self.leave)
        self.group.command(name="status", description="Show current voice session status.")(self.status)

    async def join(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This command must be used in a server text channel.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        if not self.bot._is_speech_allowed(scope):
            await interaction.response.send_message("Speech generation is not enabled for this scope.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message("Join a voice channel first, then run this command.", ephemeral=True)
            return

        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("This command must be used from a text channel or thread.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        message = await self.bot.voice_chat_service.join(
            guild=interaction.guild,
            voice_channel=interaction.user.voice.channel,
            text_channel=interaction.channel,
            scope=scope,
        )
        await interaction.followup.send(message, ephemeral=True)

    async def leave(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        message = await self.bot.voice_chat_service.leave(interaction.guild.id)
        await interaction.followup.send(message, ephemeral=True)

    async def status(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        await interaction.response.send_message(self.bot.voice_chat_service.get_status(interaction.guild.id), ephemeral=True)


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
            self.bot.memory_service.clear_scope_context(session, scope)
        await interaction.response.send_message("Memories and conversation history cleared for this scope.", ephemeral=True)

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
        self.group.command(name="enable-image", description="Enable image generation in this scope.")(self.enable_image)
        self.group.command(name="disable-image", description="Disable image generation in this scope.")(self.disable_image)
        self.group.command(name="enable-video", description="Enable video generation in this scope.")(self.enable_video)
        self.group.command(name="disable-video", description="Disable video generation in this scope.")(self.disable_video)
        self.group.command(name="enable-speech", description="Enable speech generation in this scope.")(self.enable_speech)
        self.group.command(name="disable-speech", description="Disable speech generation in this scope.")(self.disable_speech)
        self.group.command(name="delete-latest", description="Delete the latest bot chat in this scope.")(self.delete_latest)
        self.group.command(name="delete-message", description="Delete a specific bot chat message by Discord message ID.")(self.delete_message)
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

    async def enable_image(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_image_enabled(session, scope, True)
        await interaction.response.send_message("Image generation enabled for this scope.", ephemeral=True)

    async def disable_image(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_image_enabled(session, scope, False)
        await interaction.response.send_message("Image generation disabled for this scope.", ephemeral=True)

    async def enable_video(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_video_enabled(session, scope, True)
        await interaction.response.send_message("Video generation enabled for this scope.", ephemeral=True)

    async def disable_video(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_video_enabled(session, scope, False)
        await interaction.response.send_message("Video generation disabled for this scope.", ephemeral=True)

    async def enable_speech(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_speech_enabled(session, scope, True)
        await interaction.response.send_message("Speech generation enabled for this scope.", ephemeral=True)

    async def disable_speech(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        with self.bot.database.session() as session:
            self.bot.memory_service.set_scope_speech_enabled(session, scope, False)
        await interaction.response.send_message("Speech generation disabled for this scope.", ephemeral=True)

    async def delete_latest(self, interaction: discord.Interaction) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        logger.info(
            "bot_admin_delete_latest_requested",
            user_id=interaction.user.id,
            scope_type=scope.scope_type.value,
            guild_id=scope.guild_id,
            channel_id=scope.channel_id,
            thread_id=scope.thread_id,
            dm_user_id=scope.dm_user_id,
        )
        with self.bot.database.session() as session:
            record = self.bot.memory_service.get_latest_assistant_message(session, scope)

        if record is None:
            logger.info("bot_admin_delete_latest_no_record", scope_type=scope.scope_type.value)
            await interaction.response.send_message("No bot chat messages were found for this scope.", ephemeral=True)
            return

        logger.info(
            "bot_admin_delete_latest_found_record",
            discord_message_id=record.discord_message_id,
            content_length=len(record.content),
        )
        outcome = await self._delete_message_group_for_scope(interaction, scope, record.discord_message_id)
        await interaction.response.send_message(outcome, ephemeral=True)

    async def delete_message(self, interaction: discord.Interaction, message_id: str) -> None:
        if not self.bot.is_admin(interaction.user.id):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return

        try:
            target_message_id = int(message_id)
        except ValueError:
            logger.info("bot_admin_delete_message_invalid_id", raw_message_id=message_id, user_id=interaction.user.id)
            await interaction.response.send_message("Message ID must be a numeric Discord message ID.", ephemeral=True)
            return

        scope = self.bot._resolve_interaction_scope(interaction)
        logger.info(
            "bot_admin_delete_message_requested",
            user_id=interaction.user.id,
            target_message_id=target_message_id,
            scope_type=scope.scope_type.value,
            guild_id=scope.guild_id,
            channel_id=scope.channel_id,
            thread_id=scope.thread_id,
            dm_user_id=scope.dm_user_id,
        )
        outcome = await self._delete_bot_message_for_scope(interaction, scope, target_message_id)
        await interaction.response.send_message(outcome, ephemeral=True)

    async def _delete_message_group_for_scope(
        self,
        interaction: discord.Interaction,
        scope: ScopeRef,
        latest_discord_message_id: int,
    ) -> str:
        channel = interaction.channel
        if channel is None:
            return "The command must be used from the channel or thread containing the bot reply."

        deleted_any_discord_message = False
        deleted_any_record = False
        deleted_count = 0
        current_message_id = latest_discord_message_id

        while True:
            result = await self._delete_bot_message_for_scope(interaction, scope, current_message_id)
            logger.info(
                "bot_admin_delete_group_iteration",
                current_message_id=current_message_id,
                result=result,
            )

            if result == "Deleted the bot chat message and removed its stored conversation record.":
                deleted_any_discord_message = True
                deleted_any_record = True
                deleted_count += 1
            elif result == "The Discord message was already gone, but its stored conversation record was removed.":
                deleted_any_record = True
                deleted_count += 1
            else:
                break

            previous_message_id = await _find_previous_bot_message_id(channel, current_message_id, self.bot.user.id if self.bot.user else None)
            if previous_message_id is None:
                break
            current_message_id = previous_message_id

        if deleted_any_discord_message and deleted_any_record:
            return f"Deleted {deleted_count} bot chat message(s) and removed their stored conversation records."
        if deleted_any_record:
            return f"The Discord message(s) were already gone, but removed {deleted_count} stored conversation record(s)."
        return "No stored bot chat matched that message ID in this scope."

    async def _delete_bot_message_for_scope(
        self,
        interaction: discord.Interaction,
        scope: ScopeRef,
        discord_message_id: int,
    ) -> str:
        channel = interaction.channel
        if channel is None:
            return "The command must be used from the channel or thread containing the bot reply."

        return await _delete_bot_message_for_scope_from_channel(self.bot, channel, scope, discord_message_id)

    async def help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Mention the bot in approved channels, send direct messages in DMs, or use slash commands like /image, /video, /speech, /voice join, /voice leave, /voice status, /memory inspect, /bot delete-latest, and /bot delete-message.",
            ephemeral=True,
        )


def _split_discord_message(content: str, limit: int = DISCORD_MESSAGE_CHAR_LIMIT) -> list[str]:
    if len(content) <= limit:
        return [content]

    parts: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return [part for part in parts if part]


async def _delete_bot_message_for_scope_from_channel(
    bot: AzureDiscordBot,
    channel: discord.abc.Messageable,
    scope: ScopeRef,
    discord_message_id: int,
) -> str:
    with bot.database.session() as session:
        record = bot.memory_service.get_assistant_message_by_discord_id(session, scope, discord_message_id)

    logger.info(
        "bot_admin_delete_message_lookup",
        scope_type=scope.scope_type.value,
        guild_id=scope.guild_id,
        channel_id=scope.channel_id,
        thread_id=scope.thread_id,
        dm_user_id=scope.dm_user_id,
        discord_message_id=discord_message_id,
        record_found=record is not None,
    )
    if record is None:
        return "No stored bot chat matched that message ID in this scope."

    deleted_discord_message = False
    try:
        target_message = await channel.fetch_message(discord_message_id)
        logger.info(
            "bot_admin_delete_message_fetched",
            fetched_message_author_id=target_message.author.id,
            bot_user_id=bot.user.id if bot.user else None,
            channel_id=channel.id,
        )
        if bot.user is None or target_message.author.id != bot.user.id:
            return "The targeted message is not authored by this bot."
        await target_message.delete()
        deleted_discord_message = True
        logger.info("bot_admin_delete_message_deleted_from_discord", discord_message_id=discord_message_id)
    except discord.NotFound:
        logger.info("bot_admin_delete_message_not_found_in_discord", discord_message_id=discord_message_id)
        deleted_discord_message = False
    except discord.Forbidden:
        logger.warning("bot_admin_delete_message_forbidden", discord_message_id=discord_message_id)
        return "I do not have permission to delete that Discord message."
    except discord.HTTPException as exc:
        logger.exception("bot_admin_delete_message_http_error", discord_message_id=discord_message_id, error=str(exc))
        return "Discord rejected the delete request for that message."

    with bot.database.session() as session:
        deleted_record = bot.memory_service.delete_assistant_message_by_discord_id(
            session,
            scope,
            discord_message_id,
        )

    deleted_user_record = False
    if deleted_record:
        deleted_user_record = await _delete_recent_user_message_for_scope(bot, scope, discord_message_id)

    logger.info(
        "bot_admin_delete_message_record_deleted",
        discord_message_id=discord_message_id,
        deleted_record=deleted_record,
        deleted_discord_message=deleted_discord_message,
        deleted_user_record=deleted_user_record,
    )
    if deleted_discord_message and deleted_record and deleted_user_record:
        return "Deleted the bot chat message and removed its stored conversation record and paired user message record."
    if deleted_discord_message and deleted_record:
        return "Deleted the bot chat message and removed its stored conversation record."
    if deleted_record and deleted_user_record:
        return "The Discord message was already gone, but its stored conversation record and paired user message record were removed."
    if deleted_record:
        return "The Discord message was already gone, but its stored conversation record was removed."
    return "No stored bot chat matched that message ID in this scope."


async def _delete_recent_user_message_for_scope(
    bot: AzureDiscordBot,
    scope: ScopeRef,
    assistant_discord_message_id: int,
) -> bool:
    with bot.database.session() as session:
        recent_messages = bot.memory_service.get_recent_conversation_messages(session, scope, limit=20)
        seen_assistant = False
        target_user_message_id: int | None = None

        for record in recent_messages:
            if not seen_assistant:
                if record.role == "assistant" and record.discord_message_id == assistant_discord_message_id:
                    seen_assistant = True
                continue

            if record.role == "assistant":
                continue

            if record.role == "user" and record.discord_message_id is not None:
                target_user_message_id = record.discord_message_id
                break

        if target_user_message_id is None:
            return False

        return bot.memory_service.delete_message_by_discord_id(session, scope, target_user_message_id)


async def _find_previous_bot_message_id(channel: discord.abc.Messageable, current_message_id: int, bot_user_id: int | None) -> int | None:
    if bot_user_id is None or not hasattr(channel, "history"):
        return None

    async for previous_message in channel.history(limit=10, before=discord.Object(id=current_message_id)):
        if previous_message.author.id == bot_user_id:
            return previous_message.id
    return None


def _resolve_dm_user_id(bot_user_id: int | None, channel: discord.DMChannel, message_author_id: int) -> int:
    recipient = getattr(channel, "recipient", None)
    if recipient is not None:
        return recipient.id

    if bot_user_id is not None and message_author_id == bot_user_id:
        return bot_user_id

    return message_author_id


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


def _is_video_allowed(self: AzureDiscordBot, scope: ScopeRef) -> bool:
    if scope.scope_type == ScopeType.DM:
        return self.settings.allow_dms

    with self.database.session() as session:
        settings = self.memory_service.get_scope_settings(session, scope)
        return bool(settings.get("video_enabled", False))


AzureDiscordBot._is_video_allowed = _is_video_allowed


def _is_speech_allowed(self: AzureDiscordBot, scope: ScopeRef) -> bool:
    if scope.scope_type == ScopeType.DM:
        return self.settings.allow_dms

    with self.database.session() as session:
        settings = self.memory_service.get_scope_settings(session, scope)
        return bool(settings.get("speech_enabled", False))


AzureDiscordBot._is_speech_allowed = _is_speech_allowed
