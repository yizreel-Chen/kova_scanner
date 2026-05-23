# config.py — all thresholds in one place; tweak here, never in modules

# ===== Universe Filtering =====
MIN_PRICE = 10.0
MIN_DOLLAR_VOLUME = 5_000_000      # $5M daily avg dollar volume (20-day)
MIN_MARKET_CAP = 500_000_000       # $500M
ALLOWED_EXCHANGES = ["NYSE", "NASDAQ"]

# ===== Market Regime (Distribution Days) =====
DD_LOOKBACK = 25                   # trading days window for DD counting
MAX_DD_WARNING = 4                 # >= 4 → UNDER_PRESSURE
MAX_DD_CORRECTION = 5              # >= 5 → CORRECTION
DD_REBOUND_THRESHOLD = 0.05        # 5% rebound removes a DD

# ===== Individual Stock Hard Filters =====
MIN_ADR_PCT = 4.0                  # filter 2: avg daily range %
MIN_RS_RATING_DEFAULT = 90         # filter 3: normal market
MIN_RS_RATING_PRESSURE = 95        # filter 3: UNDER_PRESSURE
MIN_DIST_FROM_LOW = 0.70           # filter 6: > 70% above 52w low
MAX_DIST_FROM_50EMA = 0.15         # filter 7: <= 15% above 50 EMA (climax guard)
MAX_DIST_FROM_HIGH = 0.25          # filter 8: within 25% of 52w high

# ===== Alert Thresholds =====
GAP_THRESHOLD = 0.005              # 0.5% gap definition
GAP_LOOKBACK_DAYS = 20
GAP_COUNT_TRIGGER = 2

HIGH_VOL_MULTIPLIER = 1.5          # > 1.5x 50-day avg volume
HIGH_VOL_LOOKBACK = 5
HIGH_VOL_TRIGGER = 3               # 3+ days out of last 5

EXHAUST_PRICE_THRESHOLD = 0.08     # single-day 8%+ gain
EXHAUST_VOL_MULTIPLIER = 2.0       # on 2x+ avg volume
EXHAUST_LOOKBACK = 10

# ===== Output =====
COPY_TO_CLIPBOARD = True
CSV_OUTPUT_DIR = "./output"
DB_PATH = "./data/kova_scanner.db"

# ===== Performance =====
DOWNLOAD_BATCH_SIZE = 100          # tickers per yfinance batch download
HISTORY_LOOKBACK_DAYS = 300        # ~14 months, enough for 252-day RS + buffer
UNIVERSE_REFRESH_DAYS = 7          # rebuild universe cache weekly
MAX_MARKET_CAP_THREADS = 3         # threads for parallel market cap fetching (low to avoid rate limits)
BATCH_SLEEP_SECONDS = 3            # pause between download batches (rate-limit guard)
MAX_BATCH_RETRIES = 2              # retries on rate-limit per batch
