#!/usr/bin/env python
# coding: utf-8

# In[9]:


import os
import logging
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from config import CATEGORY
import csv
from datetime import datetime
import requests
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

load_dotenv()
logger = logging.getLogger(__name__)

proxy_host = os.getenv('PROXY_HOST')
proxy_port = os.getenv('PROXY_PORT')
proxy_login = os.getenv('PROXY_LOGIN')
proxy_password = os.getenv('PROXY_PASSWORD')
proxy_url = f"http://{proxy_login}:{proxy_password}@{proxy_host}:{proxy_port}"
os.environ['HTTP_PROXY'] = proxy_url
os.environ['HTTPS_PROXY'] = proxy_url

session = HTTP(
    testnet = False,
    api_key = os.getenv('BYBIT_API_KEY'),
    api_secret = os.getenv('BYBIT_SECRET')
)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')


_instrument_cache = {}


def get_instrument_filters(symbol):
    """Шаг цены (tickSize) и шаг количества (qtyStep) для символа (с кэшем)."""
    if symbol not in _instrument_cache:
        info = session.get_instruments_info(category=CATEGORY, symbol=symbol)
        item = info['result']['list'][0]
        _instrument_cache[symbol] = {
            'tick_size': Decimal(item['priceFilter']['tickSize']),
            'qty_step': Decimal(item['lotSizeFilter']['qtyStep']),
        }
    return _instrument_cache[symbol]


def round_to_step(value, step, rounding=ROUND_HALF_UP):
    """Округляет value к ближайшему кратному step (без ошибок float)."""
    return (Decimal(str(value)) / step).quantize(Decimal('1'), rounding=rounding) * step


# In[17]:


def get_balance(coin='USDT'):
    logger.info('Запрос баланса...')
    try:
        balance = session.get_wallet_balance(accountType='UNIFIED', coin=coin)
        return balance.get('result').get('list')[0].get('coin')[0].get('equity')
    except Exception as e:
        logger.error(f"Ошибка получения баланса: {e}")
        return None


# In[ ]:


def place_market_order(symbol, side, qty, tp, sl):
    order = None
    try:
        filters = get_instrument_filters(symbol)
        qty_r = round_to_step(qty, filters['qty_step'], ROUND_DOWN)
        tp_r = round_to_step(tp, filters['tick_size'])
        sl_r = round_to_step(sl, filters['tick_size'])
        logger.info(f"📈 Отправка ордера: {side} {qty_r} {symbol}. TP={tp_r} SL={sl_r}")
        order = session.place_order(
            category=CATEGORY,
            symbol=symbol,
            side=side,
            qty=format(qty_r, 'f'),
            orderType='Market',
            marketUnit='baseCoin',
            takeProfit=format(tp_r, 'f'),
            stopLoss=format(sl_r, 'f')
)
        logger.info(f"✅ Ордер исполнен: {order}")      
        return order

    except Exception as e:
        logger.error(f"❌ Ошибка ордера: {e}")
        return None   

def open_long(symbol, qty, tp, sl):
    logger.info(f"📤 Вызов open_long: {symbol} {qty} TP={tp} SL={sl}")
    order = place_market_order(symbol, 'Buy', qty, tp, sl)
    logger.info(f"📤 Результат open_long: {order}")    
    return order

def open_short(symbol, qty, tp, sl):
    logger.info(f"📤 Вызов open_short: {symbol} {qty} TP={tp} SL={sl}")
    order = place_market_order(symbol, 'Sell', qty, tp, sl)
    logger.info(f"📤 Результат open_short: {order}")  
    return order

# def close_position(symbol):
#     btc_balance = get_balance(coin='BTC')
#     if btc_balance and btc_balance > 0:
#         closed_position = open_short('BTCUSDT', btc_balance)
#         return closed_position
#     else:
#         logger.info("Нет позиции для закрытия")        
#         return None


# In[ ]:


def test_connection():
    try:
        balance = get_balance('USDT')
        if balance is None:
            logger.error("❌ Не удалось получить баланс USDT — нет соединения к Bybit или неверные ключи")
            return False
        logger.info(f"✅ Подключение к Bybit успешно!")
        logger.info(f"💰 Баланс USDT: {balance}")    
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")        
        return False


# In[ ]:


def log_trade_to_csv(symbol, side, qty, entry_price, tp, sl, exit_price=None, pnl=None, status=None):
    file_name = os.getenv('CSV_FILE')
    file_exist = os.path.isfile(file_name)
    with open(file_name, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exist:
            writer.writerow(['timestamp', 'symbol', 'side', 'qty', 
                'entry_price', 'tp', 'sl', 'exit_price', 
                'pnl', 'status'])
        writer.writerow([
            datetime.now().isoformat(),
            symbol,
            side,
            qty,
            round(float(entry_price), 6),
            round(float(tp), 6) if tp else None,
            round(float(sl), 6) if sl else None,
            round(float(exit_price), 6) if exit_price else None,
            round(float(pnl), 6) if pnl else None,
            status
        ])      



# In[ ]:


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram токен или chat_id не настроены в .env")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'            
        }
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            logger.debug("✅ Telegram сообщение отправлено")
        else:
            logger.error(f"❌ Ошибка отправки в Telegram: {response.text}")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения и отправки сообщения в Телеграм: {e}")

