# ⚠️ 本文件由 scripts/export_jq_xs.py 自动生成，请勿手工编辑。
# 生成源：example/strategy_xs.py 的 STRATEGY CORE + example/backtest_xs.py 的 TOP_N。
# 用途：复制到聚宽(joinquant.com)做全窗口、point-in-time 成分的实盘级终验。
#
# 与本地 backtest_xs.py 的差异（有意为之）：
#   - 成分用 get_index_stocks(基准, date) 取历史真实成分，规避本地固定快照的幸存者偏差；
#   - 聚宽用真实价格 + 现金分红落袋（本地用前复权全收益，见设计文档「分红处理」）；
#   - 盘前用「已完成 bar（截至昨收）」算分、当月首个交易日开盘成交，避免未来函数。
from jqdata import *
import pandas as pd

BENCHMARK = "000300.XSHG"
TOP_N = 5
TECH50 = ['000063.XSHE', '000100.XSHE', '000725.XSHE', '000938.XSHE', '000977.XSHE', '002049.XSHE', '002050.XSHE', '002230.XSHE', '002241.XSHE', '002371.XSHE', '002410.XSHE', '002415.XSHE', '002475.XSHE', '002555.XSHE', '002916.XSHE', '002920.XSHE', '300033.XSHE', '300059.XSHE', '300122.XSHE', '300124.XSHE', '300274.XSHE', '300308.XSHE', '300408.XSHE', '300433.XSHE', '300496.XSHE', '300628.XSHE', '300661.XSHE', '300919.XSHE', '300957.XSHE', '300999.XSHE', '600050.XSHG', '600183.XSHG', '600406.XSHG', '600460.XSHG', '600570.XSHG', '600584.XSHG', '600588.XSHG', '600745.XSHG', '600845.XSHG', '600941.XSHG', '601138.XSHG', '601360.XSHG', '603019.XSHG', '603501.XSHG', '603659.XSHG', '603799.XSHG', '603806.XSHG', '603833.XSHG', '603986.XSHG', '605117.XSHG']
FACTOR_SNAPSHOT = {'000063.XSHE': {'roe': 1.72, 'pe': 32.67}, '000100.XSHE': {'roe': 2.47, 'pe': 18.74}, '000725.XSHE': {'roe': 1.26, 'pe': 45.3}, '000938.XSHE': {'roe': 5.21, 'pe': 28.05}, '000977.XSHE': {'roe': 2.75, 'pe': 40.06}, '002049.XSHE': {'roe': 2.4, 'pe': 52.25}, '002050.XSHE': {'roe': 2.88, 'pe': 54.06}, '002230.XSHE': {'roe': -0.91, 'pe': -144.82}, '002241.XSHE': {'roe': 1.37, 'pe': 38.61}, '002371.XSHE': {'roe': 4.25, 'pe': 91.26}, '002410.XSHE': {'roe': 0.56, 'pe': 1.0047}, '002415.XSHE': {'roe': 3.28, 'pe': 27.66}, '002475.XSHE': {'roe': 4.2, 'pe': 32.36}, '002555.XSHE': {'roe': 6.22, 'pe': 12.28}, '002916.XSHE': {'roe': 4.83, 'pe': 89.54}, '002920.XSHE': {'roe': 2.95, 'pe': 27.27}, '300033.XSHE': {'roe': 2.67, 'pe': 1.8361}, '300059.XSHE': {'roe': 3.99, 'pe': 22.48}, '300122.XSHE': {'roe': -2.48, 'pe': -18.68}, '300124.XSHE': {'roe': 2.83, 'pe': 47.32}, '300274.XSHE': {'roe': 4.81, 'pe': 29.54}, '300308.XSHE': {'roe': 17.54, 'pe': 53.65}, '300408.XSHE': {'roe': 3.59, 'pe': 92.52}, '300433.XSHE': {'roe': -0.27, 'pe': -456.07}, '300496.XSHE': {'roe': 0.99, 'pe': 69.49}, '300628.XSHE': {'roe': 6.79, 'pe': 15.96}, '300661.XSHE': {'roe': 2.31, 'pe': 1.8708}, '300919.XSHE': {'roe': 2.29, 'pe': 19.67}, '300957.XSHE': {'roe': 1.07, 'pe': 51.48}, '300999.XSHE': {'roe': 1.52, 'pe': 22.29}, '600050.XSHG': {'roe': 1.24, 'pe': 14.78}, '600183.XSHG': {'roe': 6.68, 'pe': 84.91}, '600406.XSHG': {'roe': 1.36, 'pe': 63.61}, '600460.XSHG': {'roe': 1.73, 'pe': 95.48}, '600570.XSHG': {'roe': 1.4, 'pe': 72.22}, '600584.XSHG': {'roe': 1.01, 'pe': 1.4733}, '600588.XSHG': {'roe': -10.28, 'pe': -10.89}, '600745.XSHG': {'roe': -0.77, 'pe': -31.13}, '600845.XSHG': {'roe': 3.39, 'pe': 33.56}, '600941.XSHG': {'roe': 2.1, 'pe': 16.04}, '601138.XSHG': {'roe': 6.18, 'pe': 30.36}, '601360.XSHG': {'roe': 0.37, 'pe': 1.3853}, '603019.XSHG': {'roe': 1.02, 'pe': 1.5245}, '603501.XSHG': {'roe': 1.56, 'pe': 64.05}, '603659.XSHG': {'roe': 3.39, 'pe': 21.17}, '603799.XSHG': {'roe': 5.05, 'pe': 8.96}, '603806.XSHG': {'roe': 1.88, 'pe': 32.88}, '603833.XSHG': {'roe': 0.82, 'pe': 31.72}, '603986.XSHG': {'roe': 6.12, 'pe': 82.0}, '605117.XSHG': {'roe': 10.87, 'pe': 26.1}}

# ============================================================
# 以下为从 strategy_xs.py 自动注入的策略核心，请勿手工改动
# ============================================================
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
# ============================================================


def initialize(context):
    set_benchmark(BENCHMARK)
    set_option('use_real_price', True)
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
                             open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5),
                   type='stock')
    run_monthly(rebalance, 1, time='open', reference_security=BENCHMARK)


def rebalance(context):
    date = context.previous_date
    universe = [
        c for c in get_index_stocks(BENCHMARK, date=date)
        if (not c.startswith('688')) and (c in TECH50)
    ]
    hist = {}
    for code in universe:
        df = attribute_history(code, LOOKBACK, '1d', ['close'], skip_paused=True)
        closes = df['close'].dropna()
        if len(closes) >= LOOKBACK:
            hist[code] = pd.DataFrame({'close': closes.values})
    scores = score(hist)
    targets = sorted(scores, key=lambda c: scores[c], reverse=True)[:TOP_N]
    for code in list(context.portfolio.positions):      # 卖出不在目标里的
        if code not in targets:
            order_target(code, 0)
    if targets:                                          # 等权买入目标
        each = context.portfolio.total_value / len(targets)
        for code in targets:
            order_target_value(code, each)
