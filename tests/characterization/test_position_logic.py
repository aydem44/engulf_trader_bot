"""Characterization tests for risk R1 (false close by an old closed-pnl record).

The tests drive one or more iterations of ``engulf_trader_bot.main()`` with all
pybit calls mocked. They pin down the current, buggy behaviour before the
position-state logic is changed.
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

OLD_CLOSED_PNL_RECORD = {
    "avgEntryPrice": "0.60",
    "avgExitPrice": "0.6279",
    "closedPnl": "-0.001547",
    "qty": "10",
    "side": "Sell",
    "createdTime": "1600000000000",
    "orderId": "old-order-id",
}

EMPTY_CLOSED_PNL = {"retCode": 0, "result": {"list": []}}
OLD_CLOSED_PNL = {"retCode": 0, "result": {"list": [OLD_CLOSED_PNL_RECORD]}}


def run_main(closed_pnl_responses, iterations):
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
        patch.object(bot.session, "get_positions", return_value=OPEN_POSITION) as get_positions,
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
    )


def test_r1_old_closed_pnl_falsely_closes_open_position_and_allows_reentry():
    """R1: size=10 on the exchange, but an old closed-pnl record resets state.

    Current behaviour: the closing block does not call get_positions and does
    not compare the record with the position. It logs a close, clears
    ``position``/``last_signal`` and the next iteration opens LONG again.
    """
    mocks = run_main(
        [EMPTY_CLOSED_PNL, OLD_CLOSED_PNL, EMPTY_CLOSED_PNL],
        iterations=3,
    )

    # get_positions is only used when opening; the closing check never reads it.
    assert mocks.get_positions.call_count == 2
    assert mocks.get_positions.return_value["result"]["list"][0]["size"] == "10"

    # The old record is queried after opened_at but still accepted.
    assert mocks.get_closed_pnl.call_count == 3
    assert mocks.get_closed_pnl.call_args_list[1].kwargs["startTime"] == "1700000000000"

    # Characterization of the bug: Open -> false closed -> duplicate Open.
    statuses = [call.kwargs["status"] for call in mocks.log_trade.call_args_list]
    assert statuses == ["Open", "closed", "Open"]

    close_call = mocks.log_trade.call_args_list[1]
    assert close_call.kwargs["exit_price"] == 0.6279
    assert close_call.kwargs["pnl"] == -0.001547
    assert close_call.kwargs["qty"] == "10"

    assert mocks.open_long.call_count == 2
    assert mocks.open_short.call_count == 0
    assert any("Закрыта long-позиция" in call.args[0] for call in mocks.telegram.call_args_list)


def test_open_position_without_closed_pnl_record_stays_open():
    """Control: while closed-pnl is empty, an open size=10 position is not closed."""
    mocks = run_main(
        [EMPTY_CLOSED_PNL, EMPTY_CLOSED_PNL],
        iterations=2,
    )

    assert mocks.get_positions.call_count == 1
    assert mocks.open_long.call_count == 1
    assert [call.kwargs["status"] for call in mocks.log_trade.call_args_list] == ["Open"]
    assert not any("Закрыта" in call.args[0] for call in mocks.telegram.call_args_list)
