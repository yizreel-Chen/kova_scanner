# Kova Scanner - 本地部署需求文档

> 这份文档是给 Claude Code 的项目需求书。  
> 目标：在本地用 Python + yfinance 实现一个每日扫描美股的 scanner，  
> 基于 Kova 投资体系（CAN SLIM + Minervini VCP 风格），  
> 输出一个 watchlist 供使用者人工看图决策。

---

## 1. 项目背景

### 1.1 用户情况

- 用户名：Yi（个人投资者）
- 操作系统：(用户在 Claude Code 中提供)
- 交易账户：Interactive Brokers (IBKR)
- 看图工具：TradingView (已付费 Essential)
- 现有工具：OpenClaw（数据抓取自动化）
- 时区：新西兰 (UTC+12/+13)，美股收盘 ≈ 用户的早上 9:00 AM

### 1.2 项目目标

每天美股收盘后，扫描全美股，输出一份符合 Kova/CAN SLIM/VCP 风格的 watchlist，用户人工看图后决定是否下单。

**Scanner 的角色：找候选人。**  
**用户的角色：判断 setup、识别 pivot point、下单。**

### 1.3 为什么选本地部署

最初考虑 QuantConnect 平台，经过约 2 天测试发现：
- Free 账号算力不够扫描全市场
- 付费版 $84/月太贵
- QC Research Notebook API 不支持动态市值筛选
- 用户已有本地工具栈（IBKR、OpenClaw），本地部署集成性更好

---

## 2. 技术栈

### 2.1 核心依赖

```
Python 3.9+
yfinance >= 0.2.40   (Yahoo Finance 数据)
pandas >= 2.0
numpy >= 1.24
requests
sqlite3              (Python 内置，存历史快照)
```

### 2.2 可选依赖

```
pyperclip            (复制 TradingView 格式到剪贴板)
tqdm                 (进度条)
tabulate             (终端表格美化)
schedule             (后续自动化用)
```

### 2.3 数据源

**主数据源**：yfinance（Yahoo Finance）
- 历史价格（日线，OHLCV）
- 基本面数据（市值、EPS、营收等）
- ETF 持仓清单（可选用于获取 universe）

**辅助数据源**：
- SEC EDGAR（如需 13F 数据，但本期不实现）
- NASDAQ/NYSE 官方 ticker 列表（用于获取全市场 universe）

---

## 3. Universe 定义

### 3.1 Universe 筛选条件

从全美股（约 8000 只）筛选出满足以下条件的票：

1. **价格** > $10
2. **日均成交额** > $5,000,000（过去 20 日）
3. **市值** > $500,000,000（5 亿美元）
4. **交易所** ∈ {NYSE, NASDAQ}（主板，排除 OTC）
5. **股票类型** = Common Stock（排除 ETF、ADR 视情况、SPAC）

### 3.2 Universe 获取方式（按优先级）

**方法 A（推荐）**：
- 从 NASDAQ 官方下载所有上市 ticker 列表：https://www.nasdaq.com/market-activity/stocks/screener
- 或用 yfinance + 公开 ticker 列表（如 `yfinance.utils` 或 GitHub 上的开源 ticker 清单）
- 对所有 ticker 跑筛选条件

**方法 B（备选）**：
- 用 ETF 持仓近似全市场：
  - IWV (Russell 3000) → 约 3000 只
  - 或 QQQ + IWM + MDY 合集 → 约 2400 只
- 取 ETF 成分股清单

**预估最终 universe 大小**：2500-3000 只

### 3.3 Universe 缓存

- 全市场 ticker 列表每周更新一次（IPO 和退市变化慢）
- 缓存到本地 SQLite，避免每次都重新拉取
- 用户可手动触发刷新

---

## 4. Scanner 核心逻辑（12 个判断）

### 4.1 大盘开关（1 个判断，3 档状态）

**判断**：根据 SPY 和 QQQ 的状态，决定 scanner 是否启动、用什么 RS 阈值。

