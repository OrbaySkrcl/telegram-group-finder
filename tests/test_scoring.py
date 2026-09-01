import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tgfinder.db import Database
from tgfinder.scoring import compute_stats

NOW = int(time.time())


def build_db():
    db = Database(":memory:")
    for cid, name in ((1, "early_alpha"), (2, "late_relay"), (3, "rug_farm")):
        db.upsert_channel(1000 + cid, name, name, 1000, NOW)
    return db


def add_call(db, channel_id, address, ts, sim_return, max_mult, rugged=0,
             mc=50_000, pair_created=None, status="done"):
    db.execute(
        "INSERT OR IGNORE INTO tokens(chain,address,pair_created_at,resolve_status) "
        "VALUES('solana',?,?,'ok')",
        (address, pair_created if pair_created is not None else ts - 3600),
    )
    cur = db.execute(
        "INSERT INTO calls(channel_id,chain,address,ts,mc_at_call,status) "
        "VALUES(?,'solana',?,?,?,?)",
        (channel_id, address, ts, mc, status),
    )
    if status == "done":
        db.execute(
            "INSERT INTO outcomes(call_id,sim_return,max_multiple,rugged,candle_source,"
            "computed_at) VALUES(?,?,?,?,'minute',?)",
            (cur.lastrowid, sim_return, max_mult, rugged, NOW),
        )
    return cur.lastrowid


def test_first_caller_and_relay_are_distinguished():
    db = build_db()
    for i in range(10):
        token = f"tok{i}"
        add_call(db, 1, token, NOW - 86400 * (i + 1), 0.4, 2.2)
        # channel 2 always repeats the same token 30 minutes later
        add_call(db, 2, token, NOW - 86400 * (i + 1) + 1800, 0.1, 1.3)

    stats = {s.username: s for s in compute_stats(db, 30, 5)}
    early, late = stats["early_alpha"], stats["late_relay"]

    assert early.first_share == 1.0
    assert late.first_share == 0.0
    assert late.median_delay_min == 30.0
    assert "LATE" in late.flags
    assert early.score > late.score


def test_rugs_and_dead_links_drag_the_score_down():
    db = build_db()
    for i in range(12):
        rugged = 1 if i % 2 == 0 else 0
        ret = -0.95 if rugged else 0.9
        add_call(db, 3, f"rug{i}", NOW - 3600 * (i + 1), ret, 1.1 if rugged else 2.4,
                 rugged=rugged, pair_created=NOW - 3600 * (i + 1) - 60)
    stats = {s.username: s for s in compute_stats(db, 30, 5)}["rug_farm"]

    assert stats.rug_rate == 0.5
    assert "RUGGY" in stats.flags
    assert "INSIDER?" in stats.flags       # calls tokens ~1 min after pool creation
    assert stats.score < 0.5 * 100 * 0.5   # rug penalty must bite


def test_unresolved_calls_are_counted_not_hidden():
    db = build_db()
    for i in range(6):
        add_call(db, 1, f"good{i}", NOW - 3600 * (i + 1), 1.0, 3.0)
    for i in range(6):
        add_call(db, 1, f"dead{i}", NOW - 3600 * (i + 20), None, None,
                 status="unresolved")
    stats = {s.username: s for s in compute_stats(db, 30, 5)}["early_alpha"]

    assert stats.calls == 12
    assert stats.dead_calls == 6
    assert stats.scored_calls == 6
    assert "DEAD-LINKS" in stats.flags


def test_shrinkage_keeps_tiny_samples_humble():
    # Same per-call outcome at both sample sizes, spread thinly enough that the
    # spam penalty stays out of the way: only the confidence term may differ.
    db = build_db()
    for i in range(5):
        add_call(db, 1, f"lucky{i}", NOW - 86400 * (i + 1), 1.0, 3.0)
    small = {s.username: s for s in compute_stats(db, 30, 5)}["early_alpha"]
    for i in range(45):
        add_call(db, 1, f"more{i}", NOW - 86400 * (i % 25 + 1) - 60 * i, 1.0, 3.0)
    large = {s.username: s for s in compute_stats(db, 30, 5)}["early_alpha"]

    assert small.avg_return == large.avg_return == 1.0
    assert "SPAM" not in large.flags
    assert large.score > small.score * 2


def test_spam_channel_is_penalised():
    db = build_db()
    base = NOW - 86400
    for i in range(60):          # 60 calls inside one day
        add_call(db, 2, f"spam{i}", base + i * 60, 0.5, 2.1)
    stats = {s.username: s for s in compute_stats(db, 30, 5)}["late_relay"]
    assert stats.calls_per_day > 10
    assert "SPAM" in stats.flags


def test_dead_addresses_cannot_buy_an_early_reputation():
    # A channel that posts addresses nobody else mentions and that never traded
    # must not be credited as the first caller of anything.
    db = build_db()
    for i in range(10):
        add_call(db, 3, f"ghost{i}", NOW - 3600 * (i + 1), None, None,
                 status="unresolved")
    for i in range(4):
        token = f"real{i}"
        add_call(db, 1, token, NOW - 86400 * (i + 1), 0.5, 2.1)          # first
        add_call(db, 3, token, NOW - 86400 * (i + 1) + 3600, 0.1, 1.2)   # follower

    stats = {s.username: s for s in compute_stats(db, 30, 4)}
    spammer = stats["rug_farm"]
    assert spammer.dead_calls == 10
    assert spammer.first_share == 0.0
    assert "EARLY" not in spammer.flags
    assert "DEAD-LINKS" in spammer.flags
    assert stats["early_alpha"].first_share == 1.0
