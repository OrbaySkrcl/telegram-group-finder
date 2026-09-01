"""Rank channels by measured edge, not by vibes.

Two traps this module is built to avoid:

1. Survivorship bias. A token whose pool vanished has no candles, and silently
   dropping it makes every rug-heavy channel look excellent. Unresolvable calls are
   counted and reported as `dead_rate`, and a call that resolved and then died is
   scored as the near-total loss it was.
2. Peak-multiple worship. `max_multiple` is reported for context only. Ranking uses
   `avg_return`, the mean outcome of the mechanical strategy in simulate.py.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# A channel with 5 calls that went well is not evidence. Shrink the measured edge
# toward zero until enough calls have accumulated: n / (n + PRIOR).
SHRINK_PRIOR = 12


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


@dataclass
class ChannelStats:
    channel_id: int
    title: str
    username: str | None
    members: int | None

    calls: int = 0
    dead_calls: int = 0            # address never resolved to a live pool
    scored_calls: int = 0          # calls with usable candles
    calls_per_day: float = 0.0

    median_mc_at_call: float | None = None
    median_pair_age_min: float | None = None   # token age when called
    first_share: float = 0.0                   # share of calls this channel made first
    median_delay_min: float | None = None      # lag behind the pool's first caller
    unique_share: float = 0.0                  # calls nobody else in the pool made

    avg_return: float | None = None            # mean simulated return per call
    median_return: float | None = None
    hit_2x: float = 0.0
    hit_5x: float = 0.0
    rug_rate: float = 0.0
    median_max_multiple: float | None = None
    median_minutes_to_2x: float | None = None

    score: float = 0.0
    flags: list[str] = field(default_factory=list)


def compute_stats(db, window_days: int, min_calls: int) -> list[ChannelStats]:
    """Aggregate per-channel performance over the trailing `window_days`."""
    cutoff = _now() - window_days * 86400

    rows = db.query(
        """
        SELECT c.id           AS call_id,
               c.channel_id   AS channel_id,
               c.chain        AS chain,
               c.address      AS address,
               c.ts           AS ts,
               c.mc_at_call   AS mc_at_call,
               c.status       AS status,
               t.pair_created_at AS pair_created_at,
               o.sim_return   AS sim_return,
               o.max_multiple AS max_multiple,
               o.minutes_to_2x AS minutes_to_2x,
               o.rugged       AS rugged,
               o.candle_source AS candle_source
          FROM calls c
          LEFT JOIN tokens   t ON t.chain = c.chain AND t.address = c.address
          LEFT JOIN outcomes o ON o.call_id = c.id
         WHERE c.ts >= ?
        """,
        (cutoff,),
    )
    if not rows:
        return []

    # Who called each token first, and how many channels called it at all.
    # Only tokens that actually traded count: being "first" to post an address
    # that never had a pool is not alpha, it is noise, and letting it count would
    # hand a spam channel a perfect first-caller record.
    first_ts: dict[tuple, int] = {}
    callers: dict[tuple, set[int]] = {}
    for row in rows:
        if row["status"] == "unresolved":
            continue
        key = (row["chain"], row["address"])
        ts = int(row["ts"])
        if key not in first_ts or ts < first_ts[key]:
            first_ts[key] = ts
        callers.setdefault(key, set()).add(int(row["channel_id"]))

    channels = {
        int(r["id"]): r
        for r in db.query("SELECT id, title, username, members FROM channels")
    }

    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(int(row["channel_id"]), []).append(row)

    results: list[ChannelStats] = []
    for channel_id, calls in grouped.items():
        if len(calls) < min_calls:
            continue
        meta = channels.get(channel_id)
        stats = ChannelStats(
            channel_id=channel_id,
            title=(meta["title"] if meta else None) or f"channel:{channel_id}",
            username=meta["username"] if meta else None,
            members=meta["members"] if meta else None,
            calls=len(calls),
        )

        span_days = max((max(c["ts"] for c in calls) - min(c["ts"] for c in calls)) / 86400.0,
                        1.0)
        stats.calls_per_day = round(len(calls) / min(span_days, float(window_days)), 2)

        tradable = [c for c in calls if c["status"] != "unresolved"]
        returns: list[float] = []
        maxes: list[float] = []
        mcs: list[float] = []
        ages: list[float] = []
        delays: list[float] = []
        to_2x: list[float] = []
        firsts = 0
        uniques = 0
        rugs = 0

        for row in calls:
            key = (row["chain"], row["address"])
            if key in first_ts:
                if int(row["ts"]) <= first_ts[key]:
                    firsts += 1
                else:
                    delays.append((int(row["ts"]) - first_ts[key]) / 60.0)
                if len(callers[key]) == 1:
                    uniques += 1
            if row["mc_at_call"]:
                mcs.append(float(row["mc_at_call"]))
            if row["pair_created_at"]:
                ages.append(max(0.0, (int(row["ts"]) - int(row["pair_created_at"])) / 60.0))

            if row["status"] == "unresolved":
                # Address never had a tradable pool we could find. Not scored as a
                # loss (it may not be a token at all) but tracked as a quality signal.
                stats.dead_calls += 1
                continue
            if row["sim_return"] is None:
                continue

            stats.scored_calls += 1
            returns.append(float(row["sim_return"]))
            if row["max_multiple"] is not None:
                maxes.append(float(row["max_multiple"]))
            if row["minutes_to_2x"] is not None:
                to_2x.append(float(row["minutes_to_2x"]))
            if row["rugged"]:
                rugs += 1

        denom = len(tradable) or 1
        stats.first_share = round(firsts / denom, 3)
        stats.unique_share = round(uniques / denom, 3)
        stats.median_delay_min = _median(delays)
        stats.median_mc_at_call = _median(mcs)
        stats.median_pair_age_min = _median(ages)
        stats.median_minutes_to_2x = _median(to_2x)

        if stats.scored_calls:
            stats.avg_return = round(sum(returns) / len(returns), 4)
            stats.median_return = round(statistics.median(returns), 4)
            stats.hit_2x = round(sum(1 for m in maxes if m >= 2.0) / stats.scored_calls, 3)
            stats.hit_5x = round(sum(1 for m in maxes if m >= 5.0) / stats.scored_calls, 3)
            stats.rug_rate = round(rugs / stats.scored_calls, 3)
            stats.median_max_multiple = _median(maxes)

        stats.score = _score(stats)
        stats.flags = _flags(stats)
        results.append(stats)

    results.sort(key=lambda s: s.score, reverse=True)
    return results


def _score(s: ChannelStats) -> float:
    """Shrunk expected value per call, penalised for spam and rugs.

    Deliberately boring: the ranking must stay explainable from the columns next
    to it. Anything clever here would just be a second opinion dressed as a number.
    """
    if not s.scored_calls or s.avg_return is None:
        return 0.0
    edge = s.avg_return * (s.scored_calls / (s.scored_calls + SHRINK_PRIOR))
    # Firehose channels: every extra call past ~8/day dilutes the signal you act on.
    if s.calls_per_day > 8:
        edge *= 8.0 / s.calls_per_day
    edge *= (1.0 - min(s.rug_rate, 0.9))
    # A channel that mostly relays other people's calls has no edge to sell you.
    edge *= 0.6 + 0.4 * s.first_share
    return round(edge * 100, 2)


def _flags(s: ChannelStats) -> list[str]:
    flags: list[str] = []
    if s.calls_per_day > 10:
        flags.append("SPAM")
    if s.first_share > 0.35:
        flags.append("EARLY")
    if s.unique_share > 0.5:
        flags.append("UNIQUE")
    if s.median_delay_min is not None and s.median_delay_min > 10 and s.first_share < 0.15:
        flags.append("LATE")
    if s.rug_rate > 0.35:
        flags.append("RUGGY")
    if s.calls and s.dead_calls / s.calls > 0.3:
        flags.append("DEAD-LINKS")
    # Consistently calling minutes-old tokens *and* eating rugs is what a
    # deployer-adjacent distribution group looks like from the outside.
    if (s.median_pair_age_min is not None and s.median_pair_age_min < 5
            and s.rug_rate > 0.25):
        flags.append("INSIDER?")
    if s.median_mc_at_call is not None and s.median_mc_at_call > 1_000_000:
        flags.append("HIGH-MC")
    return flags


def _now() -> int:
    import time
    return int(time.time())
