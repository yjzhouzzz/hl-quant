"""固定评估器（fixed evaluator）—— 跑一次回测，得到一个分数。

职责：
1. 用环境变量里的聚宽账号认证，拉取（并缓存）日线数据；
2. 逐日调用 ``strategy.decide(...)``，模拟满仓/空仓切换、扣减聚宽口径的手续费；
3. 计算 Sharpe / Sortino / 年化 / 最大回撤等指标，汇成**单一分数**。

评分口径：

    score = Sortino

⚠️ 本文件是固定评估口径，HL 循环**不允许修改**它。要提分只能改 strategy.py。
若评估器本身坏了（如认证失败、数据缺口），停下来报告，不要靠改基准绕过。

运行：
    export JOINQUANT_ACCOUNT=...        # 聚宽账号
    export JOINQUANT_PASSWORD=...       # 聚宽密码
    python backtest.py                  # 全窗口打分（HL 研究用）
    python backtest.py --holdout        # 样本内 vs 尾部 holdout（终验前的样本外确认）
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

import strategy

# ============================================================
# 固定研究口径（FIXED —— 不要为了提分而修改）
# ============================================================
SECURITY = "510300.XSHG"          # 标的：沪深300ETF（可交易，对齐实盘）
START_DATE = "2025-03-01"         # 回测区间（落在账号数据权限窗口内）
END_DATE = "2026-02-28"
INITIAL_CASH = 100_000.0          # 初始资金：个人实盘 10 万
TRADING_DAYS_PER_YEAR = 252

# 聚宽口径手续费：买卖佣金万分之三，卖出印花税千分之一，单边佣金最低 5 元
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
MIN_COMMISSION = 5.0

# 一年数据下的尾部 holdout 比例：最近约 20%（~2.4 个月）留作样本外确认。
# 说明：低频策略 + 一年数据，holdout 交易稀薄，仅作「run-once」方向性确认，
# 不作硬性否决；真正的实盘级终验在聚宽平台完成（见 jq_strategy_export.py）。
HOLDOUT_FRAC = 0.20

CACHE_DIR = Path(__file__).parent / ".cache"


@dataclass
class Metrics:
    score: float
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    trade_count: int


# ------------------------------------------------------------
# 数据：认证 + 拉取 + 本地缓存
# ------------------------------------------------------------
def load_prices() -> pd.DataFrame:
    """拉取日线 OHLCV，优先读本地缓存以避免重复联网。"""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{SECURITY}_{START_DATE}_{END_DATE}.pkl"
    if cache_file.exists():
        return pd.read_pickle(cache_file)

    account = os.environ.get("JOINQUANT_ACCOUNT")
    password = os.environ.get("JOINQUANT_PASSWORD")
    if not account or not password:
        raise SystemExit(
            "缺少聚宽凭证。请先设置环境变量：\n"
            "  export JOINQUANT_ACCOUNT=<你的聚宽账号>\n"
            "  export JOINQUANT_PASSWORD=<你的聚宽密码>"
        )

    import jqdatasdk as jq

    jq.auth(account, password)
    df = jq.get_price(
        SECURITY, start_date=START_DATE, end_date=END_DATE, frequency="daily"
    )
    if df is None or df.empty:
        raise SystemExit(f"未取到 {SECURITY} 在 {START_DATE}~{END_DATE} 的数据。")
    df.to_pickle(cache_file)
    return df


# ------------------------------------------------------------
# 回测：逐日模拟满仓 / 空仓切换
# ------------------------------------------------------------
def run_backtest(prices: pd.DataFrame) -> Metrics:
    closes = prices["close"].reset_index(drop=True)

    cash = INITIAL_CASH
    shares = 0.0
    entry_cost = 0.0          # 当前持仓的买入总成本（含手续费），用于胜率统计
    equity_curve: list[float] = []
    closed_trades: list[float] = []   # 每笔已平仓交易的盈亏

    for i in range(len(closes)):
        price = float(closes.iloc[i])
        # 只用「截至今日收盘」的信息决策，不使用未来数据
        signal = strategy.decide(closes.iloc[: i + 1])

        if signal == "buy" and shares == 0.0:
            commission = max(cash * COMMISSION_RATE, MIN_COMMISSION)
            invest = cash - commission
            shares = invest / price
            entry_cost = cash            # 全部现金投入
            cash = 0.0
        elif signal == "sell" and shares > 0.0:
            proceeds = shares * price
            commission = max(proceeds * COMMISSION_RATE, MIN_COMMISSION)
            tax = proceeds * STAMP_TAX_RATE
            cash = proceeds - commission - tax
            closed_trades.append(cash - entry_cost)
            shares = 0.0
            entry_cost = 0.0

        equity_curve.append(cash + shares * price)

    return _compute_metrics(equity_curve, closed_trades)


def _compute_metrics(
    equity_curve: list[float],
    closed_trades: list[float],
    starting_equity: float = INITIAL_CASH,
) -> Metrics:
    equity = pd.Series(equity_curve)
    n_days = len(equity)

    total_return = equity.iloc[-1] / starting_equity - 1.0
    annualized_return = (
        (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / n_days) - 1.0
        if n_days > 0
        else 0.0
    )

    daily_returns = equity.pct_change().dropna()
    std = float(daily_returns.std())
    sharpe = (
        float(daily_returns.mean()) / std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if std > 0
        else 0.0
    )
    downside_returns = daily_returns[daily_returns < 0]
    downside_std = float(downside_returns.std())
    sortino = (
        float(daily_returns.mean()) / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR)
        if downside_std > 0
        else 0.0
    )

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown = float(-drawdown.min())   # 取正值

    wins = sum(1 for pnl in closed_trades if pnl > 0)
    win_rate = wins / len(closed_trades) if closed_trades else 0.0

    score = sortino

    return Metrics(
        score=score,
        total_return=total_return,
        annualized_return=annualized_return,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        trade_count=len(closed_trades),
    )


# ------------------------------------------------------------
# 分段回测（holdout 验证用）：在任意连续切片上跑，warmup 段只喂历史不计分
# ------------------------------------------------------------
def _simulate(closes: pd.Series, warmup: int = 0) -> tuple[list[float], list[float], float]:
    """在一段收盘价上模拟满仓/空仓，返回（计分段净值、计分段平仓盈亏、计分段起始净值）。

    warmup：前 ``warmup`` 根 bar 仅用于喂给 ``decide`` 计算均线、让持仓「热身」，
    其净值与交易不计入结果，从而保证 holdout 段的信号有足够历史、且样本外统计干净。
    """
    closes = closes.reset_index(drop=True)
    cash = INITIAL_CASH
    shares = 0.0
    entry_cost = 0.0
    equity_curve: list[float] = []
    closed_trades: list[float] = []
    starting_equity = INITIAL_CASH

    for i in range(len(closes)):
        price = float(closes.iloc[i])
        signal = strategy.decide(closes.iloc[: i + 1])

        if signal == "buy" and shares == 0.0:
            commission = max(cash * COMMISSION_RATE, MIN_COMMISSION)
            invest = cash - commission
            shares = invest / price
            entry_cost = cash
            cash = 0.0
        elif signal == "sell" and shares > 0.0:
            proceeds = shares * price
            commission = max(proceeds * COMMISSION_RATE, MIN_COMMISSION)
            tax = proceeds * STAMP_TAX_RATE
            cash = proceeds - commission - tax
            if i >= warmup:                       # 只统计计分段平仓的交易
                closed_trades.append(cash - entry_cost)
            shares = 0.0
            entry_cost = 0.0

        if i == warmup - 1:                       # 计分段起点净值（热身结束时）
            starting_equity = cash + shares * price
        if i >= warmup:
            equity_curve.append(cash + shares * price)

    return equity_curve, closed_trades, starting_equity


def evaluate_holdout(prices: pd.DataFrame) -> tuple[Metrics, Metrics]:
    """把区间切成「样本内（前段）」与「尾部 holdout（后段）」，各自打分。

    holdout 段带 ``LOOKBACK`` 根只读热身 bar，保证均线信号有完整历史。
    返回 (样本内 Metrics, holdout Metrics)。
    """
    closes = prices["close"].reset_index(drop=True)
    n = len(closes)
    split = int(n * (1.0 - HOLDOUT_FRAC))

    in_curve, in_trades, in_start = _simulate(closes.iloc[:split], warmup=0)
    in_metrics = _compute_metrics(in_curve, in_trades, in_start)

    warmup = min(strategy.LOOKBACK, split)
    ho_curve, ho_trades, ho_start = _simulate(
        closes.iloc[split - warmup:], warmup=warmup
    )
    ho_metrics = _compute_metrics(ho_curve, ho_trades, ho_start)
    return in_metrics, ho_metrics


def _print_metrics(title: str, m: Metrics) -> None:
    print(title)
    print("-" * 48)
    print(f"  总收益     : {m.total_return:+.2%}")
    print(f"  年化收益   : {m.annualized_return:+.2%}")
    print(f"  Sharpe     : {m.sharpe_ratio:.3f}")
    print(f"  Sortino    : {m.sortino_ratio:.3f}")
    print(f"  最大回撤   : {m.max_drawdown:.2%}")
    print(f"  胜率       : {m.win_rate:.2%}  ({m.trade_count} 笔)")
    print("-" * 48)
    print(f"  >>> SCORE  : {m.score:.4f}")


def main() -> None:
    holdout_mode = "--holdout" in sys.argv[1:]
    prices = load_prices()

    if holdout_mode:
        n = len(prices)
        split = int(n * (1.0 - HOLDOUT_FRAC))
        in_m, ho_m = evaluate_holdout(prices)
        print(f"标的 {SECURITY}  区间 {START_DATE} ~ {END_DATE}  "
              f"({n} 个交易日)")
        print(f"参数  SHORT_WINDOW={strategy.SHORT_WINDOW}  "
              f"LONG_WINDOW={strategy.LONG_WINDOW}")
        print("=" * 48)
        _print_metrics(f"样本内（前 {split} 日）", in_m)
        print()
        _print_metrics(f"尾部 holdout（后 {n - split} 日，run-once）", ho_m)
        print("=" * 48)
        print("注：一年数据下 holdout 交易稀薄，仅作方向性确认，非硬门槛；")
        print("    实盘级终验请用 jq_strategy_export.py 在聚宽平台跑全窗口。")
        return

    m = run_backtest(prices)
    print(f"标的 {SECURITY}  区间 {START_DATE} ~ {END_DATE}  "
          f"({len(prices)} 个交易日)")
    print(f"参数  SHORT_WINDOW={strategy.SHORT_WINDOW}  "
          f"LONG_WINDOW={strategy.LONG_WINDOW}")
    print("-" * 48)
    print(f"  总收益     : {m.total_return:+.2%}")
    print(f"  年化收益   : {m.annualized_return:+.2%}")
    print(f"  Sharpe     : {m.sharpe_ratio:.3f}")
    print(f"  Sortino    : {m.sortino_ratio:.3f}")
    print(f"  最大回撤   : {m.max_drawdown:.2%}")
    print(f"  胜率       : {m.win_rate:.2%}  ({m.trade_count} 笔)")
    print("-" * 48)
    print(f"  >>> SCORE  : {m.score:.4f}")

    results_file = CACHE_DIR / "results.json"
    CACHE_DIR.mkdir(exist_ok=True)
    import json
    results_file.write_text(json.dumps(asdict(m), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
