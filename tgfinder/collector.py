"""Read channel messages, turn contract addresses into timestamped calls."""
from __future__ import annotations

import logging
import time

from telethon import events, functions, utils
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.tl.types import PeerChannel, PeerUser

from .extract import extract_addresses, extract_handles

log = logging.getLogger("tgfinder.collector")

# A message listing several addresses at once is a recap or portfolio dump, not a
# call. Real calls carry one address; two or three can be a genuine comparison.
MAX_ADDRESSES_PER_CALL_MESSAGE = 3


class Collector:
    def __init__(self, db, client):
        self.db = db
        self.client = client
        self._monitored: dict[int, int] = {}   # telegram id -> channels.id
        self._refreshed_at = 0.0

    # ---- monitored channel cache -----------------------------------------

    def refresh_monitored(self, force: bool = False) -> None:
        if not force and time.time() - self._refreshed_at < 300:
            return
        rows = self.db.query("SELECT id, tg_id FROM channels WHERE monitored = 1")
        self._monitored = {int(r["tg_id"]): int(r["id"]) for r in rows}
        self._refreshed_at = time.time()

    def channel_row_id(self, tg_id: int) -> int | None:
        self.refresh_monitored()
        return self._monitored.get(int(tg_id))

    @property
    def monitored_count(self) -> int:
        return len(self._monitored)

    # ---- ingestion --------------------------------------------------------

    def store_message(self, channel_id: int, msg) -> int | None:
        """Persist a message and return its row id (None if already stored)."""
        fwd = _forward_source(msg)
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages(channel_id, tg_msg_id, ts, text, fwd_from) "
            "VALUES(?,?,?,?,?)",
            (channel_id, int(msg.id), int(msg.date.timestamp()), msg.message or "", fwd),
        )
        if cur.rowcount == 0:
            return None
        if fwd:
            self.db.execute(
                "INSERT INTO forward_edges(src_handle, dst_channel, count) VALUES(?,?,1) "
                "ON CONFLICT(src_handle, dst_channel) DO UPDATE SET count = count + 1",
                (fwd.lower(), channel_id),
            )
            self.db.add_candidate(fwd, False, "forward", int(time.time()), forwards=1)
        return int(cur.lastrowid)

    def record_calls(self, channel_id: int, message_row_id: int | None, msg) -> list[int]:
        """Create call rows for the addresses in a message. Returns new call ids."""
        text = msg.message or ""
        addresses = extract_addresses(text)
        if not addresses or len(addresses) > MAX_ADDRESSES_PER_CALL_MESSAGE:
            return []

        ts = int(msg.date.timestamp())
        new_ids: list[int] = []
        for addr in addresses:
            self.db.execute(
                "INSERT OR IGNORE INTO tokens(chain, address) VALUES(?,?)",
                (addr.chain, addr.address),
            )
            cur = self.db.execute(
                "INSERT OR IGNORE INTO calls(channel_id, message_id, chain, address, ts) "
                "VALUES(?,?,?,?,?)",
                (channel_id, message_row_id, addr.chain, addr.address, ts),
            )
            if cur.rowcount:
                new_ids.append(int(cur.lastrowid))
        return new_ids

    def record_handles(self, msg) -> None:
        """Every t.me link inside a monitored channel is a discovery lead."""
        now = int(time.time())
        for handle in extract_handles(msg.message or ""):
            self.db.add_candidate(handle.name, handle.is_invite, "mention", now, mentions=1)

    # ---- live listener ----------------------------------------------------

    def attach(self) -> None:
        @self.client.on(events.NewMessage())
        async def _handler(event):  # pragma: no cover - needs a live connection
            try:
                await self.handle_event(event)
            except Exception:
                log.exception("failed to handle message")

    async def handle_event(self, event) -> None:
        chat_id = getattr(event, "chat_id", None)
        if chat_id is None:
            return
        # Telethon marks peer ids (-100... for channels); we store the raw id.
        raw_id, _peer_type = utils.resolve_id(int(chat_id))
        channel_id = self.channel_row_id(raw_id)
        if channel_id is None:
            return

        msg = event.message
        message_row_id = self.store_message(channel_id, msg)
        if message_row_id is None:
            return
        self.record_handles(msg)
        call_ids = self.record_calls(channel_id, message_row_id, msg)
        if call_ids:
            log.info("channel %s -> %d new call(s)", channel_id, len(call_ids))

    # ---- historical backfill ---------------------------------------------

    async def is_member(self, entity) -> bool | None:
        """True/False if we can tell, None if Telegram will not say."""
        try:
            await self.client(functions.channels.GetParticipantRequest(entity, "me"))
            return True
        except UserNotParticipantError:
            return False
        except Exception:
            return None

    async def backfill(self, entity, days: int, limit: int = 20000) -> dict:
        """Replay a channel's recent history so it can be scored without waiting.

        Reading a public channel's history does not require joining it, so a
        channel can be judged before you ever appear in its member list. Live
        monitoring is the part that needs membership: Telegram only pushes new
        messages for chats you are in.
        """
        from .tgclient import channel_identity

        tg_id, username, title, members = channel_identity(entity)
        now = int(time.time())
        member = await self.is_member(entity)
        # Only start live-listening to channels we are actually in; otherwise the
        # channel is recorded and scored from history but not marked monitored.
        channel_id = self.db.upsert_channel(tg_id, username, title, members, now,
                                            monitored=1 if member else 0)
        self.refresh_monitored(force=True)

        cutoff = now - days * 86400
        seen = calls = 0
        truncated = False
        try:
            async for msg in self.client.iter_messages(entity, limit=limit):
                if int(msg.date.timestamp()) < cutoff:
                    break
                seen += 1
                row_id = self.store_message(channel_id, msg)
                if row_id is None:
                    continue
                self.record_handles(msg)
                calls += len(self.record_calls(channel_id, row_id, msg))
            else:
                # The iterator ran out before reaching the cutoff date.
                truncated = seen >= limit
        except FloodWaitError as exc:
            # Telegram is asking us to slow down. Keep whatever we already read
            # rather than throwing the whole backfill away.
            log.warning("flood wait %ss during backfill of %s", exc.seconds, username)
            return {"channel_id": channel_id, "title": title, "username": username,
                    "messages": seen, "new_calls": calls, "member": member,
                    "truncated": True, "flood_wait": exc.seconds}

        return {"channel_id": channel_id, "title": title, "username": username,
                "messages": seen, "new_calls": calls, "member": member,
                "truncated": truncated, "flood_wait": 0}


def _forward_source(msg) -> str | None:
    """Username of the channel a message was forwarded from, when knowable."""
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return None
    name = getattr(fwd, "from_name", None)
    peer = getattr(fwd, "from_id", None)
    if isinstance(peer, PeerChannel):
        return f"id:{peer.channel_id}"
    if isinstance(peer, PeerUser):
        return f"user:{peer.user_id}"
    return name
