"""The Telegram control chat is the only interface a non-technical user touches,
so its routing and message splitting are worth pinning down."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tgfinder.config import Config
from tgfinder.control import Control, _chunks, _clean_handle
from tgfinder.db import Database


class FakeClient:
    def __init__(self):
        self.sent = []
        self._next_id = 1000

    async def send_message(self, chat, text, **kwargs):
        self.sent.append(text)
        self._next_id += 1
        return type("Msg", (), {"id": self._next_id})()


class FakeCollector:
    monitored_count = 3

    def refresh_monitored(self, force=False):
        pass


def make_control():
    db = Database(":memory:")
    cfg = Config(api_id=1, api_hash="x", session="x", db_path=":memory:",
                 entry_delay_sec=60, slippage=0.03, tp_multiple=2.0, sl_drop=0.5,
                 horizon_hours=24, window_days=30, min_calls=5,
                 report_chat="me", report_hour_utc=6, max_joins_per_day=8)
    client = FakeClient()
    control = Control(db, client, market=None, cfg=cfg,
                      collector=FakeCollector(), tracker=None)
    control.chat_id = 42
    return control, client, db


def test_long_output_is_split_on_line_boundaries():
    text = "\n".join(f"row {i:04d} " + "x" * 80 for i in range(200))
    parts = _chunks(text, size=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == text
    # no row may be cut in half
    for part in parts:
        for line in part.splitlines():
            assert line == "" or line.startswith("row ")


def test_a_single_oversized_line_still_fits():
    parts = _chunks("y" * 5000, size=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == "y" * 5000


def test_handles_are_accepted_in_every_shape_a_user_might_paste():
    for raw in ("@gemcalls", "gemcalls", "t.me/gemcalls",
                "https://t.me/gemcalls", "https://t.me/gemcalls?start=1",
                " @gemcalls,"):
        assert _clean_handle(raw) == "gemcalls"


def test_unknown_command_gets_a_pointer_not_silence():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("nonsense"))
    assert "Bilinmeyen komut" in client.sent[0]
    assert "/help" in client.sent[0]


def test_status_reports_the_simulation_rule_in_use():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("status"))
    body = client.sent[0]
    assert "izlenen kanal" in body
    assert "2x sat" in body and "-%50 kes" in body and "24s takip" in body


def test_score_with_no_data_explains_what_to_do_next():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("score"))
    assert "/backfill" in client.sent[0]


def test_detail_on_an_unknown_channel_is_a_hint_not_a_crash():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("detail @nope"))
    assert "kayıtlı değil" in client.sent[0]


def test_the_bot_ignores_its_own_messages():
    """Replies land back in Saved Messages as new events; re-parsing them would
    make the service talk to itself."""
    control, client, _db = make_control()
    asyncio.run(control.send("/help sample reply"))
    sent_id = max(control._own_messages)

    event = type("Event", (), {
        "chat_id": 42,
        "message": type("M", (), {"id": sent_id, "message": "/help sample reply"})(),
    })()
    before = len(client.sent)
    asyncio.run(control._on_message(event))
    assert len(client.sent) == before


def test_messages_from_other_chats_are_ignored():
    control, client, _db = make_control()
    event = type("Event", (), {
        "chat_id": 999,
        "message": type("M", (), {"id": 1, "message": "/status"})(),
    })()
    asyncio.run(control._on_message(event))
    assert client.sent == []


def test_backfill_without_a_channel_name_explains_the_usage():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("backfill"))
    assert "Kullanım" in client.sent[0]


def test_chains_command_reports_coverage_per_chain():
    control, client, db = make_control()
    db.upsert_channel(1, "c", "C", 10, 1)
    for chain, market_chain, address, status in (
        ("solana", "solana", "a1", "done"),
        ("solana", "solana", "a2", "done"),
        ("evm", "bsc", "b1", "done"),
        ("evm", "bsc", "b2", "pending"),
        ("evm", "brandnew", "c1", "nochain"),
    ):
        db.execute("INSERT OR IGNORE INTO tokens(chain,address,market_chain,"
                   "resolve_status) VALUES(?,?,?,'ok')", (chain, address, market_chain))
        db.execute("INSERT INTO calls(channel_id,chain,address,ts,status) "
                   "VALUES(1,?,?,1,?)", (chain, address, status))

    asyncio.run(control.dispatch("chains"))
    body = client.sent[0]
    assert "solana" in body and "bsc" in body and "brandnew" in body
    # bsc: 2 calls, 1 scored, 1 pending
    bsc_row = [ln for ln in body.splitlines() if ln.startswith("bsc")][0].split()
    assert bsc_row[1:5] == ["2", "1", "1", "0"]
    assert "DESTEKSİZ" in body


def test_chains_command_with_no_data_points_at_backfill():
    control, client, _db = make_control()
    asyncio.run(control.dispatch("chains"))
    assert "/backfill" in client.sent[0]