**计算 Distribution Days (DD)**：
- 对 SPY 和 QQQ 分别计算
- 规则：
  1. 当日跌幅 ≥ 0.2%
  2. 当日成交量 > 前一日成交量
  3. 25 个交易日窗口内
  4. **5% 反弹移除规则**：如果从该 DD 之后指数最高涨幅 ≥ 5%，该 DD 不再计入

**3 档状态判断**：
```
if SPY 或 QQQ 跌破 50EMA:
    state = "CORRECTION"  → scanner 返回空，不扫描
elif max(SPY_DD, QQQ_DD) ≥ 5:
    state = "CORRECTION"  → scanner 返回空
elif max(SPY_DD, QQQ_DD) ≥ 4:
    state = "UNDER_PRESSURE"  → scanner 运行，RS 阈值提到 95
else:
    state = "CONFIRMED_UPTREND"  → scanner 正常运行，RS 阈值 90
```

### 4.2 个股硬过滤（8 个 Filter，全部通过才合格）

每只股票必须**全部通过**以下 8 个 filter：

| # | Filter | 阈值 | 来源/说明 |
|---|--------|------|----------|
| 1 | 股价 | > $10 | Kova 原文，过滤低价股 |
| 2 | ADR (20日平均日内波动率) | > 4% | `ADR% = mean((high - low) / low, 20) * 100`<br>过滤低波动大盘股 |
| 3 | RS Rating (跨股票百分位) | ≥ 90（正常）或 ≥ 95（UNDER_PRESSURE）| IBD 风格相对强度 |
| 4 | 价格位置 | > 50 EMA | 中期趋势确认 |
| 5 | 均线排列 | 10 EMA > 20 EMA | 短期趋势确认 |
| 6 | 距 52 周低点 | > +70% | 排除"刚反弹的弱势股" |
| 7 | 距 50 EMA | ≤ +15% | **过滤 climax run / 过度延伸** |
| 8 | 距 52 周高点 | ≥ -25% | **过滤"已死 leader"** |

**Filter 1 实现细节**：直接在 universe 阶段已过滤，scanner 阶段再验证一次。

**Filter 2 实现细节**：
```python
daily_range = (high - low) / low
adr_pct = daily_range.tail(20).mean() * 100
```

**Filter 3 实现细节**（关键，最复杂）：
```python
# 计算每只票的 raw RS 分数
returns_63d = (close / close.shift(63)) - 1   # 3 个月收益
returns_126d = (close / close.shift(126)) - 1 # 6 个月收益
returns_189d = (close / close.shift(189)) - 1 # 9 个月收益
returns_252d = (close / close.shift(252)) - 1 # 12 个月收益

# IBD 风格加权：最近 3 个月权重 40%，其余各 20%
rs_raw = returns_63d * 0.4 + returns_126d * 0.2 + returns_189d * 0.2 + returns_252d * 0.2

# 跨股票百分位排名（0-99）
# 注意：百分位是在通过基础过滤（价格、流动性）后的 universe 内排名
# 即使 universe 是 3000 只，排名也是相对的
```

**Filter 7 详解（重要）**：
- 这是 V1.2 新增的关键 filter
- 防止 climax run（如 MU 距 50EMA +38% 的票）
- 测试中发现 climax 末期票几乎都距 50EMA > 15%

**Filter 8 详解**：
- 这是 V1.2 新增
- 防止"曾经的 leader，现在已经从高点跌 30%+"
- Kova 原文是 15-20%，我们放宽到 25%（容纳刚进入回调的 base 股）

### 4.3 警报标记（3 个，不参与过滤，只警告）

通过 8 个 filter 之后，对每只票计算 3 个警报。**警报不剔除股票，但帮助用户排序看图优先级**。

| # | 警报名 | 触发条件 | 含义 |
|---|--------|---------|------|
| A | `GAP_RISK` | 近 20 日跳空高开 ≥ 2 次 | 情绪化交易、新闻驱动，警惕 |
| B | `HIGH_VOL` | 近 5 日 ≥ 3 天放量 (>1.5x 50日均量) | 派发/churning 嫌疑 |
| C | `EXHAUST` | 近 10 日单日 ≥ 8% 涨 且 当日量 ≥ 2x 50日均量 | 衰竭跳空，顶部信号 |

