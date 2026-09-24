from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from loguru import logger

from connectors.base import (
    BaseConnector,
    Document,
    Settings,
    SourceConfig,
    SourceType,
)

try:
    from telethon import TelegramClient
    from telethon.tl.types import (
        Channel,
        Chat,
        Message,
        MessageEntityBold,
        MessageEntityCode,
        MessageEntityHashtag,
        MessageEntityItalic,
        MessageEntityMention,
        MessageEntityPre,
        MessageEntityTextUrl,
        MessageEntityUrl,
        MessageMediaContact,
        MessageMediaDocument,
        MessageMediaGeo,
        MessageMediaPhoto,
        MessageMediaPoll,
        MessageMediaVenue,
        MessageMediaWebPage,
        User,
    )
    _TELETHON_AVAILABLE = True
except ImportError:
    _TELETHON_AVAILABLE = False


_DEFAULT_SESSION = "./telegram_session"
_DEFAULT_LIMIT   = 500
_DEFAULT_BATCH   = 200


class TelegramConnector(BaseConnector):
    """
    Connects to a real Telegram account via Telethon MTProto API
    and indexes messages from all dialogs (private chats, groups, channels).

    Prerequisites:
        1. pip install telethon
        2. Create an app at https://my.telegram.org/apps
        3. Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables
           (or configure them in config.yaml under sources.chats)
        4. On first run, a phone-number/OTP login flow runs in the terminal.
           The session is saved to TELEGRAM_SESSION_PATH and reused afterwards.

    config.yaml example:
        sources:
          chats:
            enabled: true
            paths: []
            extensions: []
            recursive: false
            extra:
              api_id: 12345678
              api_hash: "0abc1def2..."
              session_path: "./telegram_session"
              limit_per_dialog: 500
              batch_size: 200
              dialog_types:
                - user
                - group
                - channel
    """

    SOURCE_TYPE = SourceType.CHATS

    def __init__(self, settings: Settings):
        super().__init__(settings)

        self._api_id: int = int(
            os.getenv("TELEGRAM_API_ID", "0")
            or _extra(settings, "api_id", "0")
        )
        self._api_hash: str = (
            os.getenv("TELEGRAM_API_HASH", "")
            or _extra(settings, "api_hash", "")
        )
        self._session_path: str = (
            os.getenv("TELEGRAM_SESSION_PATH", "")
            or _extra(settings, "session_path", _DEFAULT_SESSION)
        )
        self._limit: int = int(
            _extra(settings, "limit_per_dialog", str(_DEFAULT_LIMIT))
        )
        self._batch: int = int(
            _extra(settings, "batch_size", str(_DEFAULT_BATCH))
        )
        self._dialog_types: set[str] = set(
            _extra_list(settings, "dialog_types", ["user", "group", "channel"])
        )

    def can_handle(self, path: Path) -> bool:
        return False

    def fetch(
        self,
        source_cfg: Optional[SourceConfig] = None,
    ) -> Generator[Document, None, None]:
        if not _TELETHON_AVAILABLE:
            raise RuntimeError(
                "Telethon is not installed.\n"
                "Run: pip install telethon"
            )

        if not self._api_id or not self._api_hash:
            raise ValueError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set.\n"
                "Get them from https://my.telegram.org/apps"
            )

        yield from asyncio.get_event_loop().run_until_complete(
            self._fetch_all()
        )

    async def _fetch_all(self) -> list[Document]:
        session = str(Path(self._session_path).expanduser())
        docs: list[Document] = []

        async with TelegramClient(session, self._api_id, self._api_hash) as client:
            logger.info("Connected to Telegram.")
            me = await client.get_me()
            my_id = me.id if me else None

            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                dialog_type = _classify_entity(entity)

                if dialog_type not in self._dialog_types:
                    logger.debug(
                        f"Skipping dialog '{dialog.name}' "
                        f"(type={dialog_type}, not in filter)"
                    )
                    continue

                logger.info(
                    f"Fetching '{dialog.name}' "
                    f"[{dialog_type}] …"
                )

                messages: list[str] = []
                first_date: Optional[datetime] = None
                last_date:  Optional[datetime] = None
                participants: set[str] = set()

                async for msg in client.iter_messages(
                    entity,
                    limit=self._limit,
                    reverse=True,
                ):
                    if not isinstance(msg, Message):
                        continue

                    text = _extract_text(msg)
                    if not text:
                        continue

                    sender_name = await _resolve_sender_name(
                        client, msg, my_id
                    )

                    date = _normalise_date(msg.date)

                    if first_date is None:
                        first_date = date
                    last_date = date

                    if sender_name:
                        participants.add(sender_name)

                    timestamp = (
                        date.strftime("%Y-%m-%d %H:%M")
                        if date else "?"
                    )
                    line = f"[{timestamp}] {sender_name}: {text}" if sender_name else f"[{timestamp}] {text}"
                    messages.append(line)

                if not messages:
                    logger.debug(f"No messages in '{dialog.name}'.")
                    continue

                content = "\n".join(messages)
                dialog_id = _dialog_id(entity)

                doc = self._make_document(
                    content=content,
                    source_path=f"telegram://{dialog_type}/{dialog_id}",
                    title=dialog.name or str(dialog_id),
                    created_at=first_date,
                    modified_at=last_date,
                    extra={
                        "provider": "telegram",
                        "dialog_type": dialog_type,
                        "dialog_id": dialog_id,
                        "participants": sorted(participants),
                        "message_count": len(messages),
                    },
                )
                docs.append(doc)
                logger.info(
                    f"'{dialog.name}' → {len(messages)} messages collected."
                )

        return docs


