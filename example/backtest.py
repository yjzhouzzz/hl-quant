"""固定评估器（fixed evaluator）—— 跑一次回测，得到一个分数。

职责：
1. 从腾讯财经公开接口拉取（并缓存）前复权日线，免认证、可取多年历史；
2. 逐日调用 ``strategy.decide(...)``，模拟满仓/空仓切换、扣减聚宽口径的手续费；
3. 计算 Sharpe / Sortino / 年化 / 最大回撤等指标，汇成**单一分数**。

评分口径：

    score = Sortino

⚠️ 本文件是固定评估口径，HL 循环**不允许修改**它。要提分只能改 strategy.py。
若评估器本身坏了（如接口不可用、数据缺口），停下来报告，不要靠改基准绕过。

运行（无需任何凭证）：
    python backtest.py                  # 全窗口打分（HL 研究用）
    python backtest.py --holdout        # 样本内 vs 尾部 holdout（样本外确认）
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

import strategy

# ============================================================
# 固定研究口径（FIXED —— 不要为了提分而修改）
# ============================================================
SECURITY = "510300.XSHG"          # 标的：沪深300ETF（可交易，对齐实盘）
START_DATE = "2018-01-01"         # 多年回测区间（腾讯前复权日线，覆盖多轮牛熊）
END_DATE = "2026-02-28"
INITIAL_CASH = 100_000.0          # 初始资金：个人实盘 10 万
TRADING_DAYS_PER_YEAR = 252

# 聚宽口径手续费：买卖佣金万分之三，卖出印花税千分之一，单边佣金最低 5 元
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
MIN_COMMISSION = 5.0

# 尾部 holdout 比例：最近约 20% 留作 run-once 样本外确认。
# 多年区间下 holdout 覆盖 ~1.5 年，交易样本较充足，可作方向性确认；
# 实盘级终验仍在聚宽平台完成（见 jq_strategy_export.py）。
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
# 数据：腾讯财经前复权日线 + 本地缓存
# ------------------------------------------------------------
# 腾讯行情接口约定：交易所前缀（6/5 开头为上交所 sh，其余深交所 sz）。
_TENCENT_PAGE = 800          # 单次可靠返回的最大日线条数（经验值，>800 接口异常）
_TENCENT_MAX_PAGES = 30      # 分页安全上限，防止意外死循环


def _tencent_symbol(security: str) -> str:
    code = security.split(".")[0]          # "510300.XSHG" -> "510300"
    market = "sh" if code.startswith(("6", "5")) else "sz"
    return f"{market}{code}"


def _fetch_page(symbol: str, end: str, count: int) -> list:
    """取截至 ``end`` 往前 ``count`` 根前复权日线，返回原始 kline 行列表。"""
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?_var=k&param={symbol},day,,{end},{count},qfq"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    payload = json.loads(raw[raw.index("=") + 1:])
    sd = payload.get("data", {}).get(symbol, {})
    return (sd.get("qfqday") or sd.get("day") or []) if isinstance(sd, dict) else []


def load_prices() -> pd.DataFrame:
    """拉取前复权日线（close），优先读本地缓存以避免重复联网。

    数据源：腾讯财经公开接口（免认证、可取多年历史）。该接口单次最多稳定返回
    约 800 条，故用「end 游标往前翻页」拼出多年区间。非官方接口，可能限频或
    变更格式；若拉取失败或数据缺口，停下报告，不要靠改口径绕过。
    """
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{SECURITY}_{START_DATE}_{END_DATE}.pkl"
    if cache_file.exists():
        return pd.read_pickle(cache_file)

    symbol = _tencent_symbol(SECURITY)
    rows: dict[str, float] = {}            # date_str -> close，按日期去重
    end = END_DATE
    for _ in range(_TENCENT_MAX_PAGES):
        klines = _fetch_page(symbol, end, _TENCENT_PAGE)
        if not klines:
            break
        for r in klines:
            rows[r[0]] = float(r[2])       # r[0]=date, r[2]=close
        earliest = klines[0][0]
        if earliest <= START_DATE:
            break
        end = earliest                     # 下一页以本页最早日为界，重叠一日靠去重消化
    if not rows:
        raise SystemExit(f"腾讯接口未取到 {symbol} 的数据（区间 {START_DATE}~{END_DATE}）。")

    df = pd.DataFrame(
        [{"date": pd.to_datetime(d), "close": c} for d, c in rows.items()]
    ).sort_values("date").reset_index(drop=True)
    df = df[(df["date"] >= START_DATE) & (df["date"] <= END_DATE)].reset_index(drop=True)
    if df.empty:
        raise SystemExit(f"腾讯接口返回 {symbol} 数据，但落在 {START_DATE}~{END_DATE} 内为空。")
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
        print("注：holdout 仅作 run-once 方向性确认，不作硬门槛；")
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
