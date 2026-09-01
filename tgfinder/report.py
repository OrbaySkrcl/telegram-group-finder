"""Render the leaderboard for a terminal and for a Telegram message."""
from __future__ import annotations

from .scoring import ChannelStats

COLUMNS = [
    ("score",  "SCORE", 7),
    ("name",   "CHANNEL", 24),
    ("calls",  "CALL", 6),
    ("cpd",    "/DAY", 6),
    ("avg",    "AVG$", 8),
    ("hit2x",  "2X%", 6),
    ("rug",    "RUG%", 6),
    ("first",  "1ST%", 6),
    ("uniq",   "UNQ%", 6),
    ("mc",     "MED-MC", 9),
    ("flags",  "FLAGS", 28),
]


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    for unit, size in (("M", 1e6), ("K", 1e3)):
        if value >= size:
            return f"{value / size:.1f}{unit}"
    return f"{value:.0f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}"


def _row(stats: ChannelStats) -> dict[str, str]:
    name = stats.username or stats.title or str(stats.channel_id)
    return {
        "score": f"{stats.score:+.2f}",
        "name": name[:24],
        "calls": f"{stats.calls}",
        "cpd": f"{stats.calls_per_day:.1f}",
        "avg": "-" if stats.avg_return is None else f"{stats.avg_return * 100:+.0f}%",
        "hit2x": _pct(stats.hit_2x),
        "rug": _pct(stats.rug_rate),
        "first": _pct(stats.first_share),
        "uniq": _pct(stats.unique_share),
        "mc": _money(stats.median_mc_at_call),
        "flags": ",".join(stats.flags)[:28],
    }


def render_table(all_stats: list[ChannelStats], limit: int = 30) -> str:
    if not all_stats:
        return ("No channel has reached the minimum call count yet.\n"
                "Add channels with `backfill` to get a ledger without waiting.")

    header = "  ".join(title.ljust(width) for _key, title, width in COLUMNS)
    lines = [header, "-" * len(header)]
    for stats in all_stats[:limit]:
        row = _row(stats)
        lines.append("  ".join(row[key].ljust(width) for key, _t, width in COLUMNS))

    lines.append("")
    lines.append("AVG$ = mean return per call under the simulated rule "
                 "(buy after the delay, pay slippage, TP/SL, else exit at the horizon).")
    lines.append("1ST% = share of its tokens this channel called before any other "
                 "channel you track. UNQ% = calls nobody else made.")
    lines.append("SCORE = AVG$ shrunk toward zero by sample size, penalised for "
                 "spam volume, rugs, and relaying.")
    return "\n".join(lines)


def render_telegram(all_stats: list[ChannelStats], window_days: int, limit: int = 12) -> str:
    """Plain text on purpose: channel handles contain underscores, and sending
    them through Telegram's markdown parser mangles the message."""
    if not all_stats:
        return "tgfinder: not enough data yet."

    lines = [f"tgfinder leaderboard — last {window_days}d", ""]
    for i, s in enumerate(all_stats[:limit], 1):
        name = s.username or s.title or str(s.channel_id)
        avg = "-" if s.avg_return is None else f"{s.avg_return * 100:+.0f}%"
        flags = f"  [{','.join(s.flags)}]" if s.flags else ""
        lines.append(
            f"{i}. {name} — score {s.score:+.2f}\n"
            f"   {s.calls} calls ({s.calls_per_day}/day) · avg {avg} · "
            f"2x {s.hit_2x * 100:.0f}% · rug {s.rug_rate * 100:.0f}% · "
            f"first {s.first_share * 100:.0f}%{flags}"
        )
    lines.append("")
    lines.append("avg = simulated per-call return after slippage, not peak multiples.")
    return "\n".join(lines)


def render_channel_detail(db, channel_id: int, limit: int = 40) -> str:
    """Per-call ledger for one channel - the receipts behind its score."""
    rows = db.query(
        """
        SELECT c.ts, c.address, c.mc_at_call, c.status,
               t.symbol, o.sim_return, o.max_multiple, o.exit_reason,
               o.minutes_to_2x, o.rugged, o.candle_source
          FROM calls c
          LEFT JOIN tokens   t ON t.chain = c.chain AND t.address = c.address
          LEFT JOIN outcomes o ON o.call_id = c.id
         WHERE c.channel_id = ?
         ORDER BY c.ts DESC
         LIMIT ?
        """,
        (channel_id, limit),
    )
    if not rows:
        return "No calls recorded for this channel."

    import datetime as _dt
    lines = [f"{'WHEN':17} {'TOKEN':12} {'MC':9} {'SIM':8} {'PEAK':7} {'EXIT':8} SRC",
             "-" * 78]
    for r in rows:
        when = _dt.datetime.fromtimestamp(int(r["ts"]), _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        symbol = (r["symbol"] or r["address"][:8])[:12]
        sim = "-" if r["sim_return"] is None else f"{r['sim_return'] * 100:+.0f}%"
        peak = "-" if r["max_multiple"] is None else f"{r['max_multiple']:.2f}x"
        exit_reason = r["exit_reason"] or r["status"]
        lines.append(f"{when:17} {symbol:12} {_money(r['mc_at_call']):9} {sim:8} "
                     f"{peak:7} {exit_reason:8} {r['candle_source'] or '-'}")
    return "\n".join(lines)
