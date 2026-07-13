import asyncio
import itertools
import logging
import os
import re
import tempfile
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from rapidfuzz import fuzz

from verification import VerificationConfig, VerificationService

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class BotConfig:
    """Load runtime settings from environment variables."""

    def __init__(self) -> None:
        self.bot_token = os.getenv("BOT_TOKEN", "").strip()
        self.guild_id = self._int_env("GUILD_ID")
        self.verify_channel_id = self._int_env("VERIFY_CHANNEL_ID") or self._int_env("VERIFY_CHANNEL")
        self.mod_log_channel_id = self._int_env("MOD_LOG_CHANNEL_ID") or self._int_env("LOG_CHANNEL")
        self.verified_role_id = self._int_env("VERIFIED_ROLE_ID")
        self.verified_role_name = os.getenv("VERIFIED_ROLE_NAME", os.getenv("ROLE_NAME", "Verified")).strip() or "Verified"
        self.timeout_days = self._int_env("TIMEOUT_DAYS", default=7)
        self.youtube_channel_name = os.getenv("YOUTUBE_CHANNEL_NAME", os.getenv("YOUTUBE_CHANNEL", "")).strip()
        self.youtube_channel_handle = os.getenv("YOUTUBE_CHANNEL_HANDLE", "").strip()
        self.youtube_channel_url = os.getenv("YOUTUBE_CHANNEL_URL", "").strip()
        self.gif_url = os.getenv("GIF_URL", "").strip()
        self.success_gif_url = os.getenv("SUCCESS_GIF", self.gif_url).strip()
        self.fail_gif_url = os.getenv("FAIL_GIF", self.gif_url).strip()
        self.developer_name = os.getenv("DEVELOPER_NAME", "Developer").strip() or "Developer"
        self.public_channel_ids = self._list_env("PUBLIC_CHANNEL_IDS")
        self.verification_threshold = self._float_env("VERIFY_THRESHOLD", default=90.0)

    @staticmethod
    def _int_env(name: str, default: Optional[int] = None) -> Optional[int]:
        raw = os.getenv(name, "")
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @staticmethod
    def _float_env(name: str, default: Optional[float] = None) -> Optional[float]:
        raw = os.getenv(name, "")
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @staticmethod
    def _list_env(name: str) -> list[int]:
        raw = os.getenv(name, "")
        if not raw:
            return []
        values: list[int] = []
        for item in raw.split(","):
            item = item.strip()
            if item:
                try:
                    values.append(int(item))
                except ValueError:
                    continue
        return values


class SpamTracker:
    """Track repeated or abusive messages for anti-spam enforcement."""

    def __init__(self, window_seconds: int = 8, max_messages: int = 6) -> None:
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.history: dict[int, deque[dict]] = defaultdict(deque)

    def evaluate(self, user_id: int, content: str) -> tuple[bool, str | None]:
        now = datetime.now(timezone.utc)
        bucket = self.history[user_id]
        while bucket and (now - bucket[0]["time"]).total_seconds() > self.window_seconds:
            bucket.popleft()

        bucket.append({"time": now, "content": content.lower()})

        if len(bucket) >= self.max_messages:
            return True, "Too many messages"
        if len(bucket) >= 2 and bucket[-1]["content"] == bucket[-2]["content"]:
            return True, "Duplicate messages"
        if self._emoji_spam(content):
            return True, "Emoji spam"
        if self._caps_spam(content):
            return True, "Caps spam"
        if content.count("@") >= 3 or "@everyone" in content.lower() or "@here" in content.lower():
            return True, "Mention spam"
        return False, None

    @staticmethod
    def _emoji_spam(content: str) -> bool:
        return sum(1 for char in content if ord(char) > 127) >= 6

    @staticmethod
    def _caps_spam(content: str) -> bool:
        if len(content) < 10:
            return False
        letters = [char for char in content if char.isalpha()]
        if not letters:
            return False
        return sum(1 for char in letters if char.isupper()) / len(letters) >= 0.7


