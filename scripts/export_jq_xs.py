#!/usr/bin/env python3
"""把横截面策略核心导出成聚宽脚本（point-in-time 成分终验）。

单一事实源：score() 只写在 example/strategy_xs.py 的 STRATEGY CORE 段；
TOP_N 取自 example/backtest_xs.py。生成 example/jq_strategy_xs_export.py。

用法：
    python scripts/export_jq_xs.py           # 生成/覆盖
    python scripts/export_jq_xs.py --check    # 校验漂移（供 harness）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = ROOT / "example" / "strategy_xs.py"
BACKTEST_FILE = ROOT / "example" / "backtest_xs.py"
OUTPUT_FILE = ROOT / "example" / "jq_strategy_xs_export.py"

CORE_START = "# >>> STRATEGY CORE >>>"
CORE_END = "# <<< STRATEGY CORE <<<"

TEMPLATE = '''# ⚠️ 本文件由 scripts/export_jq_xs.py 自动生成，请勿手工编辑。
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
TOP_N = {top_n}

# ============================================================
# 以下为从 strategy_xs.py 自动注入的策略核心，请勿手工改动
# ============================================================
{core}
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
    universe = [c for c in get_index_stocks(BENCHMARK, date=date) if not c.startswith('688')]
    hist = {{}}
    for code in universe:
        df = attribute_history(code, LOOKBACK, '1d', ['close'], skip_paused=True)
        closes = df['close'].dropna()
        if len(closes) >= LOOKBACK:
            hist[code] = pd.DataFrame({{'close': closes.values}})
    scores = score(hist)
    targets = sorted(scores, key=lambda c: scores[c], reverse=True)[:TOP_N]
    for code in list(context.portfolio.positions):      # 卖出不在目标里的
        if code not in targets:
            order_target(code, 0)
    if targets:                                          # 等权买入目标
        each = context.portfolio.total_value / len(targets)
        for code in targets:
            order_target_value(code, each)
'''


def extract_core() -> str:
    lines = STRATEGY_FILE.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == CORE_START)
        end = next(i for i, ln in enumerate(lines) if ln.strip() == CORE_END)
    except StopIteration:
        raise SystemExit(f"未在 {STRATEGY_FILE} 找到 STRATEGY CORE 标记。")
    if end <= start:
        raise SystemExit("STRATEGY CORE 标记顺序异常。")
    return "\n".join(lines[start + 1:end]).strip("\n")


def read_top_n() -> str:
    text = BACKTEST_FILE.read_text(encoding="utf-8")
    m = re.search(r"^TOP_N\s*=\s*(\d+)", text, re.MULTILINE)
    if not m:
        raise SystemExit(f"未在 {BACKTEST_FILE} 找到 TOP_N 定义。")
    return m.group(1)


def render() -> str:
    return TEMPLATE.format(top_n=read_top_n(), core=extract_core())


def main() -> None:
    content = render()
    if "--check" in sys.argv[1:]:
        if not OUTPUT_FILE.exists():
            raise SystemExit(f"{OUTPUT_FILE} 不存在，请先运行 python scripts/export_jq_xs.py")
        if OUTPUT_FILE.read_text(encoding="utf-8") != content:
            raise SystemExit(
                f"{OUTPUT_FILE.name} 与 strategy_xs.py/backtest_xs.py 不一致（漂移）。\n"
                "请运行：python scripts/export_jq_xs.py 重新生成后再提交。"
            )
        print("[export_jq_xs] 聚宽截面导出脚本与源一致。")
        return
    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"[export_jq_xs] 已生成 {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