**跳空定义**：今日最低价 > 昨日最高价 × 1.005（差 0.5% 以上）

**警报分组**（决定看图优先级）：
- 0 个警报 = **⭐ 干净**（优先看图）
- 1-2 个警报 = **⚠️ 谨慎**（看图时重点排查触发的警报）
- ≥ 3 个警报 = **✗ 高风险**（建议跳过或仅观察）

---

## 5. 历史快照与增量管理

### 5.1 数据持久化

使用 SQLite 数据库（推荐）或 JSON 文件存储历史快照。

**数据库 schema**（建议）：

```sql
CREATE TABLE snapshots (
    scan_date DATE PRIMARY KEY,
    market_state TEXT,          -- CORRECTION / UNDER_PRESSURE / CONFIRMED_UPTREND
    max_dd INTEGER,
    spy_price REAL,
    qqq_price REAL,
    total_passed INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE passed_stocks (
    scan_date DATE,
    ticker TEXT,
    price REAL,
    rs_rating INTEGER,
    adr_pct REAL,
    dist_50ema REAL,
    dist_high REAL,
    alerts TEXT,                -- 逗号分隔："GAP_RISK,HIGH_VOL"
    PRIMARY KEY (scan_date, ticker),
    FOREIGN KEY (scan_date) REFERENCES snapshots(scan_date)
);

CREATE TABLE universe_cache (
    ticker TEXT PRIMARY KEY,
    market_cap REAL,
    avg_dollar_volume REAL,
    exchange TEXT,
    last_updated TIMESTAMP
);
```

### 5.2 增量管理（NEW / STILL / DROPPED）

**每次跑 scanner 时**：
1. 获取昨日（最近一次）的 passed_stocks
2. 对比今日的 passed_stocks，生成三类：
   - **🆕 NEW**：今日通过 ∧ 昨日未通过 → **重点看图**
   - **✓ STILL**：今日通过 ∧ 昨日通过 → 持续观察
   - **✗ DROPPED**：今日未通过 ∧ 昨日通过 → 检查原因（跌破 50EMA？RS 滑落？）

**首次运行**：无昨日数据，所有票标记为 NEW。

### 5.3 历史回溯能力

提供命令行参数让用户回溯查看历史 watchlist：
- `python scanner.py --date 2026-05-15`：查看历史某天的 scanner 结果
- `python scanner.py --history`：列出所有历史快照日期

---

## 6. 输出格式

### 6.1 终端输出（每次跑 scanner 的主要输出）

```
============================================================
  KOVA SCANNER  |  2026-05-22 09:30 NZT
============================================================

【1/4】大盘环境检查
  SPY: $733.73 | EMA50 $707.52 | DD=4 | 数据截至 2026-05-21
  QQQ: $701.53 | EMA50 $655.90 | DD=3 | 数据截至 2026-05-21
  
  → 大盘状态：UNDER_PRESSURE
     Distribution Days = 4（达到警戒）
     RS 阈值：95

【2/4】扫描全市场...
  Universe 加载：2,837 只票（市值 > $500M, 价格 > $10, 流动性合格）
  数据可用：2,801 只
  扫描中...████████████████ 100% (耗时 5m 23s)

【3/4】对比历史快照
  基线：2026-05-21 快照
  NEW: 2 | STILL: 5 | DROPPED: 3

【4/4】Watchlist 输出
============================================================
  通过 7 只 / 总扫描 2,801 只 | 状态: UNDER_PRESSURE
============================================================

 St  Ticker    Price    RS  ADR%  D50EMA%  DHigh%   Alerts
 🆕  XXXX     $45.20    97  6.2     8.3    -12.4    -
 🆕  YYYY     $89.50    96  5.8     11.2   -8.7     GAP_RISK
 ✓   ZZZZ     $123.40   95  4.5     5.1    -6.2     -
 ✓   AAAA     $67.80    95  7.1     12.8   -15.3    HIGH_VOL
 ...

警报详情：
  YYYY ⚠ GAP_RISK: 近 20 日跳空 3 次
  AAAA ⚠ HIGH_VOL: 近 5 日放量 4 天

看图优先级：
  ⭐ 优先看 (干净): XXXX, ZZZZ
  ⚠  谨慎看 (有警报): YYYY, AAAA
  ✗  跳过 (高风险): [无]

增量明细 (vs 2026-05-21):
  🆕 NEW (2): XXXX, YYYY
     → 今日首次进入条件，重点看图
  ✓  STILL (5): ZZZZ, AAAA, BBBB, CCCC, DDDD
  ✗  DROPPED (3): EEEE, FFFF, GGGG
     → 检查是否因走弱掉出 (跌破 50EMA / RS 滑落)

============================================================
TradingView 粘贴格式（已复制到剪贴板）：
============================================================
XXXX,YYYY,ZZZZ,AAAA,BBBB,CCCC,DDDD

⚠ UNDER_PRESSURE 状态实操建议：
  • 单票仓位降到 10-15% (不是 25%)
  • 最多新建 1-2 个仓位
  • 已有持仓上移止损 (21EMA 跟踪止损)
  • 优先看「⭐干净」组，跳过「✗高风险」组

============================================================
  完成  |  耗时 5m 41s  |  CSV 已保存：./output/kova_2026-05-22.csv
============================================================
```

