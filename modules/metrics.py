"""
metrics.py — Per-stock indicator calculations.

All functions take a DataFrame with columns: Open, High, Low, Close, Volume
and return scalar values (floats or None on insufficient data).

RS raw score and RS rating (percentile) are computed batch-style across
all universe stocks so the ranking is meaningful.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Single-stock metrics
# ---------------------------------------------------------------------------

def get_ema_values(df: pd.DataFrame) -> dict | None:
    """Returns dict of EMA values at the last bar, or None if insufficient data."""
    if len(df) < 50:
        return None
    close = df["Close"]
    return {
        "ema10":  round(float(ema(close, 10).iloc[-1]), 4),
        "ema20":  round(float(ema(close, 20).iloc[-1]), 4),
        "ema21":  round(float(ema(close, 21).iloc[-1]), 4),
        "ema50":  round(float(ema(close, 50).iloc[-1]), 4),
    }


def get_adr_pct(df: pd.DataFrame, period: int = 20) -> float | None:
    """Average Daily Range % over last `period` bars."""
    if len(df) < period:
        return None
    recent = df.tail(period)
    daily_range = (recent["High"] - recent["Low"]) / recent["Low"]
    return round(float(daily_range.mean() * 100), 4)


def get_52w_high_low(df: pd.DataFrame) -> tuple[float, float] | None:
    """52-week (252 trading day) high and low of Close prices."""
    window = df.tail(252)
    if len(window) < 50:
        return None
    return (round(float(window["High"].max()), 4),
            round(float(window["Low"].min()), 4))


def get_rs_raw(df: pd.DataFrame) -> float | None:
    """
    IBD-style RS raw score (not yet percentile-ranked).
    Requires at least 253 bars.
    Weights: 63d×40%, 126d×20%, 189d×20%, 252d×20%
    """
    close = df["Close"]
    n = len(close)
    if n < 253:
        return None

    c0   = float(close.iloc[-1])
    c63  = float(close.iloc[-64])   # 63 trading days ago
    c126 = float(close.iloc[-127])
    c189 = float(close.iloc[-190])
    c252 = float(close.iloc[-253])

    r63  = c0 / c63  - 1
    r126 = c0 / c126 - 1
    r189 = c0 / c189 - 1
    r252 = c0 / c252 - 1

    return r63 * 0.4 + r126 * 0.2 + r189 * 0.2 + r252 * 0.2


# ---------------------------------------------------------------------------
# Batch RS rating across the universe
# ---------------------------------------------------------------------------

def compute_rs_ratings(stock_data: dict[str, pd.DataFrame]) -> dict[str, int]:
    """
    Given {ticker: df} for the full universe, compute RS ratings (0-99).
    Only tickers with valid rs_raw scores participate in the ranking.
    Returns {ticker: rs_rating}.
    """
    raw_scores: dict[str, float] = {}
    for ticker, df in stock_data.items():
        score = get_rs_raw(df)
        if score is not None and np.isfinite(score):
            raw_scores[ticker] = score

    if not raw_scores:
        return {}

    series = pd.Series(raw_scores)
    # percentile rank: 0-99
    ranked = series.rank(pct=True) * 99
    return {ticker: int(round(v)) for ticker, v in ranked.items()}


# ---------------------------------------------------------------------------
# Full metric bundle for a single stock
# ---------------------------------------------------------------------------

def compute_stock_metrics(ticker: str, df: pd.DataFrame,
                          rs_ratings: dict[str, int]) -> dict | None:
    """
    Compute all metrics needed for filtering and output.
    Returns None if data is insufficient.
    """
    if df is None or len(df) < 50:
        return None

    close = df["Close"].iloc[-1]
    emas = get_ema_values(df)
    if emas is None:
        return None

    adr = get_adr_pct(df)
    if adr is None:
        return None

    hl = get_52w_high_low(df)
    if hl is None:
        return None
    high52, low52 = hl

    rs_rating = rs_ratings.get(ticker)  # may be None if insufficient history

    ema50 = emas["ema50"]
    dist_50ema = (close - ema50) / ema50
    dist_high  = (close - high52) / high52
    dist_low   = (close - low52) / low52

    return {
        "ticker":       ticker,
        "price":        round(float(close), 2),
        "rs_rating":    rs_rating,
        "adr_pct":      adr,
        "ema10":        emas["ema10"],
        "ema20":        emas["ema20"],
        "ema21":        emas["ema21"],
        "ema50":        ema50,
        "high_52w":     high52,
        "low_52w":      low52,
        "dist_50ema":   round(float(dist_50ema), 4),
        "dist_high":    round(float(dist_high), 4),
        "dist_low":     round(float(dist_low), 4),
    }
