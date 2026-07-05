"""固定评估器 v2（横截面选股）—— 跑一次回测，得到一个 IR 超额分数。

职责：拉沪深300固定快照的腾讯前复权日线面板 + 基准 sh000300；按月度调仓、
等权 Top-N、次日开盘成交（滑点/整手/成本），算相对基准的信息比率(IR)。

⚠️ 固定评估口径，HL 循环不允许修改；提分只改 strategy_xs.py。设计见
docs/design/cross-sectional-pipeline.md。运行：
    python backtest_xs.py            # 全窗口打分（score = IR）
    python backtest_xs.py --holdout  # 样本内 vs 尾部 holdout
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import strategy_xs
from backtest import _fetch_page, _tencent_symbol  # 复用腾讯拉取（只读，不改单标的版）
from universe_csi300 import CSI300, SNAPSHOT_DATE

# ============================================================
# 固定研究口径（FIXED —— 不要为了提分而修改）
# ============================================================
BENCHMARK = "000300.XSHG"          # 基准：沪深300 指数（腾讯 sh000300）
START_DATE = "2018-01-01"
END_DATE = "2026-02-28"
INITIAL_CASH = 100_000.0
TRADING_DAYS_PER_YEAR = 252

TOP_N = 3                          # 等权持有前 N 名
HOLDOUT_FRAC = 0.20               # 尾部 holdout 比例

COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
MIN_COMMISSION = 5.0
LOT_SIZE = 100
SLIPPAGE = 0.00246

CACHE_DIR = Path(__file__).parent / ".cache"


@dataclass
class XSMetrics:
    score: float          # = 信息比率 IR
    ann_excess: float     # 年化超额
    excess_maxdd: float   # 超额（组合−基准 累计）最大回撤
    monthly_win: float    # 月度胜率（跑赢基准的月份占比）
    t_stat: float         # 超额 t 统计量 = IR × √年数
    turnover: float       # 年化换手（单边）
    n_holdings: int       # 有效持仓次数（调仓数 × 每次持仓数）
    top_contrib: float    # 最大「单只×单期」对累计超额贡献占比


def rebalance_dates(trading_days: list[pd.Timestamp]) -> list[pd.Timestamp]:
    """给定升序交易日列表，返回每个自然月的首个交易日（调仓日）。"""
    seen: set[tuple[int, int]] = set()
    out: list[pd.Timestamp] = []
    for d in trading_days:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


_TENCENT_PAGE = 800
_TENCENT_MAX_PAGES = 30


def _rows_to_ohlc(rows: list, start: str, end: str) -> pd.DataFrame:
    """腾讯 kline 行 [date,open,close,high,low,vol] → 去重排序后的 open/close 表。"""
    acc: dict[str, tuple] = {}
    for r in rows:
        acc[r[0]] = (float(r[1]), float(r[2]))     # date -> (open, close)
    if not acc:
        return pd.DataFrame(columns=["date", "open", "close"])
    df = pd.DataFrame(
        [{"date": pd.to_datetime(d), "open": o, "close": c} for d, (o, c) in acc.items()]
    ).sort_values("date").reset_index(drop=True)
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def _fetch_ohlc(symbol: str) -> pd.DataFrame:
    """分页拉取单个腾讯符号的多年前复权日线，返回 open/close 表。"""
    rows: list = []
    end = END_DATE
    for _ in range(_TENCENT_MAX_PAGES):
        page = _fetch_page(symbol, end, _TENCENT_PAGE)
        if not page:
            break
        rows = page + rows
        earliest = page[0][0]
        if earliest <= START_DATE:
            break
        end = earliest
    return _rows_to_ohlc(rows, START_DATE, END_DATE)


def load_panel() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """拉取股票池面板 + 基准，优先读缓存。

    返回 (panel, bench)：panel = {code: OHLC表(date/open/close)}；bench = 基准 OHLC 表。
    缺口股票（停牌久/次新不足）保留其可得区间，撮合时按日期对齐处理。
    """
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"panel_csi300_{SNAPSHOT_DATE}_{START_DATE}_{END_DATE}.pkl"
    if cache.exists():
        obj = pd.read_pickle(cache)
        return obj["panel"], obj["bench"]

    bench = _fetch_ohlc("sh000300")  # 指数 000300 非 6/5 开头，不走 _tencent_symbol
    if bench.empty:
        raise SystemExit("基准 sh000300 未取到数据，停止（数据源阻塞）。")

    panel: dict[str, pd.DataFrame] = {}
    for code in CSI300:
        df = _fetch_ohlc(_tencent_symbol(code))
        if not df.empty:
            panel[code] = df
    if len(panel) < 0.8 * len(CSI300):
        raise SystemExit(
            f"仅取到 {len(panel)}/{len(CSI300)} 只，疑似接口受限，停止（勿用残缺池）。"
        )
    pd.to_pickle({"panel": panel, "bench": bench}, cache)
    return panel, bench


def _order_value(cash: float, positions: dict, code: str,
                 target_val: float, ref_open: float) -> tuple[float, float]:
    """把 code 的持仓按目标市值 target_val 调整（次日开盘价 ref_open，含滑点/整手/成本）。
    返回 (更新后现金, 本次成交名义额)。target_val=0 即清仓。"""
    cur = positions.get(code, 0.0)
    cur_val = cur * ref_open
    traded = 0.0
    if target_val < cur_val:                       # 卖到目标
        price = ref_open * (1 - SLIPPAGE / 2)
        lots = min(int((cur_val - target_val) / (ref_open * LOT_SIZE)), int(cur / LOT_SIZE))
        if lots > 0:
            qty = lots * LOT_SIZE
            proceeds = qty * price
            comm = max(proceeds * COMMISSION_RATE, MIN_COMMISSION)
            tax = proceeds * STAMP_TAX_RATE
            cash += proceeds - comm - tax
            traded = qty * ref_open
            positions[code] = cur - qty
            if positions[code] < LOT_SIZE / 2:
                positions.pop(code, None)
    elif target_val > cur_val:                     # 买到目标
        price = ref_open * (1 + SLIPPAGE / 2)
        budget = min(target_val - cur_val, cash)
        lots = int(budget / (price * LOT_SIZE * (1 + COMMISSION_RATE)))
        while lots > 0:
            qty = lots * LOT_SIZE
            cost = qty * price
            comm = max(cost * COMMISSION_RATE, MIN_COMMISSION)
            if cost + comm <= cash:
                cash -= cost + comm
                positions[code] = cur + qty
                traded = qty * ref_open
                break
            lots -= 1
    return cash, traded


def _rebalance(cash: float, positions: dict, targets: list[str],
               open_prices: dict) -> tuple[float, dict, float]:
    """调仓到等权目标名单：先清出局者，再按等权目标市值减超配、加欠配。
    停牌（open 缺失）者无法成交则保留原状。返回 (现金, 持仓, 成交名义额)。"""
    traded = 0.0
    for code in list(positions):                   # a) 清出不在目标里的
        if code not in targets and code in open_prices:
            cash, tn = _order_value(cash, positions, code, 0.0, open_prices[code])
            traded += tn
    tradable = [c for c in targets if c in open_prices]
    if not tradable:
        return cash, positions, traded
    total = cash + sum(positions.get(c, 0.0) * open_prices[c] for c in tradable)
    tv = total / len(tradable)
    for code in tradable:                          # b) 先减超配（释放现金）
        if positions.get(code, 0.0) * open_prices[code] > tv:
            cash, tn = _order_value(cash, positions, code, tv, open_prices[code])
            traded += tn
    for code in tradable:                          # c) 再加欠配
        if positions.get(code, 0.0) * open_prices[code] < tv:
            cash, tn = _order_value(cash, positions, code, tv, open_prices[code])
            traded += tn
    return cash, positions, traded


def _simulate_xs(panel: dict, bench: pd.DataFrame, rebalance_set: set,
                 warmup_days: int = 0) -> tuple:
    """逐日模拟月度等权 Top-N：调仓日收盘选股 → 次日开盘成交 → 每日收盘估值。

    返回 (组合净值 Series, 基准净值 Series, 持仓日志 list[(date,[codes])],
          成交名义额合计, 计分段起始组合净值, 计分段起始基准值)。
    warmup_days 之前的净值/交易/日志不计入（供 holdout 段热身）。
    """
    cal = list(bench["date"])
    bclose = dict(zip(bench["date"], bench["close"]))
    popen = {c: dict(zip(df["date"], df["open"])) for c, df in panel.items()}
    pclose = {c: dict(zip(df["date"], df["close"])) for c, df in panel.items()}

    cash = INITIAL_CASH
    positions: dict[str, float] = {}
    last_close: dict[str, float] = {}
    pending: list[str] | None = None
    port_curve: list[float] = []
    bench_curve: list[float] = []
    holdings_log: list[tuple] = []
    turnover_total = 0.0
    start_port = INITIAL_CASH
    start_bench = bclose[cal[0]]

    for i, d in enumerate(cal):
        if pending is not None:                    # 1) 执行昨日目标：今开成交
            names = set(list(positions) + pending)
            op = {c: popen[c][d] for c in names if d in popen.get(c, {})}
            cash, positions, tv = _rebalance(cash, positions, pending, op)
            if i >= warmup_days:
                turnover_total += tv
            pending = None
        for c in list(positions):                  # 2) 更新估值兜底（停牌沿用上一收盘）
            if d in pclose.get(c, {}):
                last_close[c] = pclose[c][d]
        if d in rebalance_set:                      # 3) 调仓日收盘选下一批
            hist = {c: df[df["date"] <= d] for c, df in panel.items()}
            scores = strategy_xs.score(hist)
            nxt = cal[i + 1] if i + 1 < len(cal) else None
            elig = [c for c in scores if nxt is not None and nxt in popen.get(c, {})]
            elig.sort(key=lambda c: scores[c], reverse=True)
            pending = elig[:TOP_N]
            if i >= warmup_days and pending:
                holdings_log.append((d, list(pending)))
        pv = cash + sum(sh * last_close.get(c, 0.0) for c, sh in positions.items())
        if i == warmup_days:
            start_port = pv
            start_bench = bclose[d]
        if i >= warmup_days:
            port_curve.append(pv)
            bench_curve.append(bclose[d])
    idx_scoring = cal[warmup_days:]                # 计分段日期索引（供月度重采样）
    return (pd.Series(port_curve, index=idx_scoring),
            pd.Series(bench_curve, index=idx_scoring), holdings_log,
            turnover_total, start_port, start_bench)


def _close_asof(df: pd.DataFrame, d) -> float | None:
    """取 df 中 date ≤ d 的最后一个 close；无则 None。"""
    sub = df[df["date"] <= d]
    return float(sub["close"].iloc[-1]) if len(sub) else None


def _compute_xs_metrics(port: pd.Series, bench: pd.Series, holdings_log: list,
                        turnover_total: float, start_port: float,
                        start_bench: float, panel: dict) -> XSMetrics:
    port_ret = port.pct_change().dropna()
    bench_ret = bench.pct_change().dropna()
    excess = (port_ret - bench_ret).dropna()

    n = len(port)
    years = n / TRADING_DAYS_PER_YEAR if n else 0.0
    ex_std = float(excess.std())
    ir = (float(excess.mean()) / ex_std * math.sqrt(TRADING_DAYS_PER_YEAR)) if ex_std > 0 else 0.0
    t_stat = ir * math.sqrt(years) if years > 0 else 0.0
    ann_excess = float(excess.mean()) * TRADING_DAYS_PER_YEAR

    # 相对净值线（组合/基准，各自归一）→ 超额最大回撤
    rel = (port / start_port) / (bench / start_bench)
    run_max = rel.cummax()
    excess_maxdd = float(-((rel - run_max) / run_max).min()) if len(rel) else 0.0

    # 月度胜率
    pm = port.resample("ME").last().pct_change().dropna()
    bm = bench.resample("ME").last().pct_change().dropna()
    monthly_win = float((pm > bm).mean()) if len(pm) else 0.0

    # 年化换手（单边，粗略）
    avg_val = float(port.mean()) if len(port) else INITIAL_CASH
    turnover = (turnover_total / avg_val / years) if (avg_val > 0 and years > 0) else 0.0

    # 有效持仓次数 + 单只×单期超额贡献（等权、close-to-close 近似）
    n_holdings = sum(len(codes) for _, codes in holdings_log)
    contribs: list[float] = []
    for k, (d, codes) in enumerate(holdings_log):
        end = holdings_log[k + 1][0] if k + 1 < len(holdings_log) else port.index[-1]
        b0, b1 = _bench_asof(bench, d), _bench_asof(bench, end)
        bret = (b1 / b0 - 1.0) if (b0 and b1) else 0.0
        for c in codes:
            df = panel.get(c)
            if df is None:
                continue
            p0, p1 = _close_asof(df, d), _close_asof(df, end)
            if p0 and p1 and len(codes):
                contribs.append(((p1 / p0 - 1.0) - bret) / len(codes))
    pos = [x for x in contribs if x > 0]
    top_contrib = (max(pos) / sum(pos)) if pos else 0.0

    return XSMetrics(
        score=ir, ann_excess=ann_excess, excess_maxdd=excess_maxdd,
        monthly_win=monthly_win, t_stat=t_stat, turnover=turnover,
        n_holdings=n_holdings, top_contrib=top_contrib,
    )


def _bench_asof(bench: pd.Series, d) -> float | None:
    sub = bench.loc[:d]
    return float(sub.iloc[-1]) if len(sub) else None


def run_backtest(panel: dict | None = None, bench: pd.DataFrame | None = None) -> XSMetrics:
    """全窗口回测，返回 XSMetrics（score = IR）。panel/bench 省略则 load_panel()。"""
    if panel is None or bench is None:
        panel, bench = load_panel()
    reb = set(rebalance_dates(list(bench["date"])))
    port, benchc, log, tv, sp, sb = _simulate_xs(panel, bench, reb)
    return _compute_xs_metrics(port, benchc, log, tv, sp, sb, panel)


def evaluate_holdout(panel: dict | None = None, bench: pd.DataFrame | None = None) -> tuple:
    """样本内 vs 尾部 holdout。返回 (m_in, m_out, in_start, in_end, out_start, out_end)。
    holdout 段用 warmup 从头热身持仓，只对尾部计分。"""
    if panel is None or bench is None:
        panel, bench = load_panel()
    cal = list(bench["date"])
    split = int(len(cal) * (1 - HOLDOUT_FRAC))
    bench_in = bench.iloc[:split].reset_index(drop=True)
    p1, b1, l1, tv1, sp1, sb1 = _simulate_xs(panel, bench_in, set(rebalance_dates(list(bench_in["date"]))))
    m_in = _compute_xs_metrics(p1, b1, l1, tv1, sp1, sb1, panel)
    p2, b2, l2, tv2, sp2, sb2 = _simulate_xs(panel, bench, set(rebalance_dates(cal)), warmup_days=split)
    m_out = _compute_xs_metrics(p2, b2, l2, tv2, sp2, sb2, panel)
    return m_in, m_out, cal[0], cal[split - 1], cal[split], cal[-1]


def _fmt(m: XSMetrics) -> str:
    return (
        f"  IR(score)     : {m.score:+.4f}\n"
        f"  年化超额      : {m.ann_excess:+.2%}\n"
        f"  超额最大回撤  : {m.excess_maxdd:.2%}\n"
        f"  月度胜率      : {m.monthly_win:.1%}\n"
        f"  超额 t 统计量 : {m.t_stat:+.2f}\n"
        f"  年化换手(单边): {m.turnover:.2f}x\n"
        f"  有效持仓次数  : {m.n_holdings}\n"
        f"  单点最大贡献  : {m.top_contrib:.1%}"
    )


def main() -> None:
    panel, bench = load_panel()
    if "--holdout" in sys.argv:
        m_in, m_out, i0, i1, o0, o1 = evaluate_holdout(panel, bench)
        print(f"[样本内 {i0.date()}~{i1.date()}]")
        print(_fmt(m_in))
        print(f"\n[holdout {o0.date()}~{o1.date()}]")
        print(_fmt(m_out))
        print(f"\n样本内−holdout IR 分差: {m_in.score - m_out.score:+.4f}（>1.0 判过拟合）")
    else:
        m = run_backtest(panel, bench)
        print(f"[全窗口 {START_DATE}~{END_DATE}] 沪深300快照={SNAPSHOT_DATE} Top{TOP_N} 月度等权")
        print(_fmt(m))


if __name__ == "__main__":
    main()
