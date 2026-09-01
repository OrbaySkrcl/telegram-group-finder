"""Grow the candidate pool: keyword search, mentions, and the forward graph."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from telethon import functions
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel

from .tgclient import channel_identity

log = logging.getLogger("tgfinder.discovery")

DEFAULT_KEYWORDS = [
    "solana calls", "sol gems", "alpha calls", "degen calls", "memecoin calls",
    "early calls", "gem finder", "insider calls", "pumpfun calls", "runner calls",
]


async def search_public_channels(client, keywords: list[str], limit: int = 30) -> list[dict]:
    """Telegram's own global directory search - free, no scraping required."""
    out: dict[str, dict] = {}
    for keyword in keywords:
        try:
            result = await client(functions.contacts.SearchRequest(q=keyword, limit=limit))
        except FloodWaitError as exc:
            log.warning("flood wait %ss on search %r", exc.seconds, keyword)
            await asyncio.sleep(exc.seconds + 1)
            continue
        except Exception:
            log.exception("search failed for %r", keyword)
            continue

        for chat in result.chats:
            if not isinstance(chat, Channel) or not chat.username:
                continue
            out.setdefault(chat.username.lower(), {
                "handle": chat.username,
                "title": chat.title,
                "keyword": keyword,
            })
        await asyncio.sleep(1.5)
    return list(out.values())


def record_search_results(db, results: list[dict]) -> int:
    now = int(time.time())
    for item in results:
        db.add_candidate(item["handle"], False, f"search:{item['keyword']}", now)
    return len(results)


def rank_candidates(db, limit: int = 50, status: str = "new") -> list[dict]:
    """Leads worth a human glance, most-corroborated first.

    A handle that several monitored channels forward from is a far stronger lead
    than one that appeared once in a promo blast, so forwards weigh more heavily.
    """
    rows = db.query(
        """
        SELECT c.handle, c.is_invite, c.source, c.mentions, c.forwards,
               c.first_seen_at, c.last_seen_at, c.status,
               (SELECT COUNT(DISTINCT dst_channel) FROM forward_edges f
                 WHERE f.src_handle = c.handle) AS distinct_forwarders
          FROM candidates c
         WHERE (? = 'all' OR c.status = ?)
           AND c.handle NOT IN (SELECT lower(username) FROM channels
                                 WHERE username IS NOT NULL)
         ORDER BY (c.forwards * 3 + c.mentions) DESC, c.last_seen_at DESC
         LIMIT ?
        """,
        (status, status, limit),
    )
    return [dict(r) for r in rows]


def set_status(db, handle: str, status: str) -> None:
    db.execute("UPDATE candidates SET status=? WHERE handle=?", (status.lower(), handle.lower()))


async def join_approved(db, client, max_joins: int) -> list[str]:
    """Join approved candidates, respecting a self-imposed daily cap.

    Telegram limits how fast an account may join channels, and tripping that limit
    can cost the account. The cap is deliberately conservative.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"joins:{today}"
    used = int(db.get_state(key, "0") or 0)
    budget = max(0, max_joins - used)
    if budget == 0:
        log.info("daily join budget already spent (%d)", max_joins)
        return []

    rows = db.query(
        "SELECT handle, is_invite FROM candidates WHERE status='approved' LIMIT ?",
        (budget,),
    )
    joined: list[str] = []
    now = int(time.time())

    for row in rows:
        handle = row["handle"]
        try:
            if row["is_invite"]:
                updates = await client(functions.messages.ImportChatInviteRequest(handle))
                entity = updates.chats[0] if getattr(updates, "chats", None) else None
            else:
                entity = await client.get_entity(handle)
                await client(functions.channels.JoinChannelRequest(entity))
        except FloodWaitError as exc:
            log.warning("flood wait %ss while joining - stopping for now", exc.seconds)
            break
        except Exception as exc:
            log.warning("could not join %s: %s", handle, exc)
            set_status(db, handle, "rejected")
            continue

        if entity is not None:
            tg_id, username, title, members = channel_identity(entity)
            db.upsert_channel(tg_id, username, title, members, now)
        set_status(db, handle, "joined")
        joined.append(handle)
        used += 1
        db.set_state(key, str(used))
        # Space joins out; bursts are what gets accounts limited.
        await asyncio.sleep(30)

    return joined
