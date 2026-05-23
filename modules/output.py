"""
output.py — Terminal display, CSV export, clipboard copy.
"""

import os
import sys
from datetime import datetime

from tabulate import tabulate

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from modules.alerts import alert_priority


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v, decimals=1) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.{decimals}f}%"


def _status_icon(status: str) -> str:
    return {"NEW": "🆕", "STILL": "✓", "DROPPED": "✗"}.get(status, " ")


def _priority_icon(priority: str) -> str:
    return {"CLEAN": "⭐", "CAUTION": "⚠ ", "HIGH_RISK": "✗ "}.get(priority, " ")


def _market_state_color(state: str) -> str:
    icons = {
        "CONFIRMED_UPTREND": "✅",
        "UNDER_PRESSURE":    "⚠️ ",
        "CORRECTION":        "🛑",
    }
    return icons.get(state, "")


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def print_header(scan_date: str):
    tz_label = "NZT"  # user's timezone
    ts = datetime.now().strftime("%H:%M")
    print()
    print("=" * 60)
    print(f"  KOVA SCANNER  |  {scan_date} {ts} {tz_label}")
    print("=" * 60)


def print_market_state(ms: dict, step: str = "1/4"):
    print(f"\n【{step}】大盘环境检查")
    print(f"  SPY: ${ms['spy_price']:,.2f} | EMA50 ${ms['spy_ema50']:,.2f} "
          f"| DD={ms['spy_dd']} | 数据截至 {ms['spy_date']}")
    print(f"  QQQ: ${ms['qqq_price']:,.2f} | EMA50 ${ms['qqq_ema50']:,.2f} "
          f"| DD={ms['qqq_dd']} | 数据截至 {ms['qqq_date']}")
    print()
    icon = _market_state_color(ms['state'])
    print(f"  → 大盘状态：{icon} {ms['state']}")
    if ms['state'] == "CORRECTION":
        print("     市场处于回调期 — 本次扫描不输出候选股")
    elif ms['state'] == "UNDER_PRESSURE":
        print(f"     Distribution Days = {ms['max_dd']}（达到警戒）")
        print(f"     RS 阈值：{ms['rs_threshold']}")
    else:
        print(f"     Distribution Days = {ms['max_dd']}")
        print(f"     RS 阈值：{ms['rs_threshold']}")


def print_scan_progress(universe_size: int, data_available: int, step: str = "2/4"):
    print(f"\n【{step}】扫描全市场...")
    print(f"  Universe 加载：{universe_size:,} 只票")
    print(f"  数据可用：{data_available:,} 只")
    print("  扫描中...", end="", flush=True)


def print_diff_summary(new, still, dropped, prev_date: str | None, step: str = "3/4"):
    print(f"\n【{step}】对比历史快照")
    if prev_date:
        print(f"  基线：{prev_date} 快照")
        print(f"  NEW: {len(new)} | STILL: {len(still)} | DROPPED: {len(dropped)}")
    else:
        print("  首次运行，无历史快照基线")


