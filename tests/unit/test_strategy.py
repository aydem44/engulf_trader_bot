"""Unit tests for the pure domain logic in strategy.calculating_signal().

In-memory pandas DataFrames are used as fakes; there are no mocks of the
exchange or other I/O. The suite covers both sides of every branch, including
the minimum-risk fallbacks for LONG and SHORT.
"""

import importlib
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

strategy: Any = importlib.import_module("strategy")
assert Path(strategy.__file__).resolve() == PROJECT_ROOT / "strategy.py"

calculating_signal = strategy.calculating_signal

REWARD_RISK = 2.0
MA_FAST = 20
MA_SLOW = 40


def make_klines(prev, curr, trend):
    """Build a two-candle DataFrame in the shape expected by the strategy."""
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


def test_long_engulfing_with_positive_risk():
    prev = (100.0, 100.5, 97.5, 98.0)  # bearish
    curr = (98.0, 101.5, 97.5, 101.0)  # bullish engulfing

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=True), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == 1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    assert float(stop_loss) < curr[3] < float(take_profit)


def test_long_engulfing_uses_minimum_risk_when_body_is_not_positive(capsys):
    # Current candle closes above the previous open (LONG condition) but is
    # itself bearish, so risk = close - open is negative.
    prev = (100.0, 100.5, 97.5, 98.0)
    curr = (106.0, 106.5, 100.0, 101.0)

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=True), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == 1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    assert float(stop_loss) < curr[3] < float(take_profit)
    assert "Использован минимальный риск" in capsys.readouterr().out


def test_short_engulfing_with_positive_risk():
    prev = (100.0, 102.5, 99.5, 102.0)  # bullish
    curr = (102.0, 102.5, 98.5, 99.0)  # bearish engulfing

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=False), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == -1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    assert float(stop_loss) > curr[3] > float(take_profit)


def test_short_engulfing_uses_fallback_when_body_is_not_positive():
    # Current candle closes below the previous open (SHORT condition) but is
    # itself bullish, so risk = open - close is negative.
    prev = (100.0, 102.5, 99.5, 102.0)
    curr = (99.0, 100.0, 98.5, 99.8)

    entry_flag, stop_loss, take_profit = calculating_signal(
        make_klines(prev, curr, trend=False), REWARD_RISK, MA_FAST, MA_SLOW
    )

    assert entry_flag == -1
    assert isinstance(stop_loss, str)
    assert isinstance(take_profit, str)
    assert float(stop_loss) > curr[3] > float(take_profit)


@pytest.mark.parametrize(
    ("prev", "curr", "trend"),
    [
        # Bearish candle, but the current close does not exceed its open.
        ((100.0, 100.5, 98.0, 99.0), (99.0, 100.0, 98.0, 99.5), True),
        # LONG pattern, but the trend flag is False.
        ((100.0, 100.5, 97.5, 98.0), (98.0, 101.5, 97.5, 101.0), False),
        # Bullish candle, but the current close does not fall below its open.
        ((100.0, 102.0, 99.0, 101.0), (101.0, 101.5, 100.0, 100.5), False),
        # SHORT pattern, but the trend flag is True.
        ((100.0, 102.5, 99.5, 102.0), (102.0, 102.5, 98.5, 99.0), True),
        # Doji: neither bullish nor bearish previous candle.
        ((100.0, 101.0, 99.0, 100.0), (100.0, 100.5, 98.0, 99.0), False),
    ],
)
def test_no_signal_returns_zero_and_no_levels(prev, curr, trend):
    result = calculating_signal(make_klines(prev, curr, trend), REWARD_RISK, MA_FAST, MA_SLOW)

    assert result == (0, None, None)
