"""Characterization test for fetch_klines().

The test mocks ``session.get_kline`` and pins the current DataFrame shape,
sorting/merge behaviour and the ``is_slowma_lower`` flag.
"""

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

bot: Any = importlib.import_module("engulf_trader_bot")
assert Path(bot.__file__).resolve() == PROJECT_ROOT / "engulf_trader_bot.py"

EXPECTED_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "date",
    "close_d",
    "ma_40",
    "ma_20",
    "is_slowma_lower",
]


def to_ms(timestamp):
    return int(pd.Timestamp(timestamp).value // 1_000_000)


def make_kline(timestamp, close):
    return [
        to_ms(timestamp),
        str(close),
        str(close + 1),
        str(close - 1),
        str(close),
        "10",
        "1000",
    ]


def test_fetch_klines_merges_daily_ma_and_sets_trend_flag():
    # 45 daily closes: first 40 at 100, last 5 at 200.
    daily_dates = pd.date_range("2026-01-01", periods=45, freq="D")
    daily_closes = [100.0] * 40 + [200.0] * 5
    daily_rows = [make_kline(timestamp, close) for timestamp, close in zip(daily_dates, daily_closes, strict=True)]

    # Two 15m candles: one before the MA crossover (flag False) and one after
    # (flag True). Bybit returns klines newest-first, the function sorts them.
    candle_false = make_kline("2026-02-10 00:00:00", 1.5)
    candle_true = make_kline("2026-02-14 00:00:00", 1.5)
    klines_15m = [candle_true, candle_false]

    def fake_get_kline(**kwargs):
        if kwargs["interval"] == bot.TIMEFRAME:
            return {"retCode": 0, "result": {"list": klines_15m}}
        if kwargs["interval"] == "D":
            return {"retCode": 0, "result": {"list": list(reversed(daily_rows))}}
        raise AssertionError(f"Unexpected interval: {kwargs['interval']}")

    with patch.object(bot.session, "get_kline", side_effect=fake_get_kline) as get_kline:
        df = bot.fetch_klines()

    assert get_kline.call_count == 2
    assert [call.kwargs["interval"] for call in get_kline.call_args_list] == [
        bot.TIMEFRAME,
        "D",
    ]
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == 2
    # merge() resets the datetime index: the current contract is a RangeIndex
    # plus the "date" column used for the daily join.
    assert isinstance(df.index, pd.RangeIndex)
    assert df["date"].tolist() == [
        pd.Timestamp("2026-02-10").date(),
        pd.Timestamp("2026-02-14").date(),
    ]

    # At 2026-02-10: MA20 == MA40 == 100, so the strict ">" flag is False.
    before = df.loc[df["date"] == pd.Timestamp("2026-02-10").date()].iloc[0]
    assert before["ma_20"] == 100.0
    assert before["ma_40"] == 100.0
    assert not before["is_slowma_lower"]

    # At 2026-02-14: MA20=120 > MA40=110, so the flag is True.
    after = df.loc[df["date"] == pd.Timestamp("2026-02-14").date()].iloc[0]
    assert after["ma_20"] == 120.0
    assert after["ma_40"] == 110.0
    assert after["is_slowma_lower"]

    assert df["is_slowma_lower"].astype(bool).tolist() == [False, True]
