"""Characterization tests for trader.place_market_order().

The exchange calls are mocked. The tests pin the current rounding contract and
the current (missing) ``retCode`` handling before trader.py is changed.
"""

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

trader: Any = importlib.import_module("trader")
assert Path(trader.__file__).resolve() == PROJECT_ROOT / "trader.py"

INSTRUMENTS_RESPONSE = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "priceFilter": {"tickSize": "0.001"},
                "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"},
            }
        ]
    },
}

SUCCESS_RESPONSE = {"retCode": 0, "result": {"orderId": "order-1"}}
ERROR_RESPONSE = {"retCode": 10001, "retMsg": "Invalid qty"}


def setup_function():
    trader._instrument_cache.clear()


def test_place_market_order_rounds_qty_down_and_tp_sl_to_tick_size():
    with (
        patch.object(trader.session, "get_instruments_info", return_value=INSTRUMENTS_RESPONSE) as get_instruments_info,
        patch.object(trader.session, "place_order", return_value=SUCCESS_RESPONSE) as place_order,
    ):
        result = trader.place_market_order("MNTUSDT", "Buy", 1.239, 2.3456, 1.2344)

    assert result is SUCCESS_RESPONSE
    get_instruments_info.assert_called_once_with(category=trader.CATEGORY, symbol="MNTUSDT")
    place_order.assert_called_once_with(
        category=trader.CATEGORY,
        symbol="MNTUSDT",
        side="Buy",
        qty="1.23",  # ROUND_DOWN to 0.01
        orderType="Market",
        marketUnit="baseCoin",
        takeProfit="2.346",  # ROUND_HALF_UP to 0.001
        stopLoss="1.234",  # ROUND_HALF_UP to 0.001
    )


def test_place_market_order_returns_nonzero_retcode_response_unchanged():
    """Current behaviour: retCode is not checked, so the error is returned as-is."""
    with (
        patch.object(trader.session, "get_instruments_info", return_value=INSTRUMENTS_RESPONSE),
        patch.object(trader.session, "place_order", return_value=ERROR_RESPONSE) as place_order,
    ):
        result = trader.place_market_order("MNTUSDT", "Buy", 1.239, 2.3456, 1.2344)

    place_order.assert_called_once()
    assert result is ERROR_RESPONSE
    assert result["retCode"] == 10001
