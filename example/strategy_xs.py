"""横截面策略程序 —— 唯一可编辑文件（the single editable program）。

HL 只允许改本文件；固定评估器 backtest_xs.py 不动。score() 是纯函数：
输入每只股票截至调仓日的 OHLC，输出 {code: 分数}，引擎排序取 Top-N 等权。

当前基线：多因子 v1（ROE + EP + 12-1 动量）。
经济含义：
1. 高 ROE 代表更好的盈利质量；
2. 高 EP（低 PE）代表估值更便宜；
3. 12-1 动量代表市场已验证的相对强势。
三者做等权截面排序求和，作为一个“多因子入门基线”。
"""
from __future__ import annotations

import pandas as pd
from factor_snapshot_tech50 import FACTOR_SNAPSHOT

REBALANCE = "monthly"   # 声明式：调仓节奏由引擎固定执行（引擎读取，不进 CORE）

# >>> STRATEGY CORE >>>
LOOKBACK = 252          # 需要的已完成日线数（约12个月），引擎/聚宽据此取数
SKIP = 21               # 跳过最近约1个月（规避短期反转）


def score(history: dict) -> dict:
    """输入 {code: OHLC DataFrame(含 close 列，升序)}；输出 {code: 多因子分数}。
    只看价格、只给分数；不下单、不读盘、不联网。"""
    rows = []
    for code, df in history.items():
        snap = FACTOR_SNAPSHOT.get(code)
        if snap is None:
            continue
        closes = df["close"].reset_index(drop=True)
        if len(closes) < LOOKBACK:
            continue
        p_recent = float(closes.iloc[-SKIP])       # 约1个月前
        p_old = float(closes.iloc[-LOOKBACK])      # 约12个月前
        if p_old > 0:
            pe = float(snap.get("pe", 0.0))
            ep = (1.0 / pe) if pe > 0 else None
            rows.append(
                {
                    "code": code,
                    "roe": float(snap.get("roe", 0.0)),
                    "ep": ep,
                    "mom": p_recent / p_old - 1.0,
                }
            )
    if not rows:
        return {}

    df = pd.DataFrame(rows)
    df["roe_rank"] = df["roe"].rank(pct=True, ascending=True).fillna(0.5)
    df["ep_rank"] = df["ep"].rank(pct=True, ascending=True).fillna(0.0)
    df["mom_rank"] = df["mom"].rank(pct=True, ascending=True).fillna(0.0)
    df["score"] = df["roe_rank"] + df["ep_rank"] + df["mom_rank"]
    return dict(zip(df["code"], df["score"]))
# <<< STRATEGY CORE <<<
