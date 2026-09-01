"""Telethon client construction shared by every command."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from .config import Config


def build_client(cfg: Config) -> TelegramClient:
    missing = [name for name, value in (("TG_API_ID", cfg.api_id),
                                        ("TG_API_HASH", cfg.api_hash),
                                        ("TG_SESSION", cfg.session)) if not value]
    if missing:
        raise SystemExit(
            "\n" + "=" * 68 + "\n"
            "  tgfinder cannot start: missing variable(s): " + ", ".join(missing) + "\n"
            + "=" * 68 + "\n"
            "  1. TG_API_ID and TG_API_HASH come from https://my.telegram.org\n"
            "     -> API development tools -> create an application.\n"
            "  2. TG_SESSION is a login token you generate once. If you do not\n"
            "     have Python installed, open login_colab.ipynb from this repo in\n"
            "     Google Colab and run it in your browser - see README.md.\n"
            "  3. Add all three in Railway under Variables, then redeploy.\n"
            "     Also mount a Volume at /data, or every redeploy wipes the data.\n"
            + "=" * 68)
    return TelegramClient(StringSession(cfg.session), cfg.api_id, cfg.api_hash)


def channel_identity(entity) -> tuple[int, str | None, str | None, int | None]:
    """(tg_id, username, title, member_count) for a channel/chat entity."""
    return (
        int(entity.id),
        getattr(entity, "username", None),
        getattr(entity, "title", None),
        getattr(entity, "participants_count", None),
    )
