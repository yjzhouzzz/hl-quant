"""策略程序 —— 唯一可编辑文件（the single editable program）。

启发式学习 / 启发式探索（HL）**只允许修改本文件**；回测器 `backtest.py`
（固定评估器）保持不动。这样每个候选策略都在同一口径下可比，避免「改了评估器
把分数刷上去」的自欺。

原始想法（来自聚宽「双均线」demo）：
    短期均线在长期均线上方 → 满仓买入；下方 → 清仓卖出。

HL 循环要做的，就是在这里提出假设、改逻辑/调参数，再用 `backtest.py` 打分，
只有分数严格高于基线才保留。

本文件当前状态 = HL 第 2 轮接受的候选：
    在 10/20 双均线之上，叠加一道 200 日均线的「牛熊趋势过滤」。
    评估（510300，2018~2026，8 年）：score −0.024 → 0.171，总收益
    −12.25% → +16.08%，最大回撤 37.47% → 25.91%，39 笔；全部门槛通过。
    经济含义：200 日线是经典牛熊分界。价格站上 200 日线才视为多头结构，
    此时的金叉才允许做多；跌破 200 日线视为空头结构，强制空仓。这道过滤
    专门规避熊市/下行段里的逆势做多（假金叉），即 510300 在 2018、
    2022~2024 段亏损的主因。
"""

from __future__ import annotations

import pandas as pd

# 下面「STRATEGY CORE」标记之间的内容 = 可移植策略核心（唯一事实源），
# 由 scripts/export_jq.py 原样提取注入聚宽脚本。
# 约束：只依赖 pandas；不做文件 IO / 联网 / 本地 import，
# 以保证本地回测器与聚宽平台跑的是同一份信号逻辑。
# >>> STRATEGY CORE >>>
# —— 可调参数：启发式探索的搜索空间 ——
SHORT_WINDOW = 10   # 快线窗口 n1（基线 5）
LONG_WINDOW = 20    # 慢线窗口 n2（基线 10）
TREND_WINDOW = 200  # 牛熊趋势线：价格站上它才视为多头结构（经典 200 日线）

# decide() 需要的最大历史长度（已完成 bar 数）；聚宽适配层据此取数。
LOOKBACK = max(LONG_WINDOW, TREND_WINDOW)


def decide(closes: pd.Series) -> str:
    """给出当前交易日的目标信号。

    输入：截至当前交易日（含今日收盘）的收盘价序列。
    输出：``"buy"``（目标满仓）/ ``"sell"``（目标空仓）/ ``"hold"``（维持现状）。

    纯函数：不读数据、不下单、不依赖任何外部状态——只看价格、只给信号。
    回测器负责把信号翻译成成交、成本与净值。
    """
    if len(closes) < LOOKBACK:
        return "hold"

    price = closes.iloc[-1]
    ma_short = closes.iloc[-SHORT_WINDOW:].mean()
    ma_long = closes.iloc[-LONG_WINDOW:].mean()
    ma_trend = closes.iloc[-TREND_WINDOW:].mean()

    # 多头结构（价站上 200 日线）+ 金叉 → 做多
    if ma_short > ma_long and price > ma_trend:
        return "buy"
    # 死叉 或 跌破牛熊线（空头结构）→ 空仓
    if ma_short < ma_long or price < ma_trend:
        return "sell"
    return "hold"
# <<< STRATEGY CORE <<<
