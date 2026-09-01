"""End-to-end: raw messages in, ranked leaderboard out, with market data faked."""
import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tgfinder.collector import Collector
from tgfinder.config import Config
from tgfinder.db import Database
from tgfinder.prices import PairInfo
from tgfinder.scoring import compute_stats
from tgfinder.tracker import Tracker

NOW = int(dt.datetime.now(dt.timezone.utc).timestamp())
CALL_TS = NOW - 3 * 86400

WINNER = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
LOSER = "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E"
GHOST = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
EVM_TOKEN = "0x6982508145454ce325ddbe47a25d4ec3d2311933"


class FakeMarket:
    """Deterministic stand-in for DexScreener + GeckoTerminal."""

    def __init__(self):
        self.pairs = {
            WINNER: PairInfo("solana", "poolW", WINNER, "WIN", "Winner",
                             0.001, 90_000, 40_000, CALL_TS - 600),
            LOSER: PairInfo("solana", "poolL", LOSER, "LOSE", "Loser",
                            0.002, 120_000, 30_000, CALL_TS - 900),
            # An EVM token: extraction calls the chain "evm", the market calls it
            # "base". Both names have to survive into the scoring join.
            EVM_TOKEN: PairInfo("base", "poolE", EVM_TOKEN, "EVM", "Evm Token",
                                0.5, 300_000, 80_000, CALL_TS - 1200),
        }
        # WINNER doubles; LOSER goes to zero.
        self.curves = {"poolW": [1.0, 1.0, 1.4, 2.5], "poolL": [1.0, 1.0, 0.6, 0.02],
                       "poolE": [1.0, 1.0, 1.3, 2.2]}

    async def lookup_tokens(self, addresses):
        return {a: self.pairs[a] for a in addresses if a in self.pairs}

    async def search(self, address):
        return self.pairs.get(address)

    async def candles_covering(self, chain, pair_address, start_ts, end_ts):
        prices = self.curves.get(pair_address)
        if not prices:
            return [], "none"
        return [(start_ts + i * 60, p, p, p, p, 1.0) for i, p in enumerate(prices)], "minute"


class FakeMessage:
    def __init__(self, msg_id, text, ts):
        self.id = msg_id
        self.message = text
        self.date = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        self.fwd_from = None


def make_cfg(db_path):
    return Config(api_id=1, api_hash="x", session="x", db_path=db_path,
                  entry_delay_sec=60, slippage=0.0, tp_multiple=2.0, sl_drop=0.5,
                  horizon_hours=24, window_days=30, min_calls=2,
                  report_chat="me", report_hour_utc=6, max_joins_per_day=8)


def run_pipeline():
    db = Database(":memory:")
    cfg = make_cfg(":memory:")
    market = FakeMarket()
    collector = Collector(db, client=None)

    good = db.upsert_channel(111, "goodcalls", "Good Calls", 1200, NOW)
    noisy = db.upsert_channel(222, "noisycalls", "Noisy Calls", 90_000, NOW)

    messages = [
        (good, FakeMessage(1, f"early entry, dev doxxed\nCA: {WINNER}", CALL_TS)),
        (good, FakeMessage(2, f"small size here {LOSER}", CALL_TS + 120)),
        # Noisy repeats the winner an hour later and adds an address with no pool.
        (noisy, FakeMessage(1, f"🚀🚀 100X GEM {WINNER} join @otheralpha", CALL_TS + 3600)),
        (noisy, FakeMessage(2, f"ape now {LOSER}", CALL_TS + 3660)),
        (noisy, FakeMessage(3, f"next runner {GHOST}", CALL_TS + 3720)),
        (good, FakeMessage(3, f"base play: {EVM_TOKEN}", CALL_TS + 240)),
        # A recap listing many addresses must not be counted as five calls.
        (noisy, FakeMessage(4, f"recap: {WINNER} {LOSER} {GHOST} "
                               f"So11111111111111111111111111111111111111112 "
                               f"0x6982508145454ce325ddbe47a25d4ec3d2311933 "
                               f"0xdac17f958d2ee523a2206206994597c13d831ec7",
                            CALL_TS + 3780)),
    ]
    for channel_id, msg in messages:
        row_id = collector.store_message(channel_id, msg)
        collector.record_handles(msg)
        collector.record_calls(channel_id, row_id, msg)

    tracker = Tracker(db, market, cfg)
    asyncio.run(tracker.drain())
    return db, cfg, good, noisy


def test_evm_call_is_scored_despite_the_chain_name_mismatch():
    # Regression: tokens.chain used to be overwritten with the market's chain id,
    # which broke the calls<->tokens join and left every EVM call unscored.
    db, _cfg, _good, _noisy = run_pipeline()
    row = db.one(
        "SELECT c.status, c.chain, t.market_chain, o.exit_reason, o.sim_return "
        "  FROM calls c "
        "  JOIN tokens t ON t.chain = c.chain AND t.address = c.address "
        "  LEFT JOIN outcomes o ON o.call_id = c.id "
        " WHERE c.address = ?", (EVM_TOKEN,))
    assert row is not None
    assert (row["chain"], row["market_chain"]) == ("evm", "base")
    assert row["status"] == "done"
    assert row["exit_reason"] == "tp"


def test_calls_are_deduplicated_and_recaps_ignored():
    db, _cfg, good, noisy = run_pipeline()
    assert db.one("SELECT COUNT(*) n FROM calls WHERE channel_id=?", (good,))["n"] == 3
    # noisy: WINNER, LOSER, GHOST once each; the 6-address recap adds nothing.
    assert db.one("SELECT COUNT(*) n FROM calls WHERE channel_id=?", (noisy,))["n"] == 3


def test_outcomes_reflect_the_price_curves():
    db, _cfg, good, _noisy = run_pipeline()
    win = db.one(
        "SELECT o.* FROM outcomes o JOIN calls c ON c.id=o.call_id "
        "WHERE c.address=? AND c.channel_id=?", (WINNER, good))
    lose = db.one(
        "SELECT o.* FROM outcomes o JOIN calls c ON c.id=o.call_id "
        "WHERE c.address=? AND c.channel_id=?", (LOSER, good))

    assert win["exit_reason"] == "tp" and abs(win["sim_return"] - 1.0) < 1e-9
    assert lose["exit_reason"] == "sl" and abs(lose["sim_return"] + 0.5) < 1e-9


def test_address_without_a_pool_is_marked_unresolved_not_dropped():
    db, _cfg, _good, noisy = run_pipeline()
    row = db.one("SELECT status FROM calls WHERE address=?", (GHOST,))
    assert row["status"] == "unresolved"
    token = db.one("SELECT resolve_status FROM tokens WHERE address=?", (GHOST,))
    assert token["resolve_status"] == "nopair"


def test_leaderboard_prefers_the_first_caller():
    db, cfg, _good, _noisy = run_pipeline()
    stats = {s.username: s for s in compute_stats(db, cfg.window_days, cfg.min_calls)}

    assert stats["goodcalls"].first_share == 1.0
    assert stats["noisycalls"].first_share == 0.0
    assert stats["noisycalls"].dead_calls == 1
    # Same two tokens, same outcomes - the difference is who was first and who
    # padded the ledger with a dead link.
    assert stats["goodcalls"].score > stats["noisycalls"].score


def test_mentioned_handles_become_candidates():
    db, _cfg, _good, _noisy = run_pipeline()
    assert db.one("SELECT handle FROM candidates WHERE handle='otheralpha'") is not None
