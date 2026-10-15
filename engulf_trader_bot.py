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
from trader import get_balance, open_long, open_short, test_connection, log_trade_to_csv, send_telegram_message

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

    last_signal = None
    position = None
    last_heartbeat = time.time()

    while True:
        try:
            df = fetch_klines(limit=200)
            print(df.head())
            # Сигнал считаем только по закрытым свечам: последняя строка df — ещё формирующаяся свеча
            df_closed = df.iloc[:-1]
            signal, sl, tp = calculating_signal(df_closed, reward_risk=2, ma_fast=20, ma_slow=40) # Вынесем потом в конфиг
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
                    position = {'side' : 'long', 'entry' : pos_info.get('result').get('list')[0].get('avgPrice'), 'qty' : pos_info.get('result').get('list')[0].get('size')}
                    last_signal = 1
                    logger.info(f"✅ LONG позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {current_price:.2f}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side='LONG',
                        qty=POSITION_SIZE,
                        entry_price=current_price,
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status='Open'
                    )
                    send_telegram_message(
                        f"🟢 <b>Открыта LONG-позиция </b>\n"
                        f"Символ: {SYMBOL}\n"
                        f"Цена: {current_price}\n"
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
                    position = {'side' : 'short', 'entry' : pos_info.get('result').get('list')[0].get('avgPrice'), 'qty' : pos_info.get('result').get('list')[0].get('size')}
                    last_signal = -1
                    logger.info(f"✅ SHORT позиция открыта! {POSITION_SIZE} {SYMBOL[0:3]} по {current_price:.2f}")
                    log_trade_to_csv(
                        symbol=SYMBOL,
                        side='LONG',
                        qty=POSITION_SIZE,
                        entry_price=current_price,
                        tp=tp,
                        sl=sl,
                        exit_price=None,
                        pnl=None,
                        status='Open'
                    )
                    send_telegram_message(
                        f"🔴 <b>Открыта SHORT-позиция</b>\n"
                        f"Символ: {SYMBOL}\n"
                        f"Цена: {current_price}\n"
                        f"Размер: {POSITION_SIZE} {SYMBOL[0:3]}\n"
                        f"TP: {tp}\n"
                        f"SL: {sl}\n"
                        f"Время: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

            # Проверка, не закрылась ли позиция (по стопу/тейку)
            pos_info = session.get_positions(
                category=CATEGORY,
                symbol=SYMBOL
            )
            if float(pos_info.get('result').get('list')[0].get('size')) < 10 and position:

                exit_price = current_price
                pos_entry = float(position['entry'])
                pos_qty = float(position['qty'])

                if position['side'] == 'long':
                    pnl = (exit_price - pos_entry)/pos_entry
                else:
                    pnl = (pos_entry - exit_price)/pos_entry

                logger.info(f"🔒 Позиция закрыта (по стопу или тейку)")
                log_trade_to_csv(
                    symbol=SYMBOL,
                    side=position['side'],
                    qty=position['qty'],
                    entry_price=position['entry'],
                    tp=None,
                    sl=None,
                    exit_price=exit_price,
                    pnl=pnl,
                    status='closed')
                send_telegram_message(
                    f"<b>🔒 Закрыта {position['side']}-позиция</b>\n"
                    f"Символ: {SYMBOL}\n"
                    f"Цена: {exit_price}\n"
                    f"Размер: {position['qty']} {SYMBOL[0:3]}\n"
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

