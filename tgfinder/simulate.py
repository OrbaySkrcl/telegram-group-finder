"""Turn a channel's call into the only number that matters: what you'd actually make.

Peak ("ATH") multiples are fantasy - you cannot sell the wick. This module replays
minute/hour candles under a mechanical rule you *could* have followed: buy N seconds
after the message, pay slippage both ways, take profit at a fixed multiple, stop out
at a fixed drawdown, otherwise exit at the horizon.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

Candle = tuple  # (ts, open, high, low, close, volume)


@dataclass
class SimParams:
    entry_delay_sec: int = 60
    slippage: float = 0.03
    tp_multiple: float = 2.0
    sl_drop: float = 0.5          # exit if price falls this fraction below entry
    horizon_sec: int = 24 * 3600


@dataclass
class SimResult:
    entry_price: float | None = None
    exit_price: float | None = None
    exit_reason: str = "no_data"   # tp | sl | time | no_data
    sim_return: float | None = None
    max_multiple: float | None = None
    min_multiple: float | None = None
    minutes_to_max: int | None = None
    minutes_to_2x: int | None = None
    rugged: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def simulate(candles: list[Candle], call_ts: int, params: SimParams) -> SimResult:
    """Replay `candles` for one call. Candles must be ascending by timestamp."""
    if not candles:
        return SimResult()

    step = candles[1][0] - candles[0][0] if len(candles) > 1 else 60
    step = max(step, 1)
    entry_ts = call_ts + params.entry_delay_sec

    # Entry candle: the one containing entry_ts, else the first candle after it.
    entry_idx = None
    for i, candle in enumerate(candles):
        if candle[0] <= entry_ts < candle[0] + step:
            entry_idx = i
            break
        if candle[0] > entry_ts:
            entry_idx = i
            break
    if entry_idx is None:
        return SimResult()

    # Buying inside a candle: assume the close of that candle, plus slippage.
    raw_entry = candles[entry_idx][4]
    if raw_entry <= 0:
        return SimResult()
    entry = raw_entry * (1 + params.slippage)

    tp_price = entry * params.tp_multiple
    sl_price = entry * (1 - params.sl_drop)
    horizon_end = call_ts + params.horizon_sec

    best_high = candles[entry_idx][2]
    worst_low = candles[entry_idx][3]
    ts_at_max = candles[entry_idx][0]
    minutes_to_2x: int | None = None
    exit_price: float | None = None
    exit_reason = "time"
    last_close = candles[entry_idx][4]

    for candle in candles[entry_idx + 1:]:
        ts, _open, high, low, close, _vol = candle
        if ts > horizon_end:
            break
        last_close = close
        if high > best_high:
            best_high = high
            ts_at_max = ts
        worst_low = min(worst_low, low)
        if minutes_to_2x is None and high >= entry * 2.0:
            minutes_to_2x = max(0, (ts - call_ts) // 60)

        # Both levels inside one candle: assume the bad one filled first.
        if low <= sl_price:
            exit_price = sl_price
            exit_reason = "sl"
            break
        if high >= tp_price:
            exit_price = tp_price
            exit_reason = "tp"
            break

    if exit_price is None:
        exit_price = last_close
        exit_reason = "time"

    net_exit = exit_price * (1 - params.slippage)
    return SimResult(
        entry_price=entry,
        exit_price=exit_price,
        exit_reason=exit_reason,
        sim_return=(net_exit / entry) - 1.0,
        max_multiple=best_high / entry,
        min_multiple=worst_low / entry,
        minutes_to_max=max(0, (ts_at_max - call_ts) // 60),
        minutes_to_2x=minutes_to_2x,
        # "Rugged" = the position was effectively unexitable at the end of the window.
        rugged=bool(last_close <= entry * 0.05 and exit_reason == "time"),
    )
