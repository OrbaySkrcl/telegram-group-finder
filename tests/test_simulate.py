import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tgfinder.simulate import SimParams, simulate

T0 = 1_700_000_000
P = SimParams(entry_delay_sec=60, slippage=0.0, tp_multiple=2.0, sl_drop=0.5,
              horizon_sec=3600)


def candles(prices, start=T0, step=60):
    """Build flat candles: each minute's o/h/l/c all equal the given price."""
    return [(start + i * step, p, p, p, p, 1.0) for i, p in enumerate(prices)]


def test_take_profit_hit():
    # entry at minute 1 (price 1.0), doubles at minute 3
    c = candles([1.0, 1.0, 1.5, 2.4, 3.0])
    r = simulate(c, T0, P)
    assert r.entry_price == 1.0
    assert r.exit_reason == "tp"
    assert abs(r.sim_return - 1.0) < 1e-9
    assert r.minutes_to_2x == 3


def test_stop_loss_hit():
    c = candles([1.0, 1.0, 0.8, 0.4, 0.3])
    r = simulate(c, T0, P)
    assert r.exit_reason == "sl"
    assert abs(r.sim_return - (-0.5)) < 1e-9


def test_stop_loss_wins_ties_inside_one_candle():
    # A candle whose range spans both levels must be scored as the stop.
    c = [(T0, 1, 1, 1, 1.0, 1.0), (T0 + 60, 1, 1, 1, 1.0, 1.0),
         (T0 + 120, 1.0, 3.0, 0.1, 1.0, 1.0)]
    r = simulate(c, T0, P)
    assert r.exit_reason == "sl"


def test_slippage_eats_both_sides():
    p = SimParams(entry_delay_sec=60, slippage=0.10, tp_multiple=2.0, sl_drop=0.5,
                  horizon_sec=3600)
    c = candles([1.0, 1.0, 3.0])
    r = simulate(c, T0, p)
    assert abs(r.entry_price - 1.10) < 1e-9          # paid 10% up
    assert abs(r.exit_price - 2.20) < 1e-9           # tp level off the real entry
    # sold 10% down from the tp level => 1.98 / 1.10 - 1 = 0.8
    assert abs(r.sim_return - 0.8) < 1e-9


def test_time_exit_and_peak_tracking():
    c = candles([1.0, 1.0, 1.9, 1.2, 1.1])
    r = simulate(c, T0, P)
    assert r.exit_reason == "time"
    assert abs(r.max_multiple - 1.9) < 1e-9
    assert r.minutes_to_max == 2
    assert r.minutes_to_2x is None


def test_rug_detected():
    c = candles([1.0, 1.0, 0.9, 0.6, 0.02])
    r = simulate(c, T0, SimParams(entry_delay_sec=60, slippage=0.0,
                                  tp_multiple=2.0, sl_drop=0.99, horizon_sec=3600))
    assert r.rugged is True


def test_horizon_stops_the_replay():
    # 2x only arrives after the horizon closes -> must not count as a win.
    p = SimParams(entry_delay_sec=60, slippage=0.0, tp_multiple=2.0, sl_drop=0.9,
                  horizon_sec=300)
    c = candles([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 5.0])
    r = simulate(c, T0, p)
    assert r.exit_reason == "time"
    assert r.sim_return == 0.0


def test_no_candles_is_no_data():
    assert simulate([], T0, P).exit_reason == "no_data"
