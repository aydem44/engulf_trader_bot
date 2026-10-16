# engulf_trader_bot.py
# Торговля на бирже по сигналам
import pandas as pd
import os
import datetime
import logging
import time
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from config import STRATEGY_PARAMS, SYMBOL, TIMEFRAME, POSITION_SIZE, CATEGORY
from strategy import calculating_signal 
from trader import get_balance, open_long, open_short, test_connection, log_trade_to_csv, send_telegram_message, get_instrument_filters

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Настройка прокси
proxy_url = f"http://{os.getenv('PROXY_LOGIN')}:{os.getenv('PROXY_PASSWORD')}@{os.getenv('PROXY_HOST')}:{os.getenv('PROXY_PORT')}"
os.environ['HTTP_PROXY'] = proxy_url
os.environ['HTTPS_PROXY'] = proxy_url

# Настройка подключения к Байбит
session = HTTP(
    testnet=False,
    api_key=os.getenv('BYBIT_API_KEY'),
    api_secret=os.getenv('BYBIT_SECRET')
)

def fetch_klines(limit=200):
    logger.info(f"📊 Загрузка данных для {SYMBOL} (категория: {CATEGORY})")
    klines = session.get_kline(
        category=CATEGORY,
        symbol = SYMBOL,
        interval = TIMEFRAME,
        limit = limit)
    ticker_df = pd.DataFrame(klines.get('result').get('list'), columns=['datetime', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    ticker_df['datetime'] = pd.to_datetime(ticker_df['datetime'].astype('int64'), unit='ms')
    ticker_df.set_index('datetime', inplace=True)
    ticker_df = ticker_df.astype('float64')
    ticker_df = ticker_df.sort_index()
    ticker_df['date'] = ticker_df.index.date
    logger.info(f"📈 Последняя цена в данных: {ticker_df['close'].iloc[-1]}")
    
    klines_daily = session.get_kline(
        category=CATEGORY,
        symbol=SYMBOL,
        interval = 'D',
        limit = 100)    
    daily_df = pd.DataFrame(klines_daily.get('result').get('list'), columns=['datetime', 'open_d', 'high_d', 'low_d', 'close_d', 'volume_d', 'turnover_d'])
    daily_df = daily_df[['datetime', 'close_d']].copy()
    daily_df['datetime'] = pd.to_datetime(daily_df['datetime'].astype('int64'), unit='ms')
    daily_df.set_index('datetime', inplace=True)
    daily_df['date'] = daily_df.index.date
    daily_df['close_d'] = daily_df['close_d'].astype('float64')
    daily_df = daily_df.sort_index()
    # Сдвигаем MA на день назад: значение для дня D считается по закрытым
    # дневным свечам до D-1 включительно (текущая незакрытая свеча не влияет)
    daily_df['ma_40'] = daily_df['close_d'].rolling(window=40).mean().shift(1)
    daily_df['ma_20'] = daily_df['close_d'].rolling(window=20).mean().shift(1)
    merged_df = ticker_df.merge(daily_df, how = 'left', on='date')
    merged_df['is_slowma_lower'] = merged_df['ma_20']>merged_df['ma_40']
    return merged_df

def main():
    logger.info("="*60)
    logger.info("🤖 ТОРГОВЫЙ РОБОТ ЗАПУЩЕН (реальная торговля)")
    logger.info(f"Символ: {SYMBOL}")
    logger.info(f"Таймфрейм: {TIMEFRAME}")
    logger.info(f"Параметры: {STRATEGY_PARAMS}")
    logger.info(f"Размер позиции: {POSITION_SIZE} {SYMBOL[0:3]}")
    logger.info("="*60)
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
    min_qty = float(get_instrument_filters(SYMBOL)['min_qty'])
    if float(POSITION_SIZE) < min_qty:
        logger.error(f"❌ POSITION_SIZE={POSITION_SIZE} меньше минимального ордера {min_qty} для {SYMBOL}. Робот остановлен.")
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
            signal, sl, tp = calculating_signal(df_closed, reward_risk=STRATEGY_PARAMS['reward_risk'], ma_fast=20, ma_slow=40)
            print(f"Signal: {signal, tp, sl}")
            logger.info(f"Signal: {signal, tp, sl}")
            current_price = df['close'].iloc[-1]

            if time.time() - last_heartbeat > 300:
                balance = get_balance('USDT')
                logger.info(f"💓 Heartbeat | USDT: {balance} | Цена: {current_price}")
                last_heartbeat = time.time()

            if signal == 1 and last_signal != 1 and not position:
                logger.info(f"🟢 LONG сигнал! Цена: {df['close'].iloc[-1]:.2f}")
                order = open_long(SYMBOL, POSITION_SIZE, tp=tp, sl=sl)
                if order:
                    pos_info = session.get_positions(
                        category=CATEGORY,
                        symbol=SYMBOL
                    )
                    fill = pos_info.get('result').get('list')[0]
                    position = {'side' : 'long', 'entry' : fill.get('avgPrice'), 'qty' : fill.get('size'), 'opened_at' : fill.get('createdTime')}
                    last_signal = 1
                    logger.info(f"✅ LONG позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {position['entry']}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side='LONG',
                        qty=POSITION_SIZE,
                        entry_price=position['entry'],
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status='Open'
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
                order = open_short(SYMBOL, POSITION_SIZE, tp=tp, sl=sl)
                if order:
                    pos_info = session.get_positions(
                        category=CATEGORY,
                        symbol=SYMBOL
                    )
                    fill = pos_info.get('result').get('list')[0]
                    position = {'side' : 'short', 'entry' : fill.get('avgPrice'), 'qty' : fill.get('size'), 'opened_at' : fill.get('createdTime')}
                    last_signal = -1
                    logger.info(f"✅ SHORT позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {position['entry']}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side='SHORT',
                        qty=POSITION_SIZE,
                        entry_price=position['entry'],
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status='Open'
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

            # Закрытие определяем по данным биржи о закрытых сделках (не по размеру —
            # остатки-«пыль» больше не мешают)
            if position:
                closed_pnl = session.get_closed_pnl(
                    category=CATEGORY,
                    symbol=SYMBOL,
                    startTime=position['opened_at'],
                    limit=1
                )
                records = closed_pnl.get('result', {}).get('list', [])
                if records:
                    rec = records[0]
                    exit_price = float(rec['avgExitPrice'])
                    pnl = float(rec['closedPnl'])

                    logger.info(f"🔒 Позиция закрыта по {exit_price} (PnL {pnl})")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side=position['side'],
                        qty=rec['qty'],
                        entry_price=rec['avgEntryPrice'],
                        tp=None,
                        sl=None,
                        exit_price=exit_price,
                        pnl=pnl,
                        status='closed')
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

