"""Telethon client construction shared by every command."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from .config import Config


def build_client(cfg: Config) -> TelegramClient:
    if not cfg.api_id or not cfg.api_hash:
        raise SystemExit("TG_API_ID / TG_API_HASH is missing. See .env.example.")
    if not cfg.session:
        raise SystemExit("TG_SESSION is missing. Run: python login.py")
    return TelegramClient(StringSession(cfg.session), cfg.api_id, cfg.api_hash)


def channel_identity(entity) -> tuple[int, str | None, str | None, int | None]:
    """(tg_id, username, title, member_count) for a channel/chat entity."""
    return (
        int(entity.id),
        getattr(entity, "username", None),
        getattr(entity, "title", None),
        getattr(entity, "participants_count", None),
    )