class DiscordBot(commands.Bot):
    """Moderation and screenshot-based verification bot."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.config = config
        self.db_path = BASE_DIR / "bot.db"
        self.spam_tracker = SpamTracker()
        self.verify_channel: Optional[discord.TextChannel] = None
        self.mod_log_channel: Optional[discord.TextChannel] = None
        self.verified_role: Optional[discord.Role] = None
        self.logger = self._setup_logger()
        self.verification_service = VerificationService(
            VerificationConfig(threshold=float(config.verification_threshold or 90.0), reference_dir=BASE_DIR / "reference")
        )
        self.status_cycle = itertools.cycle(
            [
                "🖼️ Image Verification",
                "🔒 Protecting Server",
                "⚡ Anti Spam Enabled",
                "🚫 Blocking Links",
                "👀 Watching Members",
                "🎯 Auto Verification",
                "📺 Reference Matching",
                "💎 Premium Security",
                "🤖 Powered by Python",
                "❤️ Developed by Anupam",
            ]
        )

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger = logging.getLogger("discord_bot")
        logger.setLevel(logging.INFO)
        if logger.handlers:
            return logger
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        file_handler = logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        return logger

    async def setup_hook(self) -> None:
        await self.init_db()

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS verified_users (
                    user_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    verification_method TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS timeouts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    user_id TEXT,
                    channel_id TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.commit()
        self.logger.info("Database initialized")

    async def init_runtime_resources(self) -> None:
        if self.config.bot_token:
            self.logger.info("Bot token loaded")
        if self.config.guild_id:
            guild = self.get_guild(self.config.guild_id) or await self.fetch_guild(self.config.guild_id)
            self.verify_channel = await self.ensure_channel(guild, self.config.verify_channel_id, "youtube-verify")
            self.mod_log_channel = await self.ensure_channel(guild, self.config.mod_log_channel_id, "mod-logs")
            self.verified_role = await self.ensure_role(guild)
            self.logger.info("Runtime channels and role ready")

    async def ensure_channel(self, guild: discord.Guild, channel_id: Optional[int], fallback_name: str) -> discord.TextChannel:
        channel = None
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel is None:
                channel = await guild.fetch_channel(channel_id)
        if channel is None:
            channel = await guild.create_text_channel(
                fallback_name,
                topic="Automated moderation and verification channel",
                reason="Create required moderation channel",
            )
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Channel {channel.name if channel else fallback_name} is not a text channel")
        return channel

    async def ensure_role(self, guild: discord.Guild) -> discord.Role:
        if self.config.verified_role_id:
            role = guild.get_role(self.config.verified_role_id)
            if role is not None:
                return role
        role_name = self.config.verified_role_name or "Verified"
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            role = await guild.create_role(name=role_name, reason="Create verified role")
        self.config.verified_role_id = role.id
        self.config.verified_role_name = role.name
        return role

    @tasks.loop(seconds=10)
    async def change_status(self) -> None:
        status_text = next(self.status_cycle)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status_text))

    @change_status.before_loop
    async def before_change_status(self) -> None:
        await self.wait_until_ready()

    async def on_ready(self) -> None:
        if not self.config.bot_token:
            self.logger.error("BOT_TOKEN is missing")
            return
        await self.init_runtime_resources()
        if not self.change_status.is_running():
            self.change_status.start()
        self.logger.info("Bot is online and ready")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if self.verify_channel and message.channel.id == self.verify_channel.id:
            await self.handle_verification(message)
            return

        if self.should_protect_channel(message.channel):
            await self.handle_public_protection(message)
            return

    async def on_command_error(self, context: commands.Context, exception: Exception) -> None:
        if isinstance(exception, commands.CommandNotFound):
            return
        self.logger.exception("Command error: %s", exception)
        await context.send("An unexpected error occurred while processing that request.")

    async def handle_verification(self, message: discord.Message) -> None:
        if not message.attachments:
            await self.delete_and_report(message, "Please upload exactly one screenshot for verification")
            return

        attachment = message.attachments[0]
        if not self.is_image_attachment(attachment):
            await self.delete_and_report(message, "Only image screenshots are accepted for verification")
            return

        if await self.is_already_verified(message.author):
            await self.delete_and_report(message, "You are already verified")
            return

        temp_path: Optional[Path] = None
        try:
            suffix = Path(attachment.filename or "upload.png").suffix or ".png"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                temp_path = Path(handle.name)
            await attachment.save(temp_path)
            result = self.verification_service.verify_image(temp_path)
            if result.is_match:
                await self.complete_verification(message, result)
            else:
                await self.fail_verification(message, result)
        except FileNotFoundError as exc:
            await self.delete_and_report(message, str(exc))
        except Exception as exc:
            self.logger.exception("Verification failed for %s: %s", message.author, exc)
            await self.fail_verification(message, None)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    async def complete_verification(self, message: discord.Message, result: object) -> None:
        guild = message.guild
        if guild is None:
            return
        role = self.verified_role
        if role is None:
            role = await self.ensure_role(guild)
            self.verified_role = role

        await message.author.add_roles(role, reason="YouTube verification succeeded")
        await self.save_verified_user(message.author)

        success_embed = self._build_success_embed(message.author, role.name, result)
        await self._send_verification_log_embed(
            message.author,
            message.channel,
            success=True,
            reason="Reference image match passed",
            role_name=role.name,
            details=result,
        )
        try:
            await self._send_verification_media_reply(message, success=True)
            await message.reply(embed=success_embed, mention_author=False)
            await self._send_verification_media_to_user(message.author, success=True)
            await self._send_verification_embed_to_user(message.author, success_embed)
        except discord.Forbidden:
            self.logger.warning("Could not reply to verification message from %s", message.author)

    async def fail_verification(self, message: discord.Message, result: object | None) -> None:
        reason = "Verification failed"
        if result is not None:
            reason = f"Score {getattr(result, 'match_score', 0):.2f} / 100 — {getattr(result, 'matched_reference', 'unknown')}"

        failure_embed = self._build_failure_embed(message.author, reason)
        await self._send_verification_log_embed(
            message.author,
            message.channel,
            success=False,
            reason=reason,
            role_name=self.config.verified_role_name,
            details=result,
        )
        try:
            await self._send_verification_media_reply(message, success=False)
            await message.reply(embed=failure_embed, mention_author=False)
            await self._send_verification_media_to_user(message.author, success=False)
            await self._send_verification_embed_to_user(message.author, failure_embed)
        except discord.Forbidden:
            self.logger.warning("Could not reply to failed verification message from %s", message.author)

    async def delete_and_report(self, message: discord.Message, reason: str) -> None:
        embed = discord.Embed(title="⚠️ Verification Notice", description=reason, color=discord.Color.orange())
        embed.set_footer(text=self.config.developer_name)
        try:
            await message.reply(embed=embed, mention_author=False)
        except discord.Forbidden:
            self.logger.warning("Could not reply to verification notice from %s", message.author)

    def is_image_attachment(self, attachment: discord.Attachment) -> bool:
        image_types = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
        return attachment.content_type in image_types or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))

    async def _send_verification_media_reply(self, message: discord.Message, success: bool) -> None:
        env_name = "SUCCESS_MEDIA_URL" if success else "FAIL_MEDIA_URL"
        media_url = os.getenv(env_name, "").strip()
        if media_url:
            await message.reply(media_url, mention_author=False)
            return

        fallback_path = BASE_DIR / ("assets/success.mp4" if success else "assets/fail.mp4")
        if fallback_path.exists():
            await message.reply(file=discord.File(fallback_path), mention_author=False)
            return

    async def _send_verification_media_to_user(self, user: discord.abc.User, success: bool) -> None:
        env_name = "SUCCESS_MEDIA_URL" if success else "FAIL_MEDIA_URL"
        media_url = os.getenv(env_name, "").strip()
        if media_url:
            await user.send(media_url)
            return

        fallback_path = BASE_DIR / ("assets/success.mp4" if success else "assets/fail.mp4")
        if fallback_path.exists():
            await user.send(file=discord.File(fallback_path))
            return

    async def _send_verification_embed_to_user(self, user: discord.abc.User, embed: discord.Embed) -> None:
        try:
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.logger.warning("Could not send DM embed to %s: %s", user, exc)

    def _build_success_embed(self, user: discord.abc.User, role_name: str, result: object) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Verification Successful",
            color=discord.Color.green(),
            description=f"{user.mention}, your verification succeeded.",
        )
        embed.add_field(name="Role Granted", value=role_name, inline=True)
        embed.add_field(name="Detected Type", value=getattr(result, "detected_type", "unknown"), inline=True)
        embed.add_field(name="Reference Image", value=getattr(result, "matched_reference", "unknown"), inline=True)
        embed.add_field(name="Match Score", value=f"{getattr(result, 'match_score', 0):.2f} / 100", inline=True)
        embed.add_field(name="Processing Time", value=f"{getattr(result, 'processing_time_ms', 0):.2f} ms", inline=True)
        embed.add_field(name="Developer", value=self.config.developer_name, inline=False)
        if getattr(user, "display_avatar", None) is not None:
            embed.set_thumbnail(url=user.display_avatar.url)
        media_url = os.getenv("SUCCESS_MEDIA_URL", "").strip()
        if media_url:
            embed.set_image(url=media_url)
        embed.set_footer(text=f"Verified by {self.config.developer_name}")
        return embed

    def _build_failure_embed(self, user: discord.abc.User, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="❌ Verification Failed",
            color=discord.Color.red(),
            description=f"{user.mention}, your verification could not be completed.",
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Tip", value="Upload a clearer full screenshot and make sure it matches the reference layout.", inline=False)
        embed.add_field(name="Developer", value=self.config.developer_name, inline=False)
        if getattr(user, "display_avatar", None) is not None:
            embed.set_thumbnail(url=user.display_avatar.url)
        media_url = os.getenv("FAIL_MEDIA_URL", "").strip()
        if media_url:
            embed.set_image(url=media_url)
        embed.set_footer(text=f"Verification by {self.config.developer_name}")
        return embed

    async def _send_dm_with_fallback(self, user: discord.abc.User, embed: discord.Embed, button_label: Optional[str] = None, button_url: Optional[str] = None) -> None:
        try:
            if button_label and button_url:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label=button_label, url=button_url))
                await user.send(embed=embed, view=view)
            else:
                await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.logger.warning("Could not send DM to %s: %s", user, exc)

    async def _send_verification_log_embed(
        self,
        user: discord.abc.User,
        channel: discord.abc.GuildChannel,
        success: bool,
        reason: str,
        role_name: str,
        details: object | None,
    ) -> None:
        if self.mod_log_channel is None:
            return
        embed = discord.Embed(
            title="✅ Verification Successful" if success else "❌ Verification Failed",
            color=discord.Color.green() if success else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Username", value=str(user), inline=True)
        if success:
            embed.add_field(name="Role Given", value=role_name, inline=True)
        embed.add_field(name="Verification Method", value="Image Matching", inline=True)
        if details is not None:
            embed.add_field(name="Detected Type", value=getattr(details, "detected_type", "unknown"), inline=True)
            embed.add_field(name="Reference", value=getattr(details, "matched_reference", "unknown"), inline=True)
            embed.add_field(name="Match Score", value=f"{getattr(details, 'match_score', 0):.2f} / 100", inline=True)
            embed.add_field(name="Processing Time", value=f"{getattr(details, 'processing_time_ms', 0):.2f} ms", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await self.mod_log_channel.send(embed=embed)

    async def save_verified_user(self, member: discord.Member) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO verified_users (user_id, guild_id, username, verified_at, verification_method) VALUES (?, ?, ?, ?, ?)",
                (str(member.id), str(member.guild.id), str(member), datetime.now(timezone.utc).isoformat(), "image_verification"),
            )
            await db.commit()

    async def remove_verified_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM verified_users WHERE user_id = ?", (str(user_id),))
            await db.commit()

    async def is_already_verified(self, member: discord.Member) -> bool:
        role = self.verified_role
        if role is None:
            role = await self.ensure_role(member.guild)
            self.verified_role = role
        has_role = role in member.roles
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT 1 FROM verified_users WHERE user_id = ?", (str(member.id),))
            row = await cursor.fetchone()
        if row is not None and not has_role:
            await self.remove_verified_user(member.id)
            return False
        return row is not None and has_role

    async def handle_public_protection(self, message: discord.Message) -> None:
        content = message.content or ""
        if message.attachments:
            reason = "Images, videos, GIFs, stickers, files, and voice messages are not allowed in public channels"
            await self.moderate_message(message, reason)
            return
        if message.stickers:
            await self.moderate_message(message, "Stickers are not allowed in public channels")
            return
        if self.contains_link(content):
            await self.moderate_message(message, "Links are not allowed in public channels")
            return
        if self.contains_discord_invite(content):
            await self.moderate_message(message, "Discord invite links are not allowed")
            return
        if self.contains_scam(content):
            await self.moderate_message(message, "Scam content is not allowed")
            return
        spam_detected, spam_reason = self.spam_tracker.evaluate(message.author.id, content)
        if spam_detected:
            await self.moderate_message(message, spam_reason or "Spam detected")
            return

    def should_protect_channel(self, channel: discord.abc.GuildChannel) -> bool:
        if not isinstance(channel, discord.TextChannel):
            return False
        if self.verify_channel and channel.id == self.verify_channel.id:
            return False
        if self.config.public_channel_ids:
            return channel.id in self.config.public_channel_ids
        return True

    @staticmethod
    def contains_link(content: str) -> bool:
        lower = content.lower()
        return any(token in lower for token in ["http://", "https://", "www.", "youtube.com", "youtu.be", "instagram", "facebook", "twitter", "telegram", "whatsapp", "bit.ly", "tinyurl"])

    @staticmethod
    def contains_discord_invite(content: str) -> bool:
        lower = content.lower()
        return "discord.gg" in lower or "discord.com/invite" in lower

    @staticmethod
    def contains_scam(content: str) -> bool:
        lower = content.lower()
        return any(token in lower for token in ["grabify", "iplogger", "boostnitro", "nitro-drop", "free nitro", "steamgift"])

    async def moderate_message(self, message: discord.Message, reason: str) -> None:
        if message.author.guild_permissions.administrator:
            return
        try:
            await message.delete()
        except discord.NotFound:
            return
        await self.timeout_member(message.author, reason)
        await self.log_action(message.author, "Moderation action", message.channel, reason)

    async def timeout_member(self, member: discord.Member, reason: str) -> None:
        if member.bot:
            return
        duration = timedelta(days=self.config.timeout_days)
        until = discord.utils.utcnow() + duration
        try:
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            self.logger.warning("Missing permission to timeout %s", member)
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO timeouts (user_id, guild_id, expires_at, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(member.id), str(member.guild.id), until.isoformat(), reason, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def log_action(self, user: discord.abc.User, action: str, channel: discord.abc.GuildChannel, reason: str) -> None:
        if self.mod_log_channel is None:
            return
        embed = discord.Embed(title="🛡️ Moderation Log", color=discord.Color.dark_red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="User", value=str(user), inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Channel", value=channel.mention if hasattr(channel, "mention") else str(channel), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Moderator", value="Bot", inline=True)
        await self.mod_log_channel.send(embed=embed)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO logs (guild_id, action, user_id, channel_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(getattr(channel, "guild", None).id if getattr(channel, "guild", None) else self.config.guild_id), action, str(user.id), str(getattr(channel, "id", "")), reason, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()


if __name__ == "__main__":
    config = BotConfig()
    if not config.bot_token or not config.guild_id:
        raise SystemExit("Please set BOT_TOKEN and GUILD_ID in the .env file")
    bot = DiscordBot(config)
    bot.run(config.bot_token)
