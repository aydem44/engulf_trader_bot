import pandas as pd
from datetime import datetime
import numpy as np

def calculating_signal(kline_df, reward_risk, ma_fast, ma_slow):
    """
    Генерирует торговый сигнал на основе паттерна "поглощение" (engulfing)
    по двум последним свечам.

    Логика:
        - LONG: предыдущая свеча медвежья, текущая закрылась выше её открытия,
          и тренд восходящий (is_slowma_lower == True).
        - SHORT: предыдущая свеча бычья, текущая закрылась ниже её открытия,
          и тренд нисходящий (is_slowma_lower == False).
        - Иначе сигнала нет. Риск рассчитывается от тела текущей свечи,
          тейк-профит — через заданный коэффициент risk/reward.

    Args:
        kline_df (pd.DataFrame): Биржевые данные. Должен содержать колонки
            'open', 'high', 'low', 'close' и 'is_slowma_lower' (bool-флаг
            восходящего тренда по скользящим средним). Используются две
            последние строки (iloc[-2] и iloc[-1]).
        reward_risk (float): Коэффициент риск/прибыль (во сколько раз
            тейк-профит больше риска).
        ma_fast (int): Период быстрой скользящей средней (в расчёте
            сигнала напрямую не используется).
        ma_slow (int): Период медленной скользящей средней (в расчёте
            сигнала напрямую не используется).

    Returns:
        tuple[int, str | None, str | None]:
            - entry_flag: 1 (LONG), -1 (SHORT), 0 (нет сигнала);
            - stop_loss: цена стоп-лосса строкой, либо None;
            - take_profit: цена тейк-профита строкой, либо None.
    """
  
    # Перевод входящего датафрейма в отдельные серии
    open_price = kline_df['open']
    high = kline_df['high']
    low = kline_df['low']
    close = kline_df['close']
    is_slowma_lower = kline_df['is_slowma_lower']

    # Начальная идентификация
    entry_flag=0    
    stop_loss = None
    take_profit = None
    trend_factor=is_slowma_lower.iloc[-2]
    
    prev_open=open_price.iloc[-2].item()
    prev_close=close.iloc[-2].item()
    curr_open=open_price.iloc[-1].item()
    curr_close=close.iloc[-1].item()
    prev_bullish_candle = prev_open<prev_close
    prev_bearish_candle = prev_open>prev_close

    if prev_bearish_candle and curr_close>prev_open and trend_factor:
        entry_flag=1
        risk=curr_close - curr_open
        if risk>0:
            stop_loss=risk/curr_open
            take_profit = risk*reward_risk/curr_close
        else:
            min_risk = curr_close * 0.01
            stop_loss = min_risk / curr_open
            take_profit = min_risk * reward_risk / curr_close
            print(f"⚠️ Использован минимальный риск: risk={risk} -> min_risk={min_risk}")
        stop_loss = str(curr_close-stop_loss*curr_close)
        take_profit = str(curr_close+take_profit*curr_close)    
    elif prev_bullish_candle and curr_close<prev_open and not trend_factor:
        entry_flag=-1
        risk=curr_open-curr_close
        if risk>0:
            stop_loss=risk/curr_open
            take_profit = risk*reward_risk/curr_close
        else:
            stop_loss=0.01
            take_profit = 0.01
        stop_loss = str(curr_close+stop_loss*curr_close)
        take_profit = str(curr_close-take_profit*curr_close)    
    else:
        entry_flag=0
        stop_loss=None
        take_profit=None
    return entry_flag, stop_loss, take_profit
