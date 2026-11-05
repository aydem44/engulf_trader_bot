"""Characterization tests for strategy.calculating_signal().

These tests pin down the *current* behaviour of the engulfing pattern before any
refactoring. They must not be "fixed" by changing strategy.py.
"""

import pandas as pd

from strategy import calculating_signal

REWARD_RISK = 2.0
MA_FAST = 20
MA_SLOW = 40


def make_klines(prev, curr, trend):
    """Build a two-candle DataFrame in the shape expected by calculating_signal().

    prev/curr are (open, high, low, close) tuples. `trend` is written to the
    is_slowma_lower column of the previous candle, because that is the row the
    strategy reads.
    """
    rows = [
        {
            "open": prev[0],
            "high": prev[1],
            "low": prev[2],
            "close": prev[3],
            "is_slowma_lower": trend,
        },
        {
            "open": curr[0],
            "high": curr[1],
            "low": curr[2],
            "close": curr[3],
            "is_slowma_lower": trend,
        },
    ]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "is_slowma_lower"])


def test_long_engulfing_returns_entry_flag_1():
    # Previous candle is bearish (open 100 -> close 98).
    prev = (100.0, 100.5, 97.5, 98.0)
    # Current candle is bullish and engulfs the previous body:
    # open below previous close, close above previous open.
    curr = (98.0, 101.5, 97.8, 101.0)

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=True), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == 1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    # LONG: stop below entry, take-profit above entry.
    assert float(stop_loss) < curr[3] < float(take_profit)


def test_short_engulfing_returns_entry_flag_minus_1():
    # Previous candle is bullish (open 100 -> close 102).
    prev = (100.0, 102.5, 99.5, 102.0)
    # Current candle is bearish and engulfs the previous body:
    # open above previous close, close below previous open.
    curr = (102.0, 102.3, 98.5, 99.0)

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=False), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == -1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    # SHORT: stop above entry, take-profit below entry.
    assert float(stop_loss) > curr[3] > float(take_profit)


def test_no_engulfing_returns_entry_flag_0():
    # Bearish candle followed by a bullish candle that does NOT engulf it:
    # close 99.5 stays below the previous open 100.
    prev = (100.0, 100.2, 98.8, 99.0)
    curr = (99.2, 99.8, 98.9, 99.5)

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=True), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == 0
    assert stop_loss is None
    assert take_profit is None
