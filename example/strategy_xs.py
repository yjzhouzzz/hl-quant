"""横截面策略程序 —— 唯一可编辑文件（the single editable program）。

HL 只允许改本文件；固定评估器 backtest_xs.py 不动。score() 是纯函数：
输入每只股票截至调仓日的 OHLC，输出 {code: 分数}，引擎排序取 Top-N 等权。

当前基线：12-1 横截面动量——用「约1个月前 / 约12个月前」的累计收益排序，
跳过最近约1个月以规避短期反转。经济含义：过去一年相对强势的股票倾向延续。

HL Round 2 候选：在 12-1 动量外叠加 200 日趋势过滤，只给仍站在长期均线之上的
股票打分。经济含义：避免把“过去涨过、但已进入下行趋势”的个股继续排进 Top3。
"""
from __future__ import annotations

import pandas as pd

REBALANCE = "monthly"   # 声明式：调仓节奏由引擎固定执行（引擎读取，不进 CORE）

# >>> STRATEGY CORE >>>
LOOKBACK = 252          # 需要的已完成日线数（约12个月），引擎/聚宽据此取数
SKIP = 21               # 跳过最近约1个月（规避短期反转）
TREND_WINDOW = 200      # 长期趋势过滤：只做仍在长期上升趋势中的个股


def score(history: dict) -> dict:
    """输入 {code: OHLC DataFrame(含 close 列，升序)}；输出 {code: 动量分数}。
    只看价格、只给分数；不下单、不读盘、不联网。"""
    out = {}
    for code, df in history.items():
        closes = df["close"].reset_index(drop=True)
        if len(closes) < LOOKBACK:
            continue
        ma_trend = float(closes.iloc[-TREND_WINDOW:].mean())
        price = float(closes.iloc[-1])
        if price <= ma_trend:
            continue
        p_recent = float(closes.iloc[-SKIP])       # 约1个月前
        p_old = float(closes.iloc[-LOOKBACK])      # 约12个月前
        if p_old > 0:
            out[code] = p_recent / p_old - 1.0
    return out
# <<< STRATEGY CORE <<<
