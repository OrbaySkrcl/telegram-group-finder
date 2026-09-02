"""Nothing here joins anything on its own.

The account only ever joins a channel through an explicit approve+join, so
backfill has to work on channels you are not a member of - and must not pretend
it can live-monitor them, because Telegram only pushes updates for chats you are in.
"""
import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telethon.errors import UserNotParticipantError

from tgfinder.collector import Collector
from tgfinder.db import Database

NOW = int(dt.datetime.now(dt.timezone.utc).timestamp())
CA = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


class FakeEntity:
    id = 555
    username = "publiccalls"
    title = "Public Calls"
    participants_count = 4200


class FakeMessage:
    def __init__(self, msg_id, text, ts):
        self.id = msg_id
        self.message = text
        self.date = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        self.fwd_from = None


class FakeClient:
    def __init__(self, member: bool):
        self.member = member
        self.participant_checks = 0

    async def __call__(self, request):
        self.participant_checks += 1
        if not self.member:
            raise UserNotParticipantError(request=None)
        return object()

    def iter_messages(self, entity, limit=None):
        messages = [FakeMessage(1, f"call: {CA}", NOW - 3600),
                    FakeMessage(2, "gm", NOW - 7200)]

        async def gen():
            for msg in messages:
                yield msg
        return gen()


def run_backfill(member: bool):
    db = Database(":memory:")
    collector = Collector(db, FakeClient(member))
    info = asyncio.run(collector.backfill(FakeEntity(), days=30))
    row = db.one("SELECT monitored FROM channels WHERE tg_id=555")
    return info, row["monitored"], db


def test_history_of_a_channel_we_never_joined_is_still_collected():
    info, monitored, db = run_backfill(member=False)
    assert info["member"] is False
    assert info["messages"] == 2
    assert info["new_calls"] == 1
    # Read, recorded, scorable - but not marked for live monitoring, because
    # no new messages would ever arrive for it.
    assert monitored == 0
    assert db.one("SELECT COUNT(*) n FROM calls")["n"] == 1


def test_a_channel_we_are_in_is_monitored_live():
    info, monitored, _db = run_backfill(member=True)
    assert info["member"] is True
    assert monitored == 1
