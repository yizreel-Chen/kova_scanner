"""
alerts.py — 3 soft alert checks (do not filter out, only warn).

A  GAP_RISK   — >= 2 upward gaps in last 20 days
B  HIGH_VOL   — >= 3 days of volume > 1.5x 50-day avg in last 5 days
C  EXHAUST    — any single-day 8%+ gain on 2x+ avg volume in last 10 days

Gap definition: today's Low > yesterday's High × (1 + GAP_THRESHOLD)
"""

import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def _check_gap_risk(df: pd.DataFrame) -> tuple[bool, str]:
    recent = df.tail(config.GAP_LOOKBACK_DAYS + 1)
    gap_count = 0
    for i in range(1, len(recent)):
        today_low  = recent["Low"].iloc[i]
        prev_high  = recent["High"].iloc[i - 1]
        if today_low > prev_high * (1 + config.GAP_THRESHOLD):
            gap_count += 1
    if gap_count >= config.GAP_COUNT_TRIGGER:
        return True, f"GAP_RISK: {gap_count} upward gaps in last {config.GAP_LOOKBACK_DAYS}d"
    return False, ""


def _check_high_vol(df: pd.DataFrame) -> tuple[bool, str]:
    if len(df) < 55:
        return False, ""
    avg_vol_50 = df["Volume"].tail(50).mean()
    recent5    = df.tail(config.HIGH_VOL_LOOKBACK)
    high_days  = (recent5["Volume"] > avg_vol_50 * config.HIGH_VOL_MULTIPLIER).sum()
    if high_days >= config.HIGH_VOL_TRIGGER:
        return True, (f"HIGH_VOL: {high_days} of last {config.HIGH_VOL_LOOKBACK}d "
                      f"volume >{config.HIGH_VOL_MULTIPLIER}x avg")
    return False, ""


def _check_exhaust(df: pd.DataFrame) -> tuple[bool, str]:
    if len(df) < 55:
        return False, ""
    avg_vol_50 = df["Volume"].tail(50).mean()
    recent     = df.tail(config.EXHAUST_LOOKBACK)
    for i in range(1, len(recent)):
        open_  = recent["Open"].iloc[i]
        close_ = recent["Close"].iloc[i]
        vol_   = recent["Volume"].iloc[i]
        gain   = (close_ - open_) / open_ if open_ > 0 else 0
        if (gain >= config.EXHAUST_PRICE_THRESHOLD
                and vol_ >= avg_vol_50 * config.EXHAUST_VOL_MULTIPLIER):
            date_str = str(recent.index[i].date())
            return True, f"EXHAUST: {gain*100:.1f}% gain on {vol_/avg_vol_50:.1f}x vol ({date_str})"
    return False, ""


def compute_alerts(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """
    Returns (alert_codes, alert_details).
    alert_codes  : e.g. ['GAP_RISK', 'HIGH_VOL']
    alert_details: human-readable explanations
    """
    codes, details = [], []

    triggered, msg = _check_gap_risk(df)
    if triggered:
        codes.append("GAP_RISK")
        details.append(msg)

    triggered, msg = _check_high_vol(df)
    if triggered:
        codes.append("HIGH_VOL")
        details.append(msg)

    triggered, msg = _check_exhaust(df)
    if triggered:
        codes.append("EXHAUST")
        details.append(msg)

    return codes, details


def alert_priority(alert_codes: list[str]) -> str:
    """Returns priority label based on alert count."""
    n = len(alert_codes)
    if n == 0:
        return "CLEAN"
    elif n <= 2:
        return "CAUTION"
    else:
        return "HIGH_RISK"
