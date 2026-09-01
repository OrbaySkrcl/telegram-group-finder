"""Command line interface: python -m tgfinder <command>"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from . import discovery, report
from .collector import Collector
from .control import Control
from .config import load_config
from .db import Database
from .prices import MarketData
from .scoring import compute_stats
from .tgclient import build_client, channel_identity
from .tracker import Tracker

log = logging.getLogger("tgfinder")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# commands that do not need Telegram
# --------------------------------------------------------------------------

def cmd_seed(args, cfg, db) -> int:
    """Add channels/handles to the candidate pool from a file or the command line."""
    handles: list[str] = list(args.handles)
    if args.file:
        import re
        from .extract import extract_handles
        lines = [ln for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
                 if not ln.strip().startswith("#")]
        # Links and @mentions anywhere in the text...
        handles += [h.name for h in extract_handles("\n".join(lines))]
        # ...plus bare one-per-line handles, which carry no marker of their own.
        bare = re.compile(r"^\+?[A-Za-z][A-Za-z0-9_]{4,31}$")
        handles += [ln.strip().lstrip("@") for ln in lines
                    if bare.match(ln.strip().lstrip("@"))]

    now = int(time.time())
    added = 0
    for handle in handles:
        cleaned = handle.strip().lstrip("@")
        if not cleaned:
            continue
        db.add_candidate(cleaned, cleaned.startswith("+"), "seed", now, mentions=1)
        if args.approve:
            discovery.set_status(db, cleaned, "approved")
        added += 1
    print(f"{added} handle(s) added to the candidate pool"
          + (" and approved for joining." if args.approve else "."))
    return 0


def cmd_candidates(args, cfg, db) -> int:
    rows = discovery.rank_candidates(db, args.limit, args.status)
    if not rows:
        print(f"No candidates with status '{args.status}'. "
              "Try `seed`, `discover`, or --status all.")
        return 0
    print(f"{'HANDLE':30} {'STATUS':9} {'MENTION':8} {'FWD':5} {'FWD-BY':7} SOURCE")
    print("-" * 84)
    for row in rows:
        print(f"{row['handle'][:30]:30} {row['status']:9} {row['mentions']:<8} "
              f"{row['forwards']:<5} {row['distinct_forwarders']:<7} "
              f"{(row['source'] or '')[:22]}")
    print("\nApprove with: python -m tgfinder approve <handle> [...]")
    return 0


def cmd_approve(args, cfg, db) -> int:
    for handle in args.handles:
        discovery.set_status(db, handle.lstrip("@"), "approved")
    print(f"{len(args.handles)} candidate(s) approved. Run `join` to join them.")
    return 0


def cmd_reject(args, cfg, db) -> int:
    for handle in args.handles:
        discovery.set_status(db, handle.lstrip("@"), "rejected")
    print(f"{len(args.handles)} candidate(s) rejected.")
    return 0


def cmd_channels(args, cfg, db) -> int:
    rows = db.query(
        "SELECT c.id, c.username, c.title, c.members, c.monitored, "
        "       (SELECT COUNT(*) FROM calls WHERE channel_id = c.id) AS calls "
        "  FROM channels c ORDER BY calls DESC"
    )
    if not rows:
        print("No channels tracked yet. Use `backfill` or `join`.")
        return 0
    print(f"{'ID':5} {'HANDLE':28} {'MEMBERS':9} {'CALLS':7} MON")
    print("-" * 60)
    for row in rows:
        handle = row["username"] or (row["title"] or "")[:28]
        print(f"{row['id']:<5} {handle[:28]:28} {str(row['members'] or '-'):9} "
              f"{row['calls']:<7} {'yes' if row['monitored'] else 'no'}")
    return 0


def cmd_monitor(args, cfg, db) -> int:
    flag = 0 if args.off else 1
    changed = 0
    for target in args.channels:
        handle = target.lstrip("@")
        cur = db.execute(
            "UPDATE channels SET monitored=? WHERE id=? OR lower(username)=lower(?)",
            (flag, int(handle) if handle.isdigit() else -1, handle),
        )
        if cur.rowcount:
            changed += 1
        else:
            print(f"  unknown channel: {target}")
    print(f"{changed} channel(s) {'un' if args.off else ''}monitored.")
    return 0


def cmd_score(args, cfg, db) -> int:
    stats = compute_stats(db, args.days or cfg.window_days,
                          args.min_calls if args.min_calls is not None else cfg.min_calls)
    print(report.render_table(stats, args.limit))
    return 0


def cmd_detail(args, cfg, db) -> int:
    target = args.channel.lstrip("@")
    row = db.one(
        "SELECT id FROM channels WHERE id = ? OR lower(username) = lower(?)",
        (int(target) if target.isdigit() else -1, target),
    )
    if row is None:
        print(f"Unknown channel: {args.channel}")
        return 1
    print(report.render_channel_detail(db, int(row["id"]), args.limit))
    return 0


# --------------------------------------------------------------------------
# commands that need Telegram
# --------------------------------------------------------------------------

async def cmd_discover(args, cfg, db) -> int:
    keywords = args.keywords or discovery.DEFAULT_KEYWORDS
    client = build_client(cfg)
    async with client:
        results = await discovery.search_public_channels(client, keywords, args.limit)
        discovery.record_search_results(db, results)
    print(f"{len(results)} public channel(s) added to the candidate pool.")
    print("Review them with: python -m tgfinder candidates")
    return 0


async def cmd_join(args, cfg, db) -> int:
    client = build_client(cfg)
    async with client:
        joined = await discovery.join_approved(db, client, args.max or cfg.max_joins_per_day)
    print(f"Joined {len(joined)} channel(s): {', '.join(joined) if joined else '-'}")
    return 0


async def cmd_backfill(args, cfg, db) -> int:
    """Replay a channel's history and score it immediately - no waiting."""
    client = build_client(cfg)
    market = MarketData()
    try:
        async with client:
            collector = Collector(db, client)
            for handle in args.handles:
                try:
                    entity = await client.get_entity(handle.lstrip("@"))
                except Exception as exc:
                    print(f"  {handle}: cannot resolve ({exc})")
                    continue
                info = await collector.backfill(entity, args.days, args.limit)
                print(f"  {handle}: {info['messages']} messages, "
                      f"{info['new_calls']} new calls")

        print("\nResolving prices and replaying outcomes (this is the slow part)...")
        tracker = Tracker(db, market, cfg)
        totals = await tracker.drain()
        print(f"  resolved {totals['resolved']} token(s), scored {totals['scored']} call(s)")
    finally:
        await market.aclose()

    stats = compute_stats(db, max(args.days, cfg.window_days), cfg.min_calls)
    print()
    print(report.render_table(stats))
    return 0


