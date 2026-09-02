"""Resolve calls to real pools, then score them once the horizon has elapsed."""
from __future__ import annotations

import asyncio
import logging
import time

from .simulate import SimParams, simulate

log = logging.getLogger("tgfinder.tracker")

# A call detected within this window is priced from the live quote; older ones are
# priced from historical candles instead.
LIVE_PRICING_WINDOW_SEC = 15 * 60


class Tracker:
    def __init__(self, db, market, cfg):
        self.db = db
        self.market = market
        self.cfg = cfg
        self.params = SimParams(
            entry_delay_sec=cfg.entry_delay_sec,
            slippage=cfg.slippage,
            tp_multiple=cfg.tp_multiple,
            sl_drop=cfg.sl_drop,
            horizon_sec=cfg.horizon_sec,
        )

    # ---- step 1: address -> tradable pool ---------------------------------

    async def resolve_pending(self, batch: int = 60) -> int:
        rows = self.db.query(
            "SELECT chain, address FROM tokens WHERE resolve_status = 'pending' LIMIT ?",
            (batch,),
        )
        if not rows:
            return 0

        now = int(time.time())
        addresses = [r["address"] for r in rows]
        found = await self.market.lookup_tokens(addresses)

        # Anything the bulk endpoint missed may still be a pool address.
        for address in addresses:
            if address not in found:
                info = await self.market.search(address)
                if info is not None:
                    found[address] = info

        resolved = 0
        for row in rows:
            address = row["address"]
            info = found.get(address)
            if info is None:
                self.db.execute(
                    "UPDATE tokens SET resolve_status='nopair', resolved_at=? "
                    "WHERE chain=? AND address=?",
                    (now, row["chain"], address),
                )
                self.db.execute(
                    "UPDATE calls SET status='unresolved' WHERE chain=? AND address=?",
                    (row["chain"], address),
                )
                continue

            self.db.execute(
                "UPDATE tokens SET symbol=?, name=?, pair_address=?, pair_created_at=?, "
                "resolve_status='ok', resolved_at=?, market_chain=? "
                "WHERE chain=? AND address=?",
                (info.symbol, info.name, info.pair_address, info.pair_created_at,
                 now, info.chain, row["chain"], address),
            )
            # Live calls: capture the quote now, while it is still the call price.
            self.db.execute(
                "UPDATE calls SET mc_at_call=?, price_at_call=?, liq_at_call=? "
                "WHERE chain=? AND address=? AND mc_at_call IS NULL "
                "  AND ? - ts <= ?",
                (info.market_cap, info.price_usd, info.liquidity_usd,
                 row["chain"], address, now, LIVE_PRICING_WINDOW_SEC),
            )
            resolved += 1
        return resolved

    # ---- step 2: pool + elapsed horizon -> measured outcome ---------------

    async def score_due(self, batch: int = 40) -> int:
        deadline = int(time.time()) - self.cfg.horizon_sec
        rows = self.db.query(
            """
            SELECT c.id, c.ts, c.chain, c.address, c.mc_at_call,
                   t.pair_address, t.market_chain
              FROM calls c
              JOIN tokens t ON t.chain = c.chain AND t.address = c.address
             WHERE c.status = 'pending'
               AND t.resolve_status = 'ok'
               AND t.pair_address IS NOT NULL
               AND c.ts <= ?
             ORDER BY c.ts
             LIMIT ?
            """,
            (deadline, batch),
        )
        done = 0
        for row in rows:
            try:
                await self._score_call(row)
                done += 1
            except Exception:
                log.exception("scoring failed for call %s", row["id"])
        return done

    async def _score_call(self, row) -> None:
        call_ts = int(row["ts"])
        chain = row["market_chain"] or row["chain"]
        candles, source = await self.market.candles_covering(
            chain, row["pair_address"], call_ts, call_ts + self.cfg.horizon_sec
        )

        now = int(time.time())
        if not candles:
            # Two different failures, kept apart on purpose. "no_network" means we
            # have no candle source for that chain - a gap in this tool, and it
            # would be unfair to charge it to the channel. "no_data" means the pool
            # exists but has no usable history, which is the channel's problem.
            unsupported = source == "no_network"
            self.db.execute(
                "INSERT OR REPLACE INTO outcomes(call_id, exit_reason, candle_source, "
                "computed_at) VALUES(?, ?, ?, ?)",
                (int(row["id"]), "no_network" if unsupported else "no_data",
                 source, now),
            )
            self.db.execute(
                "UPDATE calls SET status=? WHERE id=?",
                ("nochain" if unsupported else "unresolved", int(row["id"])),
            )
            return

        result = simulate(candles, call_ts, self.params)
        self.db.execute(
            "INSERT OR REPLACE INTO outcomes(call_id, entry_price, exit_price, "
            "exit_reason, sim_return, max_multiple, min_multiple, minutes_to_max, "
            "minutes_to_2x, rugged, candle_source, computed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(row["id"]), result.entry_price, result.exit_price, result.exit_reason,
             result.sim_return, result.max_multiple, result.min_multiple,
             result.minutes_to_max, result.minutes_to_2x, int(result.rugged),
             source, now),
        )
        # Backfilled calls have no live quote; take the entry price from the candles.
        if row["mc_at_call"] is None and result.entry_price:
            self.db.execute("UPDATE calls SET price_at_call=? WHERE id=?",
                            (result.entry_price, int(row["id"])))
        self.db.execute(
            "UPDATE calls SET status=? WHERE id=?",
            ("done" if result.exit_reason != "no_data" else "unresolved", int(row["id"])),
        )

    # ---- background loop --------------------------------------------------

    async def run_forever(self, interval: int = 60) -> None:  # pragma: no cover
        while True:
            try:
                n_resolved = await self.resolve_pending()
                n_scored = await self.score_due()
                if n_resolved or n_scored:
                    log.info("resolved=%d scored=%d", n_resolved, n_scored)
            except Exception:
                log.exception("tracker cycle failed")
            await asyncio.sleep(interval)

    async def drain(self, max_cycles: int = 500) -> dict:
        """Run resolve+score until there is nothing left (used by `backfill`)."""
        totals = {"resolved": 0, "scored": 0}
        for _ in range(max_cycles):
            n_resolved = await self.resolve_pending()
            n_scored = await self.score_due()
            totals["resolved"] += n_resolved
            totals["scored"] += n_scored
            if not n_resolved and not n_scored:
                break
        return totals
