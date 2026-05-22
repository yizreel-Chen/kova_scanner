# 📈 Kova Scanner V2.0 (Local Edition)

Kova Scanner 是一款专为美股波段交易者（Swing Traders）设计的自动化盘后选股与市场监控工具。深受 Minervini 和 CANSLIM 交易体系的启发，它能够自动评估当前大盘健康度，并在海量票池中筛选出具有强劲动量和基本技术面支撑的强势股。

V2.0 本地版完全剥离了付费的量化平台（如 QuantConnect），使用免费的 `yfinance` 接口获取高质量日线数据，零成本在本地运行。

## ✨ 核心功能 (Key Features)

* 🚦 **大盘环境红绿灯 (Market Regime Detection):** 自动计算 SPY 和 QQQ 的派发日 (Distribution Days) 及 50EMA 趋势。智能输出 **Confirmed Uptrend (健康)**、**Under Pressure (警告)** 或 **Correction (调整)** 三档大盘状态，并提供相应的仓位建议。
* 🔍 **8大硬核技术面过滤 (Technical Filtering):**
  自动过滤不符合动量交易标准的股票。包括：价格 > $10、ADR > 4%、均线多头排列 (10>20>50)、站上 50EMA、以及距离 52 周高低点的合理回撤距离等。
* 🏆 **相对强度评级 (Relative Strength Rating):**
  对通过初筛的股票池进行跨票对比，自动计算并赋予 1-99 的 RS Percentile Rating，让你直观看到谁是真正的市场领头羊。
* ⚠️ **异常异动警报 (Smart Alerts):**
  自动扫描潜在的风险信号，包括：连续跳空高开 (Gap Risk)、异常连续放量 (High Volume) 以及放量衰竭信号 (Exhaustion)，帮助规避追高风险。
* 📊 **增量跟踪与输出 (Delta Tracking & Export):**
  自动对比前一日的扫描快照，标记每只股票的状态为 **🆕 NEW (新晋)**、**✓ STILL (维持)** 或 **✗ DROPPED (掉出条件)**，并一键生成 CSV 观察清单 (Watchlist)。

## 🛠️ 技术栈 (Tech Stack)

* **Python 3.8+**
* `pandas` - 核心数据处理与指标计算
* `numpy` - 向量化计算
* `yfinance` - 免费且稳定的历史日线数据源

## 🚀 快速开始 (Quick Start)

1. 克隆仓库:
   ```bash
   git clone [https://github.com/YourUsername/Kova-Scanner.git](https://github.com/YourUsername/Kova-Scanner.git)
   cd Kova-Scanner
2. 安装依赖:
   ```bash
pip install -r requirements.txt
3. 运行扫描器 (建议美股收盘后运行):
  ```bash
python kova_scanner.py
📝 运行逻辑简介 (Workflow)
脚本启动后，首先获取 SPY 和 QQQ 的近期数据，判断大盘环境。若大盘处于 "Correction" (调整期)，扫描器将建议空仓或极低仓位，并停止推荐新票。

若大盘健康，脚本将遍历 UNIVERSE 列表中的股票，下载过去一年的日线数据。

执行技术面初筛，计算 RS Rating。

对通过过滤的标的进行量价形态警报检测。

将结果按 RS 分数和警报数量排序，打印在终端，并导出为 kova_watchlist_YYYYMMDD.csv。

💡 声明 (Disclaimer)
本项目仅供编程学习与量化研究使用，代码输出的任何结果均不构成财务或投资建议。交易有风险，入市需谨慎。
