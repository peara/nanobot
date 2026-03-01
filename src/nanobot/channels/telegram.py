from __future__ import annotations

import re

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from nanobot.channels.base import Channel, IncomingMessage


class TelegramChannel(Channel):
    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.app: Application | None = None

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.effective_user is None or update.message is None:
            return
        text = update.message.text or ""
        if not text.strip():
            return
        await self.emit(
            IncomingMessage(
                channel="telegram",
                chat_id=str(update.effective_chat.id),
                user_id=str(update.effective_user.id),
                text=text,
            )
        )

    async def start(self) -> None:
        app = ApplicationBuilder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        self.app = app

    async def stop(self) -> None:
        if self.app is None:
            return
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        self.app = None

    async def send(self, chat_id: str, text: str) -> None:
        if self.app is None:
            raise RuntimeError("Telegram channel not started.")
        normalized = self._normalize_for_telegram(text)
        for chunk in self._chunk_text(normalized):
            await self.app.bot.send_message(chat_id=chat_id, text=chunk)

    def _normalize_for_telegram(self, text: str) -> str:
        # Convert common HTML line breaks produced by models.
        normalized = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
        # Strip any remaining HTML tags.
        normalized = re.sub(r"<[^>]+>", "", normalized)

        lines: list[str] = []
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue

            # Remove markdown table separators such as |---|---|.
            if re.match(r"^\|?[\-\s:|]+\|[\-\s:|]+\|?$", line):
                continue

            # Flatten markdown tables to plain text lines.
            if line.count("|") >= 2:
                cells = [c.strip() for c in line.strip("|").split("|")]
                cells = [c for c in cells if c]
                if cells:
                    line = " - ".join(cells)

            # Remove markdown emphasis and inline code markers.
            line = re.sub(r"(\*\*|__|\*|_|`)", "", line)
            line = line.replace("•", "-")
            lines.append(line.strip())

        normalized = "\n".join(lines)
        # Collapse excess blank lines.
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        return normalized or " "

    def _chunk_text(self, text: str, limit: int = 3900) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, limit)
            if split_at < int(limit * 0.5):
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return chunks