async def cmd_run(args, cfg, db) -> int:
    """The long-running service: listen, track, and post a daily leaderboard."""
    client = build_client(cfg)
    market = MarketData()
    try:
        async with client:
            collector = Collector(db, client)
            # Register the account's channels, but do not start reading a chat
            # just because the account happens to be in it - personal groups stay
            # out unless --adopt-all is passed or they were added deliberately
            # (via `backfill`, `join`, or `monitor`). Existing flags are kept.
            now = int(time.time())
            async for dialog in client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    tg_id, username, title, members = channel_identity(dialog.entity)
                    db.upsert_channel(tg_id, username, title, members, now,
                                      monitored=1 if args.adopt_all else 0)
            collector.refresh_monitored(force=True)
            collector.attach()

            tracker = Tracker(db, market, cfg)
            log.info("watching %d channel(s)", collector.monitored_count)
            if collector.monitored_count == 0:
                log.warning("nothing is monitored yet - message the control chat "
                            "with /backfill @channel 14 to get started")

            # The control chat turns the service into something you can drive
            # from Telegram, so a terminal is never required to use it.
            control = Control(db, client, market, cfg, collector, tracker)
            await control.start()

            await asyncio.gather(
                tracker.run_forever(),
                _daily_report(client, db, cfg),
                client.run_until_disconnected(),
            )
    finally:
        await market.aclose()
    return 0


async def _daily_report(client, db, cfg) -> None:  # pragma: no cover - timing loop
    import datetime as dt
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        target = now.replace(hour=cfg.report_hour_utc, minute=0, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            stats = compute_stats(db, cfg.window_days, cfg.min_calls)
            await client.send_message(
                cfg.report_chat, report.render_telegram(stats, cfg.window_days),
                link_preview=False, parse_mode=None,
            )
            log.info("daily report sent to %s", cfg.report_chat)
        except Exception:
            log.exception("could not send the daily report")


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgfinder",
        description="Measure which Telegram call channels actually have an edge.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="add handles to the candidate pool")
    p.add_argument("handles", nargs="*")
    p.add_argument("--file", help="text file of handles or t.me links (X search dumps work)")
    p.add_argument("--approve", action="store_true", help="mark them ready to join")
    p.set_defaults(func=cmd_seed, needs_async=False)

    p = sub.add_parser("discover", help="search Telegram's public channel directory")
    p.add_argument("keywords", nargs="*")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_discover, needs_async=True)

    p = sub.add_parser("candidates", help="list discovery leads to review")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--status", default="new",
                   choices=["new", "approved", "joined", "rejected", "all"])
    p.set_defaults(func=cmd_candidates, needs_async=False)

    p = sub.add_parser("approve", help="approve candidates for joining")
    p.add_argument("handles", nargs="+")
    p.set_defaults(func=cmd_approve, needs_async=False)

    p = sub.add_parser("reject", help="reject candidates")
    p.add_argument("handles", nargs="+")
    p.set_defaults(func=cmd_reject, needs_async=False)

    p = sub.add_parser("join", help="join approved candidates (rate limited)")
    p.add_argument("--max", type=int, help="override the daily join cap")
    p.set_defaults(func=cmd_join, needs_async=True)

    p = sub.add_parser("backfill", help="score a channel from its history, right now")
    p.add_argument("handles", nargs="+")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--limit", type=int, default=5000, help="max messages to read")
    p.set_defaults(func=cmd_backfill, needs_async=True)

    p = sub.add_parser("channels", help="list tracked channels")
    p.set_defaults(func=cmd_channels, needs_async=False)

    p = sub.add_parser("score", help="print the leaderboard")
    p.add_argument("--days", type=int)
    p.add_argument("--min-calls", type=int, dest="min_calls")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_score, needs_async=False)

    p = sub.add_parser("detail", help="per-call ledger for one channel")
    p.add_argument("channel", help="channel id or @handle")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_detail, needs_async=False)

    p = sub.add_parser("monitor", help="start (or stop) reading a tracked channel")
    p.add_argument("channels", nargs="+", help="channel ids or @handles")
    p.add_argument("--off", action="store_true", help="stop monitoring instead")
    p.set_defaults(func=cmd_monitor, needs_async=False)

    p = sub.add_parser("run", help="run the collector service (this is the Railway entrypoint)")
    p.add_argument("--adopt-all", action="store_true", dest="adopt_all",
                   help="monitor every channel this account is in, including ones "
                        "you never added on purpose")
    p.set_defaults(func=cmd_run, needs_async=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config()
    db = Database(cfg.db_path)
    try:
        if args.needs_async:
            return asyncio.run(args.func(args, cfg, db))
        return args.func(args, cfg, db)
    except KeyboardInterrupt:
        return 130
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