def print_watchlist(passed: list[dict], new: list, still: list, dropped: list,
                    market_state: str, scan_date: str,
                    total_scanned: int, step: str = "4/4"):

    print(f"\n【{step}】Watchlist 输出")
    print("=" * 60)
    print(f"  通过 {len(passed)} 只 / 总扫描 {total_scanned:,} 只 | 状态: {market_state}")
    print("=" * 60)

    if not passed:
        print("  无符合条件股票")
        return

    new_set   = set(new)
    still_set = set(still)

    # Build table rows
    rows = []
    for s in passed:
        tk = s["ticker"]
        if tk in new_set:
            status = "NEW"
            st_icon = "🆕"
        else:
            status = "STILL"
            st_icon = "✓ "

        alert_codes = s.get("alert_codes", [])
        alerts_str  = ",".join(alert_codes) if alert_codes else "-"
        rs = s.get("rs_rating")
        rs_str = str(rs) if rs is not None else "N/A"

        rows.append([
            st_icon,
            tk,
            f"${s['price']:,.2f}",
            rs_str,
            f"{s.get('adr_pct', 0):.1f}",
            f"{s.get('dist_50ema', 0) * 100:+.1f}",
            f"{s.get('dist_high', 0) * 100:+.1f}",
            alerts_str,
        ])

    headers = ["St", "Ticker", "Price", "RS", "ADR%", "D50EMA%", "DHigh%", "Alerts"]
    print()
    print(tabulate(rows, headers=headers, tablefmt="simple"))

    # Alert details
    alert_stocks = [s for s in passed if s.get("alert_codes")]
    if alert_stocks:
        print("\n警报详情：")
        for s in alert_stocks:
            for detail in s.get("alert_details", []):
                print(f"  {s['ticker']} ⚠ {detail}")

    # Priority grouping
    clean     = [s["ticker"] for s in passed if alert_priority(s.get("alert_codes", [])) == "CLEAN"]
    caution   = [s["ticker"] for s in passed if alert_priority(s.get("alert_codes", [])) == "CAUTION"]
    high_risk = [s["ticker"] for s in passed if alert_priority(s.get("alert_codes", [])) == "HIGH_RISK"]

    print("\n看图优先级：")
    print(f"  ⭐ 优先看 (干净): {', '.join(clean) if clean else '（无）'}")
    print(f"  ⚠  谨慎看 (有警报): {', '.join(caution) if caution else '（无）'}")
    print(f"  ✗  跳过 (高风险): {', '.join(high_risk) if high_risk else '（无）'}")

    # NEW / STILL / DROPPED detail
    print(f"\n增量明细:")
    print(f"  🆕 NEW ({len(new)}): {', '.join(new) if new else '（无）'}")
    if new:
        print("     → 今日首次进入条件，重点看图")
    print(f"  ✓  STILL ({len(still)}): {', '.join(still) if still else '（无）'}")
    print(f"  ✗  DROPPED ({len(dropped)}): {', '.join(dropped) if dropped else '（无）'}")
    if dropped:
        print("     → 检查是否因走弱掉出 (跌破 50EMA / RS 滑落)")


def print_market_advice(market_state: str):
    if market_state == "UNDER_PRESSURE":
        print()
        print(f"⚠ UNDER_PRESSURE 状态实操建议：")
        print("  • 单票仓位降到 10-15% (不是 25%)")
        print("  • 最多新建 1-2 个仓位")
        print("  • 已有持仓上移止损 (21EMA 跟踪止损)")
        print("  • 优先看「⭐干净」组，跳过「✗高风险」组")
    elif market_state == "CORRECTION":
        print()
        print("🛑 CORRECTION 状态：建议不开新仓，保护已有仓位")


def _tv_string(stocks: list[dict]) -> str:
    parts = []
    for s in stocks:
        ex = s.get("exchange", "NASDAQ").upper()
        parts.append(f"{ex}:{s['ticker']}")
    return ",".join(parts)


def print_tradingview(stocks: list[dict]):
    tv_str = _tv_string(stocks)
    print()
    print("=" * 60)
    print("TradingView 粘贴格式（已复制到剪贴板）：")
    print("=" * 60)
    print(tv_str)


def print_footer(scan_date: str, elapsed: float, csv_path: str | None):
    print()
    print("=" * 60)
    mins  = int(elapsed // 60)
    secs  = int(elapsed % 60)
    saved = f"  |  TXT 已保存：{csv_path}" if csv_path else ""
    print(f"  完成  |  耗时 {mins}m {secs}s{saved}")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def save_txt(passed: list[dict], new: list, still: list,
             scan_date: str) -> str | None:
    """Save TradingView-ready ticker list as kova_YYYY-MM-DD.txt."""
    os.makedirs(config.CSV_OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.CSV_OUTPUT_DIR, f"kova_{scan_date}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_tv_string(passed))
        return path
    except Exception as e:
        print(f"  ⚠ TXT 保存失败: {e}")
        return None


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def copy_to_clipboard(stocks: list[dict]) -> bool:
    if not config.COPY_TO_CLIPBOARD or not stocks:
        return False
    try:
        import pyperclip
        pyperclip.copy(_tv_string(stocks))
        return True
    except Exception as e:
        print(f"  ⚠ 剪贴板复制失败: {e}")
        return False


# ---------------------------------------------------------------------------
# History viewer
# ---------------------------------------------------------------------------

def print_history(snapshots: list[str]):
    if not snapshots:
        print("  没有历史快照")
        return
    print("\n历史扫描记录：")
    for d in snapshots:
        print(f"  {d}")
    print()
