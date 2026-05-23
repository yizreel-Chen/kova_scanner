#!/usr/bin/env python3
"""
scanner.py — Kova Scanner main entry point.

Usage:
  python scanner.py                    # run today's scan
  python scanner.py --date 2026-05-20  # view historical snapshot
  python scanner.py --history          # list all snapshot dates
  python scanner.py --refresh-universe # force rebuild universe cache
  python scanner.py --verbose          # extra diagnostic output
  python scanner.py --tv-only          # print only the TradingView ticker string
"""

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from tqdm import tqdm

from modules import universe as uni_mod
from modules import market_regime
from modules import metrics as metrics_mod
from modules import filters as filters_mod
from modules import alerts as alerts_mod
from modules import snapshot as snap_mod
from modules import output as out_mod
import config

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data download helpers
# ---------------------------------------------------------------------------

def _calc_start_date() -> str:
    return (datetime.now() - timedelta(days=config.HISTORY_LOOKBACK_DAYS * 1.5)).strftime("%Y-%m-%d")


def download_all_data(tickers: list[str], verbose: bool = False
                      ) -> dict[str, pd.DataFrame]:
    """
    Batch-download historical OHLCV for all universe tickers.
    Returns {ticker: DataFrame}.
    """
    start = _calc_start_date()
    batches = [tickers[i:i + config.DOWNLOAD_BATCH_SIZE]
               for i in range(0, len(tickers), config.DOWNLOAD_BATCH_SIZE)]

    all_data: dict[str, pd.DataFrame] = {}

    for i, batch in enumerate(tqdm(batches, desc="Downloading data", unit="batch",
                                   disable=not verbose)):
        tickers_str = " ".join(batch)
        raw = pd.DataFrame()
        for attempt in range(config.MAX_BATCH_RETRIES + 1):
            try:
                raw = yf.download(
                    tickers=tickers_str,
                    start=start,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
                break
            except Exception as e:
                err = str(e)
                if ("rate" in err.lower() or "429" in err or "too many" in err.lower()) \
                        and attempt < config.MAX_BATCH_RETRIES:
                    wait = 10 * (2 ** attempt) + random.uniform(0, 3)
                    logger.info(f"Rate limited — waiting {wait:.0f}s (retry {attempt+1})")
                    time.sleep(wait)
                else:
                    logger.warning(f"Batch download error: {e}")
                    break

        if not raw.empty:
            for ticker in batch:
                df = _extract_df(raw, ticker, len(batch))
                if df is not None and len(df) >= 60:
                    all_data[ticker] = df

        if i < len(batches) - 1:
            time.sleep(config.BATCH_SLEEP_SECONDS)

    return all_data


def _extract_df(raw: pd.DataFrame, ticker: str, batch_size: int) -> pd.DataFrame | None:
    """Extract single-ticker DataFrame from a potentially multi-indexed result.

    yfinance 1.3+ returns MultiIndex columns as (Ticker, Metric) i.e. level-0 = ticker.
    Older versions used (Metric, Ticker) i.e. level-0 = metric.
    Handle both, plus the single-ticker flat case.
    """
    try:
        if raw.empty:
            return None

        if isinstance(raw.columns, pd.MultiIndex):
            l0 = raw.columns.get_level_values(0).tolist()
            l1 = raw.columns.get_level_values(1).tolist()
            # yfinance 1.3+: level-0 = Ticker, level-1 = Metric
            if ticker in l0:
                df = raw[ticker].copy()
            # older yfinance: level-0 = Metric, level-1 = Ticker
            elif ticker in l1:
                df = raw.xs(ticker, level=1, axis=1).copy()
            else:
                return None
        else:
            df = raw.copy()

        # Normalize column names to Title Case
        df.columns = [str(c).capitalize() for c in df.columns]
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(set(df.columns)):
            return None
        df = df.dropna(subset=["Close", "Volume"])
        df = df[df["Volume"] > 0]
        return df if len(df) >= 20 else None
    except Exception as e:
        logger.debug(f"Extract failed for {ticker}: {e}")
        return None


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def run_scan(market_state: dict, tickers: list[str],
             verbose: bool = False, tv_only: bool = False) -> list[dict]:
    """
    Download data, compute metrics, apply filters and alerts.
    Returns list of passed stocks sorted by RS Rating desc.
    """
    rs_threshold = market_state["rs_threshold"]

    print(f"\n  Downloading data for {len(tickers):,} universe tickers...")
    t0 = time.time()
    stock_data = download_all_data(tickers, verbose=verbose)
    print(f"  Data available: {len(stock_data):,} tickers  "
          f"(download: {time.time()-t0:.0f}s)")

    # Compute RS ratings across full universe
    print("  Computing RS ratings...")
    rs_ratings = metrics_mod.compute_rs_ratings(stock_data)

    # Per-stock: metrics → filters → alerts
    passed = []
    fail_stats: dict[str, int] = {}

    for ticker, df in tqdm(stock_data.items(), desc="Scanning", unit="stock",
                           disable=not verbose):
        m = metrics_mod.compute_stock_metrics(ticker, df, rs_ratings)
        if m is None:
            fail_stats["_insufficient_data"] = fail_stats.get("_insufficient_data", 0) + 1
            continue

        ok, failed = filters_mod.apply_filters(m, rs_threshold)
        if not ok:
            for f in failed:
                key = f.split("(")[0]
                fail_stats[key] = fail_stats.get(key, 0) + 1
            continue

        alert_codes, alert_details = alerts_mod.compute_alerts(df)
        m["alert_codes"]   = alert_codes
        m["alert_details"] = alert_details
        passed.append(m)

    if verbose:
        print("\n  Filter failure breakdown:")
        for k, v in sorted(fail_stats.items(), key=lambda x: -x[1])[:10]:
            print(f"    {k}: {v}")

    # Sort by RS Rating desc, then by price (secondary)
    passed.sort(key=lambda x: (-(x.get("rs_rating") or 0), -x["price"]))
    return passed, len(stock_data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kova Scanner — US stock scanner")
    parser.add_argument("--date",             metavar="YYYY-MM-DD",
                        help="View historical snapshot for this date")
    parser.add_argument("--history",          action="store_true",
                        help="List all historical snapshot dates")
    parser.add_argument("--refresh-universe", action="store_true",
                        help="Force rebuild universe cache")
    parser.add_argument("--verbose",          action="store_true",
                        help="Show diagnostic output")
    parser.add_argument("--tv-only",          action="store_true",
                        help="Print only TradingView ticker string")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # ── History view ──────────────────────────────────────────────────────
    if args.history:
        dates = snap_mod.list_snapshots()
        out_mod.print_history(dates)
        return

    if args.date:
        meta, stocks = snap_mod.get_snapshot(args.date)
        if meta is None:
            print(f"No snapshot found for {args.date}")
            return
        if args.tv_only:
            print(",".join(s["ticker"] for s in stocks))
            return
        # Reconstruct diff context from saved data
        out_mod.print_header(args.date)
        ms_stub = {
            "state": meta["market_state"], "max_dd": meta["max_dd"],
            "spy_price": meta["spy_price"], "qqq_price": meta["qqq_price"],
            "spy_ema50": 0, "qqq_ema50": 0,
            "spy_dd": meta["max_dd"], "qqq_dd": 0,
            "spy_date": args.date, "qqq_date": args.date,
            "rs_threshold": config.MIN_RS_RATING_PRESSURE
                if meta["market_state"] == "UNDER_PRESSURE"
                else config.MIN_RS_RATING_DEFAULT,
        }
        out_mod.print_market_state(ms_stub)
        new, still, dropped, prev = snap_mod.compute_diff(
            [s["ticker"] for s in stocks], args.date
        )
        out_mod.print_diff_summary(new, still, dropped, prev)
        out_mod.print_watchlist(stocks, new, still, dropped,
                                meta["market_state"], args.date,
                                meta["total_passed"])
        exchange_map = {u["ticker"]: u["exchange"]
                        for u in uni_mod.get_universe_info()}
        for s in stocks:
            if "exchange" not in s:
                s["exchange"] = exchange_map.get(s["ticker"], "NASDAQ")
        out_mod.print_tradingview(stocks)
        out_mod.copy_to_clipboard(stocks)
        return

    # ── Live scan ─────────────────────────────────────────────────────────
    t_start = time.time()

    # Determine scan date (last US market close date)
    scan_date = datetime.now().strftime("%Y-%m-%d")

    out_mod.print_header(scan_date)

    # Step 1: Market regime
    print("\n【1/4】大盘环境检查")
    print("  Fetching SPY & QQQ data...")
    market_state = market_regime.get_market_state()
    out_mod.print_market_state(market_state, step="1/4")

    if market_state["state"] == "DATA_UNAVAILABLE":
        print("\n  ⚠ SPY/QQQ data unavailable (Yahoo Finance rate limit).")
        print("  Wait a few minutes and retry: python scanner.py")
        return

    if market_state["state"] == "CORRECTION":
        out_mod.print_market_advice("CORRECTION")
        print("\n  → 市场处于 CORRECTION，扫描已停止。")
        # Still save a snapshot with 0 stocks so history is recorded
        snap_mod.save_snapshot(scan_date, market_state, [])
        out_mod.print_footer(scan_date, time.time() - t_start, None)
        return

    # Step 2: Universe
    print("\n【2/4】加载 Universe...")
    tickers = uni_mod.get_universe(force_refresh=args.refresh_universe,
                                   verbose=args.verbose)
    if not tickers:
        print("  ERROR: Universe is empty. Try --refresh-universe")
        return

    # Build ticker→exchange map for output formatting
    exchange_map = {u["ticker"]: u["exchange"]
                    for u in uni_mod.get_universe_info()}

    print(f"  Universe: {len(tickers):,} tickers")

    # Step 2b: Run scan
    passed, total_scanned = run_scan(market_state, tickers,
                                     verbose=args.verbose, tv_only=args.tv_only)
    print(f" ✓ {len(passed)} stocks passed all filters")

    # Attach exchange to each passed stock
    for s in passed:
        s["exchange"] = exchange_map.get(s["ticker"], "NASDAQ")

    # Step 3: Diff
    passed_tickers = [s["ticker"] for s in passed]
    new, still, dropped, prev_date = snap_mod.compute_diff(passed_tickers, scan_date)

    out_mod.print_diff_summary(new, still, dropped, prev_date, step="3/4")

    # Save snapshot
    snap_mod.save_snapshot(scan_date, market_state, passed)

    if args.tv_only:
        print(",".join(passed_tickers))
        return

    # Step 4: Output
    out_mod.print_watchlist(passed, new, still, dropped,
                            market_state["state"], scan_date,
                            total_scanned, step="4/4")
    out_mod.print_market_advice(market_state["state"])

    # TradingView + clipboard (exchange-prefixed)
    out_mod.print_tradingview(passed)
    out_mod.copy_to_clipboard(passed)

    # TXT watchlist file
    txt_path = out_mod.save_txt(passed, new, still, scan_date)

    out_mod.print_footer(scan_date, time.time() - t_start, txt_path)


if __name__ == "__main__":
    main()