### 6.2 CSV 输出

每次跑完保存一个 CSV 到 `./output/kova_YYYY-MM-DD.csv`，包含：

| 列名 | 说明 |
|------|------|
| Ticker | 股票代码 |
| Status | 🆕 NEW / ✓ STILL / ✗ DROPPED |
| Price | 当前价 |
| RS_Rating | RS 评级 (0-99) |
| ADR_pct | ADR % |
| Dist_50EMA_pct | 距 50EMA % |
| Dist_High_pct | 距 52 周高点 % |
| EMA10 | 10 日 EMA |
| EMA20 | 20 日 EMA |
| EMA50 | 50 日 EMA |
| EMA21 | 21 日 EMA（用于跟踪止损） |
| High_52w | 52 周高点 |
| Low_52w | 52 周低点 |
| Alerts | 警报列表（逗号分隔） |
| Alert_Details | 警报详细描述 |

### 6.3 TradingView 集成

跑完后自动把 ticker 列表复制到系统剪贴板（用 pyperclip）。

格式：`TICKER1,TICKER2,TICKER3,...`

用户直接 Cmd+V / Ctrl+V 粘贴到 TradingView watchlist 即可。

---

## 7. 项目结构（建议）

```
kova-scanner/
├── README.md
├── requirements.txt
├── config.py                  # 所有阈值配置
├── scanner.py                 # 主程序入口
├── modules/
│   ├── __init__.py
│   ├── universe.py            # Universe 获取和缓存
│   ├── market_regime.py       # 大盘状态判断 + DD 计算
│   ├── metrics.py             # 个股指标计算（EMA、ADR、RS Raw 等）
│   ├── filters.py             # 8 个硬过滤
│   ├── alerts.py              # 3 个警报检查
│   ├── snapshot.py            # SQLite 持久化 + diff
│   └── output.py              # 终端输出 + CSV + 剪贴板
├── data/
│   ├── kova_scanner.db        # SQLite 数据库
│   └── universe_cache.json    # Universe 缓存（备用）
└── output/
    └── kova_YYYY-MM-DD.csv    # 每日输出
```

---

## 8. 命令行接口

```bash
# 日常使用：跑今日 scanner
python scanner.py

# 指定日期回溯（用历史数据，但 yfinance 不支持任意历史日，主要用于读快照）
python scanner.py --date 2026-05-20

# 列出历史快照
python scanner.py --history

# 强制刷新 universe 缓存
python scanner.py --refresh-universe

# Verbose 模式，输出诊断信息
python scanner.py --verbose

# 只输出 TradingView 格式（用于脚本化）
python scanner.py --tv-only
```

---

## 9. 配置文件（config.py）

所有阈值集中在一个文件，方便用户调整：

