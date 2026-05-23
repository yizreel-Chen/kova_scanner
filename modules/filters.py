"""
filters.py — 8 hard filters; all must pass.

Filter map:
  1  price > MIN_PRICE                        (usually pre-filtered in universe)
  2  ADR% > MIN_ADR_PCT
  3  RS Rating >= threshold (90 or 95)
  4  close > EMA50
  5  EMA10 > EMA20
  6  dist_low  > MIN_DIST_FROM_LOW  (+70% above 52w low)
  7  dist_50ema <= MAX_DIST_FROM_50EMA (+<=15% above 50 EMA, climax guard)
  8  dist_high >= -MAX_DIST_FROM_HIGH  (within 25% of 52w high)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def apply_filters(metrics: dict, rs_threshold: int) -> tuple[bool, list[str]]:
    """
    Returns (passed: bool, failed_filters: list[str]).
    `metrics` is the dict from metrics.compute_stock_metrics().
    `rs_threshold` is 90 (normal) or 95 (UNDER_PRESSURE).
    """
    failed = []

    # Filter 1: price
    if metrics["price"] <= config.MIN_PRICE:
        failed.append("F1_price")

    # Filter 2: ADR
    if metrics["adr_pct"] is None or metrics["adr_pct"] <= config.MIN_ADR_PCT:
        failed.append("F2_adr")

    # Filter 3: RS Rating
    rs = metrics.get("rs_rating")
    if rs is None or rs < rs_threshold:
        failed.append(f"F3_rs({rs})")

    # Filter 4: price > 50 EMA
    if metrics["price"] <= metrics["ema50"]:
        failed.append("F4_above_ema50")

    # Filter 5: EMA10 > EMA20
    if metrics["ema10"] <= metrics["ema20"]:
        failed.append("F5_ema_alignment")

    # Filter 6: > 70% above 52w low
    if metrics["dist_low"] <= config.MIN_DIST_FROM_LOW:
        failed.append(f"F6_dist_low({metrics['dist_low']:.2f})")

    # Filter 7: <= 15% above 50 EMA (climax guard)
    if metrics["dist_50ema"] > config.MAX_DIST_FROM_50EMA:
        failed.append(f"F7_climax({metrics['dist_50ema']:.2f})")

    # Filter 8: within 25% of 52w high
    if metrics["dist_high"] < -config.MAX_DIST_FROM_HIGH:
        failed.append(f"F8_dist_high({metrics['dist_high']:.2f})")

    return len(failed) == 0, failed
