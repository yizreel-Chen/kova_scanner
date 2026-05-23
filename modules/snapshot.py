"""
snapshot.py — SQLite persistence and NEW/STILL/DROPPED diff.
"""

import sqlite3
import logging
from datetime import date, datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    return sqlite3.connect(config.DB_PATH)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save_snapshot(scan_date: str, market_state: dict, passed: list[dict]):
    """
    Persist scan results.
    scan_date: 'YYYY-MM-DD' (the last US trading date)
    market_state: dict from market_regime.get_market_state()
    passed: list of dicts from the main scan (with metrics + alerts)
    """
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO snapshots
               (scan_date, market_state, max_dd, spy_price, qqq_price, total_passed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_date, market_state["state"], market_state["max_dd"],
             market_state["spy_price"], market_state["qqq_price"], len(passed)),
        )
        # Clear previous entries for this date (in case of re-run)
        conn.execute("DELETE FROM passed_stocks WHERE scan_date = ?", (scan_date,))
        for s in passed:
            conn.execute(
                """INSERT INTO passed_stocks
                   (scan_date, ticker, price, rs_rating, adr_pct,
                    dist_50ema, dist_high, alerts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scan_date, s["ticker"], s["price"], s.get("rs_rating"),
                 s.get("adr_pct"), s.get("dist_50ema"), s.get("dist_high"),
                 ",".join(s.get("alert_codes", []))),
            )
        conn.commit()
        logger.info(f"Snapshot saved: {scan_date}, {len(passed)} stocks")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_latest_snapshot_date(before_date: str | None = None) -> str | None:
    """Return the most recent scan_date in snapshots (optionally before `before_date`)."""
    conn = _get_conn()
    try:
        if before_date:
            cur = conn.execute(
                "SELECT MAX(scan_date) FROM snapshots WHERE scan_date < ?",
                (before_date,)
            )
        else:
            cur = conn.execute("SELECT MAX(scan_date) FROM snapshots")
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_passed_tickers(scan_date: str) -> set[str]:
    """Return set of tickers that passed on a given scan_date."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT ticker FROM passed_stocks WHERE scan_date = ?", (scan_date,)
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def get_snapshot(scan_date: str) -> tuple[dict | None, list[dict]]:
    """Return (snapshot_meta, list_of_passed_stocks) for a given date."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT scan_date, market_state, max_dd, spy_price, qqq_price, total_passed "
            "FROM snapshots WHERE scan_date = ?", (scan_date,)
        )
        meta_row = cur.fetchone()
        if not meta_row:
            return None, []
        meta = {
            "scan_date": meta_row[0], "market_state": meta_row[1],
            "max_dd": meta_row[2], "spy_price": meta_row[3],
            "qqq_price": meta_row[4], "total_passed": meta_row[5],
        }
        cur2 = conn.execute(
            """SELECT ticker, price, rs_rating, adr_pct, dist_50ema, dist_high, alerts
               FROM passed_stocks WHERE scan_date = ?
               ORDER BY rs_rating DESC""",
            (scan_date,)
        )
        stocks = [
            {"ticker": r[0], "price": r[1], "rs_rating": r[2], "adr_pct": r[3],
             "dist_50ema": r[4], "dist_high": r[5],
             "alert_codes": r[6].split(",") if r[6] else []}
            for r in cur2.fetchall()
        ]
        return meta, stocks
    finally:
        conn.close()


def list_snapshots() -> list[str]:
    """Return all snapshot dates in descending order."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT scan_date FROM snapshots ORDER BY scan_date DESC")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Diff: NEW / STILL / DROPPED
# ---------------------------------------------------------------------------

def compute_diff(today_tickers: list[str],
                 today_date: str) -> tuple[list[str], list[str], list[str], str | None]:
    """
    Compare today's passed tickers against the most recent previous snapshot.
    Returns (new_tickers, still_tickers, dropped_tickers, prev_date | None).
    If no prior snapshot exists, all today's tickers are NEW and prev_date is None.
    """
    prev_date = get_latest_snapshot_date(before_date=today_date)
    if prev_date is None:
        return list(today_tickers), [], [], None

    prev_set  = get_passed_tickers(prev_date)
    today_set = set(today_tickers)

    new     = sorted(today_set - prev_set)
    still   = sorted(today_set & prev_set)
    dropped = sorted(prev_set - today_set)

    return new, still, dropped, prev_date


def get_prev_date_for_diff(today_date: str) -> str | None:
    return get_latest_snapshot_date(before_date=today_date)