```python
# config.py

# ===== Universe 筛选 =====
MIN_PRICE = 10.0
MIN_DOLLAR_VOLUME = 5_000_000     # 日均成交额 $5M
MIN_MARKET_CAP = 500_000_000      # 市值 $500M
ALLOWED_EXCHANGES = ["NYSE", "NASDAQ"]

# ===== 大盘开关 =====
MAX_DD_WARNING = 4                # ≥4 进入 UNDER_PRESSURE
MAX_DD_CORRECTION = 5             # ≥5 进入 CORRECTION
DD_REBOUND_THRESHOLD = 0.05       # 5% 反弹移除规则

# ===== 个股硬过滤 =====
MIN_ADR_PCT = 4.0
MIN_RS_RATING_DEFAULT = 90
MIN_RS_RATING_PRESSURE = 95
MIN_DIST_FROM_LOW = 0.70          # 距 52w 低点 > 70%
MAX_DIST_FROM_50EMA = 0.15        # 距 50EMA ≤ 15%
MAX_DIST_FROM_HIGH = 0.25         # 距 52w 高点 ≤ 25%

# ===== 警报阈值 =====
GAP_THRESHOLD = 0.005             # 跳空定义 0.5%
GAP_LOOKBACK_DAYS = 20
GAP_COUNT_TRIGGER = 2

HIGH_VOL_MULTIPLIER = 1.5
HIGH_VOL_LOOKBACK = 5
HIGH_VOL_TRIGGER = 3

EXHAUST_PRICE_THRESHOLD = 0.08
EXHAUST_VOL_MULTIPLIER = 2.0
EXHAUST_LOOKBACK = 10

# ===== 输出 =====
COPY_TO_CLIPBOARD = True
CSV_OUTPUT_DIR = "./output"
DB_PATH = "./data/kova_scanner.db"

# ===== 性能 =====
PARALLEL_DOWNLOAD = True          # yfinance 多线程下载
DOWNLOAD_BATCH_SIZE = 100         # 每批下载 ticker 数
HISTORY_LOOKBACK_DAYS = 300       # 拉取的历史长度（至少 252 用于 52 周高低点）
```

---

## 10. 不实现的功能（明确范围）

为了控制范围，本期**不实现**以下功能，留待后续迭代：

1. **基本面 filter（EPS、营收增长）**——yfinance 提供这些数据，但实现复杂，先用纯技术面
2. **VCP 形态算法识别**——交给用户人眼判断
3. **Pivot Point 自动识别**——交给用户人眼判断
4. **Pocket Pivot 标记**——可作为加分项，本期不实现
5. **机构持仓变化（13F 数据）**——需要 SEC EDGAR 集成，复杂度高
6. **回测引擎**——本工具不是回测平台，是生产 scanner
7. **自动下单（IBKR API 集成）**——用户明确表示手动下单
8. **邮件/Slack 通知**——本地命令行输出即可
9. **图表渲染**——TradingView 已经做得很好，不重复造轮子
10. **多账户/多用户支持**——单用户工具

---

## 11. 设计决策记录（DDR）

这些是在开发过程中做的关键决策，Claude Code 实现时请遵循：

### DDR-1：Filter 7 阈值定为 15%

- Minervini 原文用 25%，Kova 原文未明示
- 我们测试发现 15% 能有效干掉 climax 末期票（如 MU 距 50EMA +38%）
- 但可能过滤掉真正的 leader（如 INTC 在反弹后 +34%）
- 接受这个偏差，宁可漏掉一些也不要假信号

### DDR-2：Filter 8 阈值放宽到 25%（Kova 原文是 15-20%）

- 15-20% 太严，会漏掉刚突破 base 的票
- 25% 是 Minervini 实操常用阈值
- 保留"接近 leader"特征同时不过于严格

### DDR-3：RS Rating 在通过基础过滤的 universe 内排名

- 不是全市场 8000 只排名
- 而是 universe（市值 + 流动性合格）内排名
- 即使是 2500-3000 只，排名也接近真实 IBD 风格

### DDR-4：5% 反弹规则用于 DD 计算

- IBD 官方规则
- 我们之前误用了 6%，已修正
- 经过用户在 TradingView 手数对照验证

### DDR-5：CORRECTION 状态完全停 scanner

