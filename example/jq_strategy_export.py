# ⚠️ 本文件由 scripts/export_jq.py 自动生成，请勿手工编辑。
# 生成源：example/strategy.py 的 STRATEGY CORE + example/backtest.py 的 SECURITY。
# 用途：复制到聚宽(joinquant.com)策略编辑器，做全窗口、实盘口径的终验。
#
# 与本地 backtest.py 的差异（有意为之，正是用聚宽做实盘级终验的意义）：
#   - 聚宽用真实价格、T+1、100 股整手、滑点、涨跌停/停牌等真实约束；
#   - 信号在盘前用「已完成 bar（截至昨收）」计算、当日开盘成交，避免未来函数；
#     本地回测用「截至今日收盘」决策、当日收盘成交。两者不会完全一致。
from jqdata import *
import pandas as pd

# —— 交易标的（自动同步自 backtest.py 的 SECURITY）——
SECURITY = "510300.XSHG"

# ============================================================
# 以下为从 strategy.py 自动注入的策略核心，请勿手工改动
# ============================================================
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
# ============================================================


def initialize(context):
    set_benchmark(SECURITY)
    set_option('use_real_price', True)
    # 成本对齐本地 backtest.py：买万3，卖万3+千1印花，最低5元
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
                             open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5),
                   type='stock')
    run_daily(market_open, time='open', reference_security=SECURITY)


def market_open(context):
    # 用已完成的 LOOKBACK 根日线（不含今日）算信号 → 当日开盘按目标仓位下单
    hist = attribute_history(SECURITY, LOOKBACK, '1d', ['close'], skip_paused=True)
    closes = hist['close'].dropna()
    if len(closes) < LOOKBACK:
        return
    signal = decide(closes)
    # 幂等门控：只在需要切换持仓状态时下单，对齐本地 backtest.py 的
    # 「shares==0 才买、shares>0 才卖」。否则满仓时每日重复委托微调、空仓时
    # 反复发清仓单，会刷「下单数量为0」「开仓数量不能小于100」等报错，
    # 并引入本地回测没有的每日调仓噪声，导致与 holdout 结果偏离。
    position = context.portfolio.positions[SECURITY].total_amount
    if signal == "buy" and position == 0:
        order_target_value(SECURITY, context.portfolio.total_value)
    elif signal == "sell" and position > 0:
        order_target(SECURITY, 0)
    # hold 或持仓状态无需改变：不下单
