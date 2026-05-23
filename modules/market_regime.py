"""
market_regime.py — SPY/QQQ distribution day calculation and market state.

Distribution Day rules:
  1. Day's close declines >= 0.2% vs previous close
  2. Day's volume > previous day's volume
  3. Within the last DD_LOOKBACK trading days
  4. 5% rebound removal: if SPY/QQQ subsequently rallied >= 5% from that DD's close, remove it

Market states:
  CORRECTION      — SPY or QQQ below 50 EMA, OR max(DD) >= MAX_DD_CORRECTION
  UNDER_PRESSURE  — max(DD) >= MAX_DD_WARNING  (RS threshold raised to 95)
  CONFIRMED_UPTREND — otherwise
"""

import logging
import time
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _download_index(ticker: str, days: int = 400, retries: int = 3) -> pd.DataFrame | None:
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for attempt in range(retries):
        try:
            df = yf.download(ticker, start=start, interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                l0 = df.columns.get_level_values(0).tolist()
                if ticker in l0:
                    df = df[ticker]
                else:
                    df.columns = df.columns.get_level_values(0)
            df.columns = [c.capitalize() for c in df.columns]
            df = df.dropna(subset=["Close", "Volume"])
            if len(df) < 60:
                logger.warning(f"{ticker}: insufficient data ({len(df)} rows)")
                return None
            return df
        except Exception as e:
            err = str(e)
            if ("rate" in err.lower() or "429" in err or "too many" in err.lower()) \
                    and attempt < retries - 1:
                wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                logger.warning(f"{ticker} rate limited, retrying in {wait:.0f}s...")
                time.sleep(wait)
            else:
                logger.error(f"Failed to download {ticker}: {e}")
                return None
    return None


def _find_dd_dates(df: pd.DataFrame, lookback: int) -> list:
    """
    Find raw DD candidate dates within the last `lookback` trading days.
    Returns list of index labels (dates).
    """
    if len(df) < lookback + 1:
        lookback = len(df) - 1

    candidates = []
    # Indices in df corresponding to the last `lookback` rows
    window_indices = list(range(len(df) - lookback, len(df)))

    for idx in window_indices:
        if idx == 0:
            continue
        today = df.iloc[idx]
        prev  = df.iloc[idx - 1]

        pct_chg = (today["Close"] - prev["Close"]) / prev["Close"]
        if pct_chg > -0.002:          # must decline >= 0.2%
            continue
        if today["Volume"] <= prev["Volume"]:  # volume must be higher
            continue
        candidates.append(df.index[idx])

    return candidates


def _apply_rebound_rule(df: pd.DataFrame, candidate_dates: list,
                        threshold: float = 0.05) -> list:
    """
    Remove DDs where the index subsequently rallied >= threshold from that DD's close.
    Returns only the still-valid DD dates.
    """
    valid = []
    for dd_date in candidate_dates:
        dd_close = df.loc[dd_date, "Close"]
        after = df[df.index > dd_date]["Close"]
        if after.empty:
            valid.append(dd_date)
            continue
        max_close_after = after.max()
        rebound = (max_close_after - dd_close) / dd_close
        if rebound < threshold:
            valid.append(dd_date)
    return valid


def calculate_distribution_days(df: pd.DataFrame,
                                 lookback: int = None,
                                 rebound_threshold: float = None
                                 ) -> tuple[int, list, list]:
    """
    Returns (valid_dd_count, all_candidate_dates, valid_dd_dates).
    """
    if lookback is None:
        lookback = config.DD_LOOKBACK
    if rebound_threshold is None:
        rebound_threshold = config.DD_REBOUND_THRESHOLD

    candidates = _find_dd_dates(df, lookback)
    valid = _apply_rebound_rule(df, candidates, rebound_threshold)
    return len(valid), candidates, valid


def get_market_state() -> dict:
    """
    Fetch SPY and QQQ, compute DDs, return a dict with:
      state        : 'CORRECTION' | 'UNDER_PRESSURE' | 'CONFIRMED_UPTREND'
      spy_dd       : int
      qqq_dd       : int
      max_dd       : int
      spy_price    : float
      qqq_price    : float
      spy_ema50    : float
      qqq_ema50    : float
      spy_date     : str   (last data date)
      qqq_date     : str
      rs_threshold : int   (90 or 95)
      spy_candidates : list of date strings (all raw DD candidates)
      spy_valid      : list of date strings (after rebound rule)
      qqq_candidates : list of date strings
      qqq_valid      : list of date strings
    """
    spy_df = _download_index("SPY")
    qqq_df = _download_index("QQQ")

    result = {
        "state": "CORRECTION",
        "spy_dd": 0, "qqq_dd": 0, "max_dd": 0,
        "spy_price": 0.0, "qqq_price": 0.0,
        "spy_ema50": 0.0, "qqq_ema50": 0.0,
        "spy_date": "N/A", "qqq_date": "N/A",
        "rs_threshold": config.MIN_RS_RATING_PRESSURE,
        "spy_candidates": [], "spy_valid": [],
        "qqq_candidates": [], "qqq_valid": [],
    }

    if spy_df is None or qqq_df is None:
        logger.error("Cannot determine market state — index data unavailable after retries")
        # Return a special state that the caller can detect and abort cleanly
        result["state"] = "DATA_UNAVAILABLE"
        return result

    # SPY
    spy_ema50 = _ema(spy_df["Close"], 50).iloc[-1]
    spy_count, spy_cands, spy_valid = calculate_distribution_days(spy_df)
    spy_price = spy_df["Close"].iloc[-1]
    spy_date  = str(spy_df.index[-1].date())

    # QQQ
    qqq_ema50 = _ema(qqq_df["Close"], 50).iloc[-1]
    qqq_count, qqq_cands, qqq_valid = calculate_distribution_days(qqq_df)
    qqq_price = qqq_df["Close"].iloc[-1]
    qqq_date  = str(qqq_df.index[-1].date())

    max_dd = max(spy_count, qqq_count)

    result.update({
        "spy_dd": spy_count, "qqq_dd": qqq_count, "max_dd": max_dd,
        "spy_price": round(float(spy_price), 2),
        "qqq_price": round(float(qqq_price), 2),
        "spy_ema50": round(float(spy_ema50), 2),
        "qqq_ema50": round(float(qqq_ema50), 2),
        "spy_date": spy_date, "qqq_date": qqq_date,
        "spy_candidates": [str(d.date()) for d in spy_cands],
        "spy_valid":      [str(d.date()) for d in spy_valid],
        "qqq_candidates": [str(d.date()) for d in qqq_cands],
        "qqq_valid":      [str(d.date()) for d in qqq_valid],
    })

    # Determine state
    below_ema = (spy_price < spy_ema50) or (qqq_price < qqq_ema50)
    if below_ema or max_dd >= config.MAX_DD_CORRECTION:
        state = "CORRECTION"
    elif max_dd >= config.MAX_DD_WARNING:
        state = "UNDER_PRESSURE"
    else:
        state = "CONFIRMED_UPTREND"

    rs_threshold = (
        config.MIN_RS_RATING_PRESSURE
        if state == "UNDER_PRESSURE"
        else config.MIN_RS_RATING_DEFAULT
    )

    result["state"] = state
    result["rs_threshold"] = rs_threshold
    return result