- Kova 原文："仓位降到 30% 以下"
- 实现上更严格：scanner 返回空，强制用户不开新仓
- 用户已有仓位的管理由用户自己决定

### DDR-6：警报不剔除股票

- 早期设计想用警报做硬过滤，后来改为软警告
- 因为有些警报（如 GAP_RISK）在真正强势启动时也会出现
- 让用户看图时综合判断

### DDR-7：测试集偏 mid-cap

- 之前用过 50 只大盘股测试集，发现 ADR<4% 过滤掉 NVDA、AVGO 等
- Kova 系统本身适配 mid-cap 高波动 leader
- 全市场扫描时这个偏差自然消失

---

## 12. 测试与验证

### 12.1 单元测试（必须）

```python
# tests/test_market_regime.py
def test_dd_calculation():
    # 已知数据：SPY 2026-05-22 视角应有 4 个有效 DD
    # 测试 5% 反弹规则
    pass

def test_correction_state():
    # SPY 跌破 50EMA → CORRECTION
    pass

# tests/test_filters.py
def test_climax_filter():
    # 距 50EMA > 15% 应被剔除
    pass

# tests/test_alerts.py
def test_gap_risk():
    # 构造跳空数据，验证检测
    pass
```

### 12.2 集成测试（必须）

跑一次完整 scanner（universe 较小，如 50 只），验证：
1. Universe 加载正常
2. 大盘状态判断正确
3. 8 个 filter 都生效
4. 警报标记正确
5. NEW/STILL/DROPPED diff 工作（需要两次跑）
6. CSV 正确生成
7. 剪贴板复制成功

### 12.3 验证 SPY DD 计算（关键）

**已知数据点**（用户在 TradingView 手动核对过）：

```
SPY 在 2026-05-22 视角下，过去 25 个交易日内的有效 Distribution Days：
- 2026-05-04（跌 -0.37%）
- 2026-05-07（跌 -0.31%）
- 2026-05-15（跌 -1.20%）
- 2026-05-19（跌 -0.67%）

总计 4 个有效 DD（5% 反弹规则移除了 3 个早期候选）
```

实现完成后，跑一次验证 SPY DD 是否得到这 4 个日期。

---

## 13. 性能要求

- **首次跑**（universe 缓存为空）：< 30 分钟
- **日常跑**（universe 已缓存）：< 10 分钟
- **内存占用**：< 4GB（普通笔记本能扛）
- **数据下载**：并发下载（yfinance 支持 threads 参数）

---

## 14. 错误处理

### 14.1 网络问题
- yfinance 偶尔抽风（Yahoo API 限速）
- 实现重试机制（3 次重试，指数退避）
- 单只票数据下载失败不影响整体，记录到日志

### 14.2 数据缺失
- 某只票数据不足 252 天 → 跳过该票
- 某只票 NaN 值 → 跳过该票
- 全部失败 → 报错，停止运行

### 14.3 用户使用错误
- 数据库文件损坏 → 提示用户重置
- 配置参数不合理（如 RS 阈值 > 100）→ 报错

---

## 15. Scanner 的已知盲点与适用边界

> ⚠️ **重要**：这是 Kova 系统本身的设计取舍，不是实现 bug。
> Claude Code 在实现时不要试图"修复"这些问题——这是系统的固有特征。
> 用户已经知情并接受这些权衡。

### 15.1 Scanner 在不同市场环境下的真实表现

| 市场状态       | 时间占比 | Scanner 有效性 | 说明                                  |
| -------------- | -------- | -------------- | ------------------------------------- |
| 标准牛市中段   | 30-40%   | ⭐⭐⭐⭐⭐ 核心适用 | 系统设计的目标环境                    |
| 牛市末期       | 10-15%   | ⭐⭐⭐ 部分适用   | 通过的票减少是真实信号                |
| 牛转熊         | 5-10%    | ⭐⭐⭐⭐ 保护适用  | 大盘开关迅速进入 CORRECTION           |
| 熊市           | 15-25%   | ⭐⭐ 保护适用    | 持续 CORRECTION，避免接刀             |
| **熊转牛初期** | 5-10%    | ⭐ **盲点**     | **Scanner 几乎沉默，错过早期 leader** |
| 牛市初期       | 10-15%   | ⭐⭐ 部分适用    | RS Rating 滞后，渐渐恢复输出          |

