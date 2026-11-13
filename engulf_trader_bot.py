# engulf_trader_bot.py
# Торговля на бирже по сигналам
import datetime
import logging
import os
import time
from typing import Any, cast
from uuid import uuid4

import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

from config import CATEGORY, POSITION_SIZE, STRATEGY_PARAMS, SYMBOL, TIMEFRAME
from strategy import calculating_signal
from trader import (
    get_balance,
    get_instrument_filters,
    log_trade_to_csv,
    open_long,
    open_short,
    send_telegram_message,
    test_connection,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
)

logger = logging.getLogger(__name__)

# Настройка прокси
# proxy_url = f"http://{os.getenv('PROXY_LOGIN')}:{os.getenv('PROXY_PASSWORD')}@{os.getenv('PROXY_HOST')}:{os.getenv('PROXY_PORT')}"
# os.environ["HTTP_PROXY"] = proxy_url
# os.environ["HTTPS_PROXY"] = proxy_url

# Настройка подключения к Байбит
session = HTTP(testnet=False, api_key=os.getenv("BYBIT_API_KEY"), api_secret=os.getenv("BYBIT_SECRET"))


def fetch_klines(limit=200):
    logger.info(f"📊 Загрузка данных для {SYMBOL} (категория: {CATEGORY})")
    klines = cast(
        dict[str, Any],
        session.get_kline(category=CATEGORY, symbol=SYMBOL, interval=TIMEFRAME, limit=limit),
    )
    ticker_df = pd.DataFrame(
        cast(dict[str, Any], klines.get("result")).get("list"),
        columns=["datetime", "open", "high", "low", "close", "volume", "turnover"],
    )
    ticker_df["datetime"] = pd.to_datetime(ticker_df["datetime"].astype("int64"), unit="ms")
    ticker_df = ticker_df.set_index("datetime")
    ticker_df = ticker_df.astype("float64")
    ticker_df = ticker_df.sort_index()
    ticker_df["date"] = cast(pd.DatetimeIndex, ticker_df.index).date
    logger.info(f"📈 Последняя цена в данных: {ticker_df['close'].iloc[-1]}")

    klines_daily = cast(
        dict[str, Any],
        session.get_kline(category=CATEGORY, symbol=SYMBOL, interval="D", limit=100),
    )
    daily_df = pd.DataFrame(
        cast(dict[str, Any], klines_daily.get("result")).get("list"),
        columns=["datetime", "open_d", "high_d", "low_d", "close_d", "volume_d", "turnover_d"],
    )
    daily_df = daily_df[["datetime", "close_d"]].copy()
    daily_df["datetime"] = pd.to_datetime(daily_df["datetime"].astype("int64"), unit="ms")
    daily_df = daily_df.set_index("datetime")
    daily_df["date"] = cast(pd.DatetimeIndex, daily_df.index).date
    daily_df["close_d"] = daily_df["close_d"].astype("float64")
    daily_df = daily_df.sort_index()
    # Сдвигаем MA на день назад: значение для дня D считается по закрытым
    # дневным свечам до D-1 включительно (текущая незакрытая свеча не влияет)
    close_d = cast(pd.Series, daily_df["close_d"])
    daily_df["ma_40"] = cast(pd.Series, close_d.rolling(window=40).mean()).shift(1)
    daily_df["ma_20"] = cast(pd.Series, close_d.rolling(window=20).mean()).shift(1)
    merged_df = ticker_df.merge(daily_df, how="left", on="date")
    merged_df["is_slowma_lower"] = merged_df["ma_20"] > merged_df["ma_40"]
    return merged_df


def _get_open_position_size() -> float | None:
    """Вернуть суммарный размер позиции на бирже или None при ошибке API."""
    pos_info = cast(dict[str, Any], session.get_positions(category=CATEGORY, symbol=SYMBOL))
    if pos_info.get("retCode") != 0:
        logger.error(f"❌ Не удалось получить позиции: {pos_info.get('retMsg')}")
        return None
    positions = (pos_info.get("result") or {}).get("list") or []
    return sum(float(item.get("size") or 0) for item in positions)


def _get_server_time_ms() -> int:
    """Серверное время Bybit в ms; локальное — только аварийный fallback."""
    try:
        response = cast(dict[str, Any], session.get_server_time())
        if response.get("retCode") == 0 and response.get("time"):
            return int(response["time"])
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить время Bybit: {e}")
    return int(time.time() * 1000)


def _get_entry_exec_time(order_id: str) -> int | None:
    """Время первого филла входного ордера по данным Bybit."""
    try:
        response = cast(
            dict[str, Any],
            session.get_executions(category=CATEGORY, symbol=SYMBOL, orderId=order_id),
        )
        if response.get("retCode") != 0:
            return None
        records = (response.get("result") or {}).get("list") or []
        times = [int(item["execTime"]) for item in records if item.get("execTime") and int(item["execTime"]) > 0]
        return min(times) if times else None
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить executions для {order_id}: {e}")
        return None