def _extra(settings: Settings, key: str, default: str) -> str:
    cfg = settings.get_source("chats")
    if cfg is None:
        return default
    return str(cfg.extra.get(key, default))


def _extra_list(
    settings: Settings, key: str, default: list[str]
) -> list[str]:
    cfg = settings.get_source("chats")
    if cfg is None:
        return default
    val = cfg.extra.get(key, default)
    return val if isinstance(val, list) else default


def _classify_entity(entity) -> str:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "channel" if not entity.megagroup else "group"
    return "unknown"


def _dialog_id(entity) -> int:
    return getattr(entity, "id", 0)


def _normalise_date(date) -> Optional[datetime]:
    if date is None:
        return None
    if date.tzinfo is None:
        return date.replace(tzinfo=timezone.utc)
    return date


async def _resolve_sender_name(
    client: "TelegramClient",
    msg: "Message",
    my_id: Optional[int],
) -> str:
    try:
        sender = await msg.get_sender()
    except Exception:
        return ""

    if sender is None:
        return ""

    if isinstance(sender, User):
        if sender.id == my_id:
            return "me"
        parts = [
            p for p in [sender.first_name, sender.last_name] if p
        ]
        return " ".join(parts) or sender.username or str(sender.id)

    if isinstance(sender, (Chat, Channel)):
        return sender.title or str(sender.id)

    return str(getattr(sender, "id", ""))


def _extract_text(msg: "Message") -> str:
    parts: list[str] = []

    raw = (msg.message or "").strip()
    if raw:
        parts.append(raw)

    media_label = _media_label(msg)
    if media_label:
        parts.append(media_label)

    return "\n".join(parts)


def _media_label(msg: "Message") -> str:
    media = msg.media
    if media is None:
        return ""

    if isinstance(media, MessageMediaPhoto):
        caption = (msg.message or "").strip()
        return f"[photo: {caption}]" if caption else "[photo]"

    if isinstance(media, MessageMediaDocument):
        doc = media.document
        mime = getattr(doc, "mime_type", "") or ""
        caption = (msg.message or "").strip()

        if mime.startswith("video/"):
            return f"[video: {caption}]" if caption else "[video]"
        if mime.startswith("audio/"):
            return f"[audio: {caption}]" if caption else "[audio]"
        if mime == "image/gif" or getattr(msg, "gif", None):
            return "[gif]"
        if getattr(msg, "sticker", None):
            emoji = getattr(msg.sticker, "emoji", "") or ""
            return f"[sticker {emoji}]".strip()
        if getattr(msg, "voice", None):
            return "[voice message]"
        if getattr(msg, "video_note", None):
            return "[video note]"

        file_name = ""
        for attr in getattr(doc, "attributes", []):
            name = getattr(attr, "file_name", None)
            if name:
                file_name = name
                break

        label = f"[file: {file_name}]" if file_name else "[file]"
        return f"{label} {caption}".strip() if caption else label

    if isinstance(media, MessageMediaWebPage):
        page = media.webpage
        url = getattr(page, "url", "") or ""
        title = getattr(page, "title", "") or ""
        text = (msg.message or "").strip()
        pieces = [p for p in [text, title, url] if p]
        return " | ".join(pieces) if pieces else "[link]"

    if isinstance(media, MessageMediaPoll):
        poll = media.poll
        question = getattr(
            poll.question, "text", str(poll.question)
        ) if poll.question else ""
        answers = []
        for ans in (poll.answers or []):
            ans_text = getattr(ans.text, "text", str(ans.text)) if ans.text else ""
            answers.append(ans_text)
        options = ", ".join(answers)
        return f"[poll: {question} | options: {options}]"

    if isinstance(media, MessageMediaContact):
        name = " ".join(
            p for p in [media.first_name, media.last_name] if p
        )
        phone = media.phone_number or ""
        return f"[contact: {name} {phone}]".strip()

    if isinstance(media, MessageMediaGeo):
        geo = media.geo
        lat = getattr(geo, "lat", "?")
        lon = getattr(geo, "long", "?")
        return f"[location: {lat}, {lon}]"

    if isinstance(media, MessageMediaVenue):
        title = media.title or ""
        address = media.address or ""
        return f"[venue: {title}, {address}]"

    return ""
