# Kova Scanner

Local US stock scanner based on the Kova/CAN SLIM/VCP system.  
Scans the full US market daily after market close and outputs a watchlist for manual chart review.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Daily scan (run after US market close — ~9:00 AM NZT)
python scanner.py

# First run / rebuild universe cache
python scanner.py --refresh-universe

# View historical snapshot
python scanner.py --date 2026-05-20

# List all historical scans
python scanner.py --history

# Diagnostic output
python scanner.py --verbose

# TradingView string only (for scripting)
python scanner.py --tv-only
```

## How it works

**Universe**: ~1000–1500 NYSE/NASDAQ common stocks filtered by price > $10, avg daily dollar volume > $5M, market cap > $500M. Cached weekly.

**Market regime**: Checks SPY/QQQ distribution days (25-day window, 5% rebound rule). Scanner stops completely during CORRECTION.

**8 hard filters** (all must pass):
1. Price > $10
2. ADR% > 4% (volatile leaders, not slow large-caps)
3. RS Rating ≥ 90 (95 when UNDER_PRESSURE) — IBD-style percentile rank
4. Price > 50 EMA
5. 10 EMA > 20 EMA
6. > 70% above 52-week low
7. ≤ 15% above 50 EMA (climax run guard — DDR-1)
8. Within 25% of 52-week high

**3 soft alerts** (warn, don't filter):
- `GAP_RISK`: ≥ 2 upward gaps in 20 days
- `HIGH_VOL`: ≥ 3 high-volume days in last 5
- `EXHAUST`: single-day ≥ 8% gain on 2× avg volume

**Output**: terminal table, CSV to `./output/`, tickers copied to clipboard for TradingView paste.

## Performance

- First run (universe build): 15–30 min
- Daily scan: 5–10 min

## Project structure

```
scanner.py          main entry point
config.py           all thresholds (edit here)
modules/
  universe.py       ticker universe fetch + SQLite cache
  market_regime.py  SPY/QQQ distribution day calculation
  metrics.py        EMA, ADR, RS raw score, RS rating
  filters.py        8 hard filters
  alerts.py         3 soft alerts
  snapshot.py       SQLite persistence + NEW/STILL/DROPPED diff
  output.py         terminal display, CSV, clipboard
data/
  kova_scanner.db   SQLite database
output/
  kova_YYYY-MM-DD.csv
```