**合计约 15-25% 的时间 scanner 不可靠。**

### 15.2 最大盲点：熊转牛初期

**问题本质**：RS Rating 公式是基于过去 12 个月相对表现的加权（最近 3 个月 40%，其余各 20%）。

在熊转牛初期（如 2023 年 1-3 月）：
- 真正的下一轮 leader（如 NVDA、META）刚从底部反弹
- 过去 12 个月仍然是负收益
- 过去 3 个月可能 +30%，但加权后 RS Rating 仅 50-60
- **远低于 90 阈值**

**典型例子**（2023 年 2 月 NVDA）：

---

## 16. 未来扩展方向

按优先级排列，但**本期不实现**：

### 16.1 Bear-to-Bull Transition Mode（牛熊转换适配，针对 15.2 盲点）

**目标**：解决熊转牛初期 RS Rating 滞后的盲点。

**触发条件**：
- 大盘从 CORRECTION 状态转回 CONFIRMED_UPTREND 后的前 30 个交易日
- 系统自动识别"转换模式"

**Mode 下的临时调整**：
- RS 阈值从 90 临时降低到 **70**
- 临时**取消 Filter 8**（距 52 周高点 ≤ 25%）
- 保留所有其他 filter（包括 Filter 7 距 50EMA ≤ 15%，仍防止追高）

**附加 filter**（Stage Analysis）：
- 50 EMA 斜率开始上升（不再下降）
- 200 EMA 走平或上升
- 股价突破长期下降趋势线
- 出现放量上涨

**实现优先级**：中（部署后 6-12 个月再考虑）

**参考**：Minervini 在《Trade Like a Stock Market Wizard》中的 Stage Analysis 章节。

### 16.2 其他扩展（按优先级）

1. **Pocket Pivot 标记**（加分项，不参与过滤）
2. **基本面 filter**（EPS、营收，yfinance 已提供）
3. **Notion API 集成**（如果用户重新使用 Notion）
4. **OpenClaw 集成**（抓取另类数据如专利、供应链）
5. **定时自动跑**（cron / Task Scheduler）
6. **Telegram/微信推送**
7. **手动回测分析**（基于历史快照统计 scanner 命中率）

---

---

## 17. 用户预期使用流程

```
每个工作日，美股收盘后（NZ 时间约 9:00 AM）：
  ↓
1. 用户打开终端，cd 到项目目录
2. 跑 python scanner.py
3. 等待 5-10 分钟扫描完成
4. 看终端输出：
   - 大盘状态如何？是否 CORRECTION？
   - 通过几只票？
   - NEW 票有几只？重点关注哪些？
   - 哪些票是「干净」组？
5. ticker 列表已自动复制到剪贴板
6. 切到 TradingView，覆盖/更新 watchlist
7. 翻图 5-15 分钟，对照看图 checklist：
   - 是不是 climax run？
   - 能找到清晰 base 吗？
   - Base 在什么阶段？
   - Pivot point 在哪？
   - 量价配合健康吗？
8. 找到 setup 票 → TradingView 设 price alert
9. Alert 触发 → 切 IBKR 下单 + 同步挂止损单
10. 收工
```

**总投入：10-30 分钟/天**

---

## 18. 联系与上下文

如果 Claude Code 在实现过程中有疑问：

- 这份文档是用户和 Claude（不同实例）共同讨论 2 天后的结果
- 用户已经在 QuantConnect 上验证过 scanner 的核心逻辑
- 用户对 Kova 投资体系熟悉，对技术指标熟悉
- 用户软件背景（前 ADI、startup 经验），可以理解技术决策
- **遇到不确定的设计决策，向用户确认，不要自作主张**

---

**文档版本**：v1.0  
**日期**：2026-05-22  
**作者**：Claude（基于与用户 Yi 的两天讨论）  
**目标读者**：Claude Code（用于本地部署实现）
