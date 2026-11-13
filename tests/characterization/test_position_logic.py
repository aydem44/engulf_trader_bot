"""Regression tests for risks R1 and R13 (false close by an old closed-pnl record).

The tests drive one or more iterations of ``engulf_trader_bot.main()`` with all
pybit calls mocked. A position may only be closed when the exchange reports
``size == 0`` and the closed-pnl record was created after the position opened.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

bot: Any = importlib.import_module("engulf_trader_bot")
assert Path(bot.__file__).resolve() == PROJECT_ROOT / "engulf_trader_bot.py"

OPEN_POSITION = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "side": "Buy",
                "size": "10",
                "avgPrice": "0.60",
                "createdTime": "1700000000000",
                "positionIdx": 0,
            }
        ]
    },
}

CLOSED_POSITION = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "side": "Buy",
                "size": "0",
                "avgPrice": "0.60",
                "createdTime": "1700000000000",
                "positionIdx": 0,
            }
        ]
    },
}

OLD_CLOSED_PNL_RECORD = {
    "avgEntryPrice": "0.60",
    "avgExitPrice": "0.6279",
    "closedPnl": "-0.001547",
    "qty": "10",
    "side": "Sell",
    "createdTime": "1600000000000",
    "orderId": "old-order-id",
}

FRESH_CLOSED_PNL_RECORD = {
    "avgEntryPrice": "0.60",
    "avgExitPrice": "0.6279",
    "closedPnl": "-0.001547",
    "qty": "10",
    "side": "Sell",
    "createdTime": "1700000001000",
    "orderId": "current-order-id",
}

EMPTY_CLOSED_PNL = {"retCode": 0, "result": {"list": []}}
OLD_CLOSED_PNL = {"retCode": 0, "result": {"list": [OLD_CLOSED_PNL_RECORD]}}
FRESH_CLOSED_PNL = {"retCode": 0, "result": {"list": [FRESH_CLOSED_PNL_RECORD]}}
POSITION_API_ERROR = {"retCode": 10001, "retMsg": "request failed", "result": {"list": []}}
CLOSED_PNL_API_ERROR = {
    "retCode": 10001,
    "retMsg": "request failed",
    "result": {"list": [FRESH_CLOSED_PNL_RECORD]},
}


def run_main(closed_pnl_responses, iterations, position_responses=None):
    """Run ``main()`` for a fixed number of loop iterations, then stop it.

    All exchange and side-effect calls are mocked; ``KeyboardInterrupt`` is
    raised from the last ``time.sleep`` so the infinite loop exits cleanly.
    """
    sleep_calls = 0

    def fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= iterations:
            raise KeyboardInterrupt

    if position_responses is None:
        position_responses = [OPEN_POSITION] * (iterations + 3)

    df = pd.DataFrame({"close": [100.0, 101.0]})

    with (
        patch.object(bot, "fetch_klines", return_value=df),
        patch.object(bot, "calculating_signal", return_value=(1, "0.63", "0.58")),
        patch.object(bot, "test_connection", return_value=True),
        patch.object(bot, "get_instrument_filters", return_value={"min_qty": "0"}),
        patch.object(bot, "open_long", return_value={"retCode": 0}) as open_long,
        patch.object(bot, "open_short", return_value={"retCode": 0}) as open_short,
        patch.object(bot, "log_trade_to_csv") as log_trade,
        patch.object(bot, "send_telegram_message") as telegram,
        patch.object(bot.session, "get_server_time", return_value={"retCode": 0, "time": 1700000000000}),
        patch.object(
            bot.session,
            "get_executions",
            return_value={"retCode": 0, "result": {"list": []}},
        ) as get_executions,
        patch.object(bot.session, "get_positions", side_effect=position_responses) as get_positions,
        patch.object(bot.session, "get_closed_pnl", side_effect=closed_pnl_responses) as get_closed_pnl,
        patch.object(bot.time, "sleep", side_effect=fake_sleep),
        patch.object(bot.time, "time", return_value=1000.0),
    ):
        bot.main()

    return SimpleNamespace(
        open_long=open_long,
        open_short=open_short,
        log_trade=log_trade,
        telegram=telegram,
        get_positions=get_positions,
        get_closed_pnl=get_closed_pnl,
        get_executions=get_executions,
    )


def test_r1_old_closed_pnl_does_not_close_while_exchange_size_is_positive():
    """R1: an old closed-pnl record must not reset state while size is 10."""
    mocks = run_main(
        [EMPTY_CLOSED_PNL, OLD_CLOSED_PNL, EMPTY_CLOSED_PNL],
        iterations=3,
    )

    # The closing check must consult the exchange and see size=10.
    assert mocks.get_positions.call_count >= 2
    assert mocks.get_closed_pnl.call_count == 0

    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert mocks.open_long.call_count == 1
    assert mocks.open_short.call_count == 0
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)


def test_open_position_without_closed_pnl_record_stays_open():
    """Control: while closed-pnl is empty, an open size=10 position is not closed."""
    mocks = run_main(
        [EMPTY_CLOSED_PNL, EMPTY_CLOSED_PNL],
        iterations=2,
    )

    assert mocks.get_positions.call_count == 3
    assert mocks.get_closed_pnl.call_count == 0
    assert mocks.open_long.call_count == 1
    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)


def test_old_closed_pnl_does_not_close_when_exchange_size_is_zero():
    """Even at size=0, a record created before opened_at is stale."""
    mocks = run_main(
        [OLD_CLOSED_PNL, OLD_CLOSED_PNL],
        iterations=2,
        position_responses=[OPEN_POSITION, CLOSED_POSITION, CLOSED_POSITION],
    )

    assert mocks.get_positions.call_count == 3
    assert mocks.get_closed_pnl.call_count == 2
    assert mocks.open_long.call_count == 1
    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)


def test_fresh_closed_pnl_closes_position_once():
    """A real close is still detected: size=0 plus a record after opened_at."""
    mocks = run_main(
        [FRESH_CLOSED_PNL],
        iterations=1,
        position_responses=[OPEN_POSITION, CLOSED_POSITION],
    )

    assert mocks.get_positions.call_count == 2
    assert mocks.get_closed_pnl.call_count == 1
    assert mocks.open_long.call_count == 1

    statuses = [call.kwargs["status"] for call in mocks.log_trade.call_args_list]
    assert statuses == ["Open", "closed"]

    close_query = mocks.get_closed_pnl.call_args.kwargs
    assert "endTime" in close_query
    assert close_query["limit"] == 100

    close_call = mocks.log_trade.call_args_list[1]
    assert close_call.kwargs["exit_price"] == 0.6279
    assert close_call.kwargs["pnl"] == -0.001547
    assert close_call.kwargs["qty"] == "10"
    assert any("Закрыта long-позиция" in call.args[0] for call in mocks.telegram.call_args_list)


def test_position_api_error_does_not_close_position():
    """If get_positions fails, the position state must not be reset."""
    mocks = run_main(
        [],
        iterations=1,
        position_responses=[OPEN_POSITION, POSITION_API_ERROR],
    )

    assert mocks.get_positions.call_count == 2
    assert mocks.get_closed_pnl.call_count == 0
    assert mocks.open_long.call_count == 1
    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)


def test_closed_pnl_api_error_does_not_close_position():
    """A non-zero retCode on closed-pnl must not be treated as a close."""
    mocks = run_main(
        [CLOSED_PNL_API_ERROR],
        iterations=1,
        position_responses=[OPEN_POSITION, CLOSED_POSITION],
    )

    assert mocks.get_positions.call_count == 2
    assert mocks.get_closed_pnl.call_count == 1
    assert mocks.open_long.call_count == 1
    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)


def test_resolve_opened_at_prefers_execution_time():
    """The exact first fill time wins over the pre-order timestamp."""
    with patch.object(
        bot.session,
        "get_executions",
        return_value={"retCode": 0, "result": {"list": [{"execTime": "1700000000500"}]}},
    ):
        opened_at = bot._resolve_opened_at("order-1", 1700000000000)

    assert opened_at == 1700000000500


def test_resolve_opened_at_falls_back_to_sent_at_when_executions_empty():
    """Without executions, the pre-order Bybit time is used."""
    with patch.object(
        bot.session,
        "get_executions",
        return_value={"retCode": 0, "result": {"list": []}},
    ):
        opened_at = bot._resolve_opened_at("order-1", 1700000000000)

    assert opened_at == 1700000000000


def test_resolve_opened_at_uses_sent_at_without_order_id():
    """If place_order did not return an id, the pre-order time is used."""
    assert bot._resolve_opened_at(None, 1700000000000) == 1700000000000


def test_closed_pnl_rejects_record_from_another_position():
    """The old 0.6279 record has a different qty/entry and must be rejected."""
    position = {
        "side": "long",
        "entry": "0.5358",
        "qty": "100",
        "opened_at": 1700000000000,
        "entry_sent_at": 1700000000000,
    }
    old_record = {
        "createdTime": "1700000001000",
        "closedSize": "10",
        "qty": "10",
        "side": "Sell",
        "avgEntryPrice": "0.6268",
    }

    assert not bot._closed_pnl_matches_position(old_record, position)
