"""
universe.py — Fetch and cache the investable stock universe.

Strategy:
  1. Download ticker lists from NASDAQ FTP (nasdaqlisted.txt + otherlisted.txt)
  2. Filter to common stocks on NYSE / NASDAQ only
  3. Batch-download 60 days of OHLCV for all ~5000 candidates
  4. Apply price > $10 and avg_dollar_volume > $5M filters → ~1500-2000 tickers
  5. Fetch market cap for the filtered set via yfinance fast_info (threaded)
  6. Apply market_cap > $500M → final universe ~800-1500 tickers
  7. Store result in SQLite; refresh weekly
"""

import sqlite3
import time
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf
from tqdm import tqdm

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# ETF proxies for Russell 3000 — already market-cap filtered, ~3000 stocks combined
ETF_UNIVERSE_TICKERS = ["IWV"]          # Russell 3000 (~3000 holdings)
ETF_FALLBACK = ["IWB", "IWM", "MDY"]   # Russell 1000 + 2000 + S&P MidCap 400


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    return sqlite3.connect(config.DB_PATH)


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            scan_date DATE PRIMARY KEY,
            market_state TEXT,
            max_dd INTEGER,
            spy_price REAL,
            qqq_price REAL,
            total_passed INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS passed_stocks (
            scan_date DATE,
            ticker TEXT,
            price REAL,
            rs_rating INTEGER,
            adr_pct REAL,
            dist_50ema REAL,
            dist_high REAL,
            alerts TEXT,
            PRIMARY KEY (scan_date, ticker),
            FOREIGN KEY (scan_date) REFERENCES snapshots(scan_date)
        );

        CREATE TABLE IF NOT EXISTS universe_cache (
            ticker TEXT PRIMARY KEY,
            market_cap REAL,
            avg_dollar_volume REAL,
            exchange TEXT,
            last_updated TIMESTAMP
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Ticker list download
# ---------------------------------------------------------------------------

def _fetch_nasdaq_tickers() -> list[str]:
    """Download NASDAQ-listed tickers and return common stock symbols."""
    try:
        resp = requests.get(NASDAQ_LISTED_URL, timeout=30)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        tickers = []
        for line in lines[1:]:  # skip header
            parts = line.split("|")
            if len(parts) < 7:
                continue
            symbol = parts[0].strip()
            etf_flag = parts[6].strip()
            test_issue = parts[3].strip()
            fin_status = parts[4].strip()
            if etf_flag != "N" or test_issue != "N":
                continue
            if fin_status not in ("N", ""):
                continue
            # Skip symbols with special chars (warrants, rights, preferred, units)
            if any(c in symbol for c in ["^", "+", "~", "%", "*", "#", "!", "$"]):
                continue
            tickers.append(symbol)
        logger.info(f"NASDAQ listed: {len(tickers)} common stocks")
        return tickers
    except Exception as e:
        logger.warning(f"Failed to fetch NASDAQ listed: {e}")
        return []


def _fetch_other_tickers() -> list[str]:
    """Download NYSE and other listed tickers and return NYSE common stocks."""
    try:
        resp = requests.get(OTHER_LISTED_URL, timeout=30)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        tickers = []
        for line in lines[1:]:  # skip header
            parts = line.split("|")
            if len(parts) < 8:
                continue
            symbol = parts[0].strip()
            exchange = parts[2].strip()
            etf_flag = parts[4].strip()
            test_issue = parts[6].strip()
            if etf_flag != "N" or test_issue != "N":
                continue
            # N = NYSE, A = NYSE American
            if exchange not in ("N", "A"):
                continue
            if any(c in symbol for c in ["^", "+", "~", "%", "*", "#", "!", ".", "$"]):
                continue
            tickers.append(symbol)
        logger.info(f"NYSE/AMEX listed: {len(tickers)} common stocks")
        return tickers
    except Exception as e:
        logger.warning(f"Failed to fetch other listed: {e}")
        return []


# ---------------------------------------------------------------------------
# Batch download helpers
# ---------------------------------------------------------------------------

def _download_batch(tickers: list[str], period: str = "3mo",
                    max_retries: int = None) -> pd.DataFrame:
    """Download OHLCV for a batch; retries with backoff on rate-limit errors."""
    if not tickers:
        return pd.DataFrame()
    if max_retries is None:
        max_retries = config.MAX_BATCH_RETRIES
    tickers_str = " ".join(tickers)
    for attempt in range(max_retries + 1):
        try:
            data = yf.download(
                tickers=tickers_str,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            return data
        except Exception as e:
            err = str(e)
            if "rate" in err.lower() or "429" in err or "too many" in err.lower():
                if attempt < max_retries:
                    wait = 10 * (2 ** attempt) + random.uniform(0, 3)
                    logger.info(f"Rate limited — waiting {wait:.0f}s before retry {attempt+1}/{max_retries}")
                    time.sleep(wait)
                    continue
            logger.warning(f"Batch download failed: {e}")
            return pd.DataFrame()
    return pd.DataFrame()


def _extract_ticker_df(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Extract single ticker's OHLCV from a potentially multi-index DataFrame.

    yfinance 1.3+: MultiIndex (Ticker, Metric) — level-0 is the ticker.
    Older:         MultiIndex (Metric, Ticker) — level-1 is the ticker.
    """
    if data.empty:
        return None
    try:
        if isinstance(data.columns, pd.MultiIndex):
            l0 = data.columns.get_level_values(0).tolist()
            l1 = data.columns.get_level_values(1).tolist()
            if ticker in l0:
                df = data[ticker].copy()
            elif ticker in l1:
                df = data.xs(ticker, level=1, axis=1).copy()
            else:
                return None
        else:
            df = data.copy()
        df.columns = [str(c).capitalize() for c in df.columns]
        df = df.dropna(subset=["Close", "Volume"])
        return df if len(df) >= 10 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Market cap fetching (threaded)
# ---------------------------------------------------------------------------

def _fetch_market_cap(ticker: str) -> tuple[str, float]:
    """Fetch market cap for a single ticker using yfinance fast_info."""
    try:
        t = yf.Ticker(ticker)
        mc = t.fast_info.market_cap
        if mc and mc > 0:
            return ticker, float(mc)
    except Exception:
        pass
    return ticker, 0.0


def _batch_market_caps(tickers: list[str], max_workers: int = 20) -> dict[str, float]:
    """Fetch market caps for many tickers concurrently."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_fetch_market_cap, t): t for t in tickers}
        for fut in tqdm(as_completed(futures), total=len(tickers),
                        desc="Fetching market caps", unit="ticker"):
            ticker, mc = fut.result()
            results[ticker] = mc
    return results


# ---------------------------------------------------------------------------
# ETF holdings universe (primary method — avoids mass ticker download)
# ---------------------------------------------------------------------------

def _fetch_etf_tickers(etf_symbols: list[str]) -> list[str]:
    """
    Pull holdings from one or more ETFs via yfinance funds_data.
    NOTE: yfinance only returns top ~10 holdings; this method is kept as a probe
    and the caller falls back to NASDAQ FTP when < 500 tickers are returned.
    """
    tickers = set()
    for etf in etf_symbols:
        try:
            t = yf.Ticker(etf)
            holdings = None
            for attr in ("get_holdings", "holdings"):
                try:
                    val = getattr(t, attr)
                    holdings = val() if callable(val) else val
                    if holdings is not None and not holdings.empty:
                        break
                except Exception:
                    pass
            if holdings is None or (hasattr(holdings, 'empty') and holdings.empty):
                try:
                    fd = t.funds_data
                    holdings = fd.top_holdings if fd else None
                except Exception:
                    pass
            if holdings is not None and hasattr(holdings, 'index'):
                if 'Symbol' in holdings.columns:
                    h_syms = holdings['Symbol'].dropna().tolist()
                else:
                    h_syms = holdings.index.tolist()
                for s in h_syms:
                    s = str(s).strip().upper()
                    if s and len(s) <= 5 and s.isalpha():
                        tickers.add(s)
                logger.info(f"{etf}: got {len(h_syms)} holdings (yfinance fallback)")
        except Exception as e:
            logger.warning(f"Failed to fetch {etf} holdings: {e}")

    return list(tickers)


# ---------------------------------------------------------------------------
# Universe build
# ---------------------------------------------------------------------------

def _build_universe(verbose: bool = False) -> list[dict]:
    """
    Build the full universe from scratch.
    Strategy: try ETF holdings (IWV) first — already market-cap filtered, ~3000 tickers,
    no separate market-cap fetch needed. Fall back to NASDAQ FTP if ETF approach fails.
    Returns list of dicts with keys: ticker, exchange, market_cap, avg_dollar_volume
    """
    # --- Method A: ETF holdings (preferred — smaller, no market cap step) ---
    print("  Fetching Russell 3000 universe via IWV ETF holdings...")
    etf_tickers = _fetch_etf_tickers(ETF_UNIVERSE_TICKERS)
    if len(etf_tickers) < 500:
        print(f"  IWV returned only {len(etf_tickers)} tickers, trying fallback ETFs...")
        etf_tickers = _fetch_etf_tickers(ETF_FALLBACK)

    if len(etf_tickers) >= 500:
        print(f"  ETF universe: {len(etf_tickers)} tickers (market cap pre-filtered)")
        all_tickers = etf_tickers
        # For ETF-sourced tickers we trust market cap is already > $500M
        # Exchange label is approximated; doesn't affect filtering
        nasdaq_set = set()   # unknown — assign NASDAQ as default
        nyse_set   = set()
        use_etf_source = True
    else:
        # --- Method B: NASDAQ FTP (fallback) ---
        print("  ETF holdings unavailable — falling back to NASDAQ FTP...")
        nasdaq_tickers = _fetch_nasdaq_tickers()
        nyse_tickers   = _fetch_other_tickers()
        all_tickers    = list(set(nasdaq_tickers + nyse_tickers))
        nasdaq_set     = set(nasdaq_tickers)
        nyse_set       = set(nyse_tickers)
        use_etf_source = False

    print(f"  Total candidate tickers: {len(all_tickers)}")

    # Assign exchange labels (best-effort for ETF source)
    if not use_etf_source:
        pass  # nasdaq_set / nyse_set already set

    # Step 1: filter by price and dollar volume using 3-month history
    print("  Downloading 3-month price/volume data (price & liquidity filter)...")
    price_vol_filtered: dict[str, dict] = {}

    batches = [all_tickers[i:i + config.DOWNLOAD_BATCH_SIZE]
               for i in range(0, len(all_tickers), config.DOWNLOAD_BATCH_SIZE)]

    for i, batch in enumerate(tqdm(batches, desc="Price/volume scan", unit="batch")):
        data = _download_batch(batch, period="3mo")
        if data.empty:
            if i < len(batches) - 1:
                time.sleep(config.BATCH_SLEEP_SECONDS)
            continue
        for ticker in batch:
            df = _extract_ticker_df(data, ticker)
            if df is None or len(df) < 20:
                continue
            last_close = df["Close"].iloc[-1]
            if last_close < config.MIN_PRICE:
                continue
            dollar_vol = (df["Close"] * df["Volume"]).tail(20).mean()
            if dollar_vol < config.MIN_DOLLAR_VOLUME:
                continue
            if use_etf_source:
                exchange = "NASDAQ"  # approximate; acceptable since ETF covers both
            else:
                exchange = "NASDAQ" if ticker in nasdaq_set else "NYSE"
            price_vol_filtered[ticker] = {
                "ticker": ticker,
                "exchange": exchange,
                "avg_dollar_volume": float(dollar_vol),
            }
        if i < len(batches) - 1:
            time.sleep(config.BATCH_SLEEP_SECONDS)

    print(f"  After price/volume filter: {len(price_vol_filtered)} tickers")

    if not price_vol_filtered:
        logger.error("Universe build yielded zero tickers after price/volume filter")
        return []

    universe = []

    if use_etf_source:
        # ETF holdings are already market-cap filtered — skip the slow cap fetch
        print("  Skipping market cap fetch (ETF source pre-filters by market cap)")
        for ticker, attrs in price_vol_filtered.items():
            attrs["market_cap"] = 0.0   # unknown but trusted via ETF membership
            universe.append(attrs)
    else:
        # NASDAQ FTP source: need explicit market cap check
        # Brief pause so Yahoo Finance can recover from the price/volume download
        print("  Pausing before market cap fetch (rate-limit recovery)...")
        time.sleep(20)
        print("  Fetching market caps (threaded)...")
        caps = _batch_market_caps(
            list(price_vol_filtered.keys()),
            max_workers=config.MAX_MARKET_CAP_THREADS,
        )

        valid_caps = sum(1 for mc in caps.values() if mc and mc >= config.MIN_MARKET_CAP)
        mc_coverage = valid_caps / len(caps) if caps else 0

        if mc_coverage < 0.20:
            logger.warning(
                f"Market cap API returned valid data for only {mc_coverage*100:.0f}% "
                f"of tickers — skipping filter (dollar-volume proxy in effect)"
            )
            print(f"  ⚠ Market cap data unreliable ({mc_coverage*100:.0f}% coverage) — "
                  f"skipping market cap filter")
            for ticker, attrs in price_vol_filtered.items():
                attrs["market_cap"] = caps.get(ticker, 0.0)
                universe.append(attrs)
        else:
            for ticker, attrs in price_vol_filtered.items():
                mc = caps.get(ticker, 0.0)
                if mc < config.MIN_MARKET_CAP:
                    continue
                attrs["market_cap"] = mc
                universe.append(attrs)

    print(f"  Final universe: {len(universe)} tickers")
    return universe


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _save_universe(universe: list[dict], conn: sqlite3.Connection):
    now = datetime.now().isoformat()
    conn.execute("DELETE FROM universe_cache")
    for u in universe:
        conn.execute(
            """INSERT OR REPLACE INTO universe_cache
               (ticker, market_cap, avg_dollar_volume, exchange, last_updated)
               VALUES (?, ?, ?, ?, ?)""",
            (u["ticker"], u["market_cap"], u["avg_dollar_volume"], u["exchange"], now),
        )
    conn.commit()


def _load_universe(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT ticker, market_cap, avg_dollar_volume, exchange, last_updated FROM universe_cache"
    )
    rows = cur.fetchall()
    return [
        {"ticker": r[0], "market_cap": r[1], "avg_dollar_volume": r[2],
         "exchange": r[3], "last_updated": r[4]}
        for r in rows
    ]


def _is_cache_stale(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT MIN(last_updated) FROM universe_cache")
    row = cur.fetchone()
    if not row or not row[0]:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).days >= config.UNIVERSE_REFRESH_DAYS
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_universe(force_refresh: bool = False, verbose: bool = False) -> list[str]:
    """
    Return list of tickers in the current universe.
    Rebuilds from scratch if cache is stale (>= UNIVERSE_REFRESH_DAYS old) or forced.
    """
    conn = _get_conn()
    _ensure_tables(conn)

    stale = _is_cache_stale(conn)
    if force_refresh or stale:
        reason = "forced" if force_refresh else "stale/missing"
        print(f"  Universe cache is {reason}, rebuilding...")
        universe = _build_universe(verbose=verbose)
        if universe:
            _save_universe(universe, conn)
        conn.close()
        return [u["ticker"] for u in universe]
    else:
        universe = _load_universe(conn)
        conn.close()
        return [u["ticker"] for u in universe]


def get_universe_info() -> list[dict]:
    """Return full universe info (ticker + metadata) from cache."""
    conn = _get_conn()
    _ensure_tables(conn)
    result = _load_universe(conn)
    conn.close()
    return result