def _resolve_opened_at(order_id: str | None, sent_at: int) -> int:
    """Точное время входа: execution -> время до отправки ордера."""
    exec_time = _get_entry_exec_time(order_id) if order_id else None
    if exec_time:
        return exec_time

    return sent_at


def _closed_pnl_matches_position(record: dict[str, Any], position: dict[str, Any]) -> bool:
    """Сопоставить closed-pnl именно с текущей позицией."""
    try:
        opened_at = int(position.get("entry_sent_at") or position["opened_at"])
        if int(record["createdTime"]) < opened_at - 5000:
            return False

        position_qty = float(position.get("qty") or 0)
        record_qty = float(record.get("closedSize") or 0) or float(record.get("qty") or 0)
        if position_qty > 0 and abs(position_qty - record_qty) > 1e-9:
            return False

        expected_close_side = "Sell" if position["side"] == "long" else "Buy"
        if record.get("side") != expected_close_side:
            return False

        expected_entry = float(position["entry"])
        actual_entry = float(record["avgEntryPrice"])
        return abs(actual_entry - expected_entry) <= max(1e-9, abs(expected_entry) * 1e-6)
    except (KeyError, TypeError, ValueError):
        return False


def main():
    logger.info("=" * 60)
    logger.info("🤖 ТОРГОВЫЙ РОБОТ ЗАПУЩЕН (реальная торговля)")
    logger.info(f"Символ: {SYMBOL}")
    logger.info(f"Таймфрейм: {TIMEFRAME}")
    logger.info(f"Параметры: {STRATEGY_PARAMS}")
    logger.info(f"Размер позиции: {POSITION_SIZE} {SYMBOL[0:3]}")
    logger.info("=" * 60)
    send_telegram_message(
        f"🤖 <b>Робот Engulf  запущен</b>\n"
        f"Символ: {SYMBOL}\n"
        f"Таймфрейм: {TIMEFRAME}\n"
        f"Размер позиции: {POSITION_SIZE} {SYMBOL[0:3]}"
    )

    # Проверим подключение
    if not test_connection():
        logger.error("❌ Нет подключения к Bybit! Робот остановлен.")
        return

    # Минимальный размер ордера: если POSITION_SIZE меньше, ордер не выставится
    min_qty = float(get_instrument_filters(SYMBOL)["min_qty"])
    if float(POSITION_SIZE) < min_qty:
        logger.error(
            f"❌ POSITION_SIZE={POSITION_SIZE} меньше минимального ордера {min_qty} для {SYMBOL}. Робот остановлен."
        )
        return
    logger.info(f"Размер позиции: {POSITION_SIZE} {SYMBOL[0:3]} | Минимальный лот: {min_qty}")

    last_signal = None
    position = None
    last_heartbeat = time.time()

    while True:
        try:
            df = fetch_klines(limit=200)
            print(df.head())
            # Сигнал считаем только по закрытым свечам: последняя строка df — ещё формирующаяся свеча
            df_closed = df.iloc[:-1]
            signal, sl, tp = calculating_signal(
                df_closed, reward_risk=STRATEGY_PARAMS["reward_risk"], ma_fast=20, ma_slow=40
            )
            print(f"Signal: {signal, tp, sl}")
            logger.info(f"Signal: {signal, tp, sl}")
            current_price = df["close"].iloc[-1]

            if time.time() - last_heartbeat > 300:
                balance = get_balance("USDT")
                logger.info(f"💓 Heartbeat | USDT: {balance} | Цена: {current_price}")
                last_heartbeat = time.time()

            if signal == 1 and last_signal != 1 and not position:
                logger.info(f"🟢 LONG сигнал! Цена: {df['close'].iloc[-1]:.2f}")
                entry_sent_at = _get_server_time_ms()
                order_link_id = f"engulf-{entry_sent_at}-{uuid4().hex[:8]}"
                order = open_long(SYMBOL, POSITION_SIZE, tp=tp, sl=sl, order_link_id=order_link_id)
                if order:
                    order_id = (cast(dict[str, Any], order).get("result") or {}).get("orderId")
                    pos_info = cast(dict[str, Any], session.get_positions(category=CATEGORY, symbol=SYMBOL))
                    fill = cast(
                        list[dict[str, Any]],
                        cast(dict[str, Any], pos_info.get("result")).get("list"),
                    )[0]
                    position = {
                        "side": "long",
                        "entry": fill.get("avgPrice"),
                        "qty": fill.get("size"),
                        "opened_at": _resolve_opened_at(order_id, entry_sent_at),
                        "entry_sent_at": entry_sent_at,
                        "entry_order_id": order_id,
                        "entry_order_link_id": order_link_id,
                    }
                    last_signal = 1
                    logger.info(f"✅ LONG позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {position['entry']}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side="LONG",
                        qty=POSITION_SIZE,
                        entry_price=position["entry"],
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status="Open",
                    )
                    send_telegram_message(
                        f"🟢 <b>Открыта LONG-позиция </b>\n"
                        f"Символ: {SYMBOL}\n"
                        f"Цена: {position['entry']}\n"
                        f"Размер: {POSITION_SIZE} {SYMBOL[0:3]}\n"
                        f"TP: {tp}\n"
                        f"SL: {sl}\n"
                        f"Время: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

            elif signal == -1 and last_signal != -1 and not position:
                logger.info(f"🔴 SHORT сигнал! Цена: {df['close'].iloc[-1]:.2f}")
                entry_sent_at = _get_server_time_ms()
                order_link_id = f"engulf-{entry_sent_at}-{uuid4().hex[:8]}"
                order = open_short(SYMBOL, POSITION_SIZE, tp=tp, sl=sl, order_link_id=order_link_id)
                if order:
                    order_id = (cast(dict[str, Any], order).get("result") or {}).get("orderId")
                    pos_info = cast(dict[str, Any], session.get_positions(category=CATEGORY, symbol=SYMBOL))
                    fill = cast(
                        list[dict[str, Any]],
                        cast(dict[str, Any], pos_info.get("result")).get("list"),
                    )[0]
                    position = {
                        "side": "short",
                        "entry": fill.get("avgPrice"),
                        "qty": fill.get("size"),
                        "opened_at": _resolve_opened_at(order_id, entry_sent_at),
                        "entry_sent_at": entry_sent_at,
                        "entry_order_id": order_id,
                        "entry_order_link_id": order_link_id,
                    }
                    last_signal = -1
                    logger.info(f"✅ SHORT позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {position['entry']}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side="SHORT",
                        qty=POSITION_SIZE,
                        entry_price=position["entry"],
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status="Open",
                    )
                    send_telegram_message(
                        f"🔴 <b>Открыта SHORT-позиция</b>\n"
                        f"Символ: {SYMBOL}\n"
                        f"Цена: {position['entry']}\n"
                        f"Размер: {POSITION_SIZE} {SYMBOL[0:3]}\n"
                        f"TP: {tp}\n"
                        f"SL: {sl}\n"
                        f"Время: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

            # Закрытие подтверждаем только когда на бирже нет открытой позиции,
            # и есть closed-pnl, созданный после открытия текущей позиции
            if position:
                rec = None
                open_size = _get_open_position_size()
                if open_size == 0:
                    position_entry_sent_at = int(position.get("entry_sent_at") or position["opened_at"])
                    now_ms = _get_server_time_ms()
                    start_time = max(
                        position_entry_sent_at - 5000,
                        now_ms - 7 * 24 * 60 * 60 * 1000,
                    )
                    closed_pnl = cast(
                        dict[str, Any],
                        session.get_closed_pnl(
                            category=CATEGORY,
                            symbol=SYMBOL,
                            startTime=start_time,
                            endTime=now_ms,
                            limit=100,
                        ),
                    )
                    records = []
                    if closed_pnl.get("retCode") != 0:
                        logger.error(f"❌ Не удалось получить closed-pnl: {closed_pnl.get('retMsg')}")
                    else:
                        records = (closed_pnl.get("result") or {}).get("list") or []
                    matching = [record for record in records if _closed_pnl_matches_position(record, position)]
                    rec = max(matching, key=lambda item: int(item.get("createdTime", 0))) if matching else None
                if rec:
                    exit_price = float(rec["avgExitPrice"])
                    pnl = float(rec["closedPnl"])

                    logger.info(f"🔒 Позиция закрыта по {exit_price} (PnL {pnl})")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side=position["side"],
                        qty=rec["qty"],
                        entry_price=rec["avgEntryPrice"],
                        tp=None,
                        sl=None,
                        exit_price=exit_price,
                        pnl=pnl,
                        status="closed",
                    )
                    send_telegram_message(
                        f"<b>🔒 Закрыта {position['side']}-позиция</b>\n"
                        f"Символ: {SYMBOL}\n"
                        f"Цена: {exit_price}\n"
                        f"Размер: {rec['qty']} {SYMBOL[0:3]}\n"
                        f"PNL: {pnl}"
                    )
                    position = None
                    last_signal = None
            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("\n🛑 Робот остановлен")
            break
        except Exception as e:
            logger.info(f"❌ Ошибка: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
