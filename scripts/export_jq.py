#!/usr/bin/env python3
"""把本地策略核心导出成聚宽(JoinQuant)平台脚本。

单一事实源：策略语义只写在 ``example/strategy.py`` 的 STRATEGY CORE 段里；
交易标的取自 ``example/backtest.py`` 的 ``SECURITY``。本脚本把两者拼进聚宽
回调模板，生成 ``example/jq_strategy_export.py``，从而保证本地回测与聚宽
终验跑的是同一份信号逻辑，避免手工维护两份而漂移。

用法：
    python scripts/export_jq.py            # 生成/覆盖 example/jq_strategy_export.py
    python scripts/export_jq.py --check    # 只校验已提交文件是否与当前源一致（供 harness）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = ROOT / "example" / "strategy.py"
BACKTEST_FILE = ROOT / "example" / "backtest.py"
OUTPUT_FILE = ROOT / "example" / "jq_strategy_export.py"

CORE_START = "# >>> STRATEGY CORE >>>"
CORE_END = "# <<< STRATEGY CORE <<<"

TEMPLATE = '''# ⚠️ 本文件由 scripts/export_jq.py 自动生成，请勿手工编辑。
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
SECURITY = "{security}"

# ============================================================
# 以下为从 strategy.py 自动注入的策略核心，请勿手工改动
# ============================================================
{core}
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
'''


def extract_core() -> str:
    text = STRATEGY_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == CORE_START)
        end = next(i for i, ln in enumerate(lines) if ln.strip() == CORE_END)
    except StopIteration:
        raise SystemExit(
            f"未在 {STRATEGY_FILE} 找到 STRATEGY CORE 标记（{CORE_START} / {CORE_END}）"
        )
    if end <= start:
        raise SystemExit("STRATEGY CORE 标记顺序异常。")
    return "\n".join(lines[start + 1:end]).strip("\n")


def read_security() -> str:
    text = BACKTEST_FILE.read_text(encoding="utf-8")
    m = re.search(r'^SECURITY\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"未在 {BACKTEST_FILE} 找到 SECURITY 定义。")
    return m.group(1)


def render() -> str:
    return TEMPLATE.format(security=read_security(), core=extract_core())


def main() -> None:
    content = render()
    if "--check" in sys.argv[1:]:
        if not OUTPUT_FILE.exists():
            raise SystemExit(f"{OUTPUT_FILE} 不存在，请先运行 python scripts/export_jq.py")
        if OUTPUT_FILE.read_text(encoding="utf-8") != content:
            raise SystemExit(
                f"{OUTPUT_FILE.name} 与 strategy.py/backtest.py 不一致（漂移）。\n"
                "请运行：python scripts/export_jq.py 重新生成后再提交。"
            )
        print("[export_jq] 聚宽导出脚本与源一致。")
        return
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"[export_jq] 已生成 {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
