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

# ============================================================
# 以下为从 strategy_xs.py 自动注入的策略核心，请勿手工改动
# ============================================================
LOOKBACK = 252          # 需要的已完成日线数（约12个月），引擎/聚宽据此取数
SKIP = 21               # 跳过最近约1个月（规避短期反转）


def score(history: dict) -> dict:
    """输入 {code: OHLC DataFrame(含 close 列，升序)}；输出 {code: 动量分数}。
    只看价格、只给分数；不下单、不读盘、不联网。"""
    out = {}
    for code, df in history.items():
        closes = df["close"].reset_index(drop=True)
        if len(closes) < LOOKBACK:
            continue
        p_recent = float(closes.iloc[-SKIP])       # 约1个月前
        p_old = float(closes.iloc[-LOOKBACK])      # 约12个月前
        if p_old > 0:
            out[code] = p_recent / p_old - 1.0
    return out
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
