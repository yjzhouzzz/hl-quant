# 单标的择时：本地打磨 → 部分数据验证 → 聚宽终验

本文定义「方向 1（单标的 / 单 ETF 择时）」的三段式闭环：在本地用固定评估器
迭代策略，用一年窗口内的尾部数据做样本外确认，最后把**同一份策略**导出到
聚宽平台做实盘口径的终验。

## 核心原则：策略语义单一事实源

```
        ┌──────────────────────────────┐
        │  example/strategy.py         │
        │  STRATEGY CORE：decide()+参数 │  ← 唯一可编辑、唯一事实源
        └───────┬───────────────┬──────┘
                │ import        │ scripts/export_jq.py 提取
                ▼               ▼
   example/backtest.py    example/jq_strategy_export.py
   固定评估器（本地）        聚宽平台脚本（终验）
```

- 策略逻辑只写在 `strategy.py` 的 `# >>> STRATEGY CORE >>>` … `# <<< STRATEGY CORE <<<` 之间。
- 本地回测器 `backtest.py` **import** 它；聚宽脚本由 `scripts/export_jq.py` **提取**它。
- 三段跑的是同一份 `decide()`；`harness.sh` 用 `export_jq.py --check` 防止漂移。

**约束**：STRATEGY CORE 只能依赖 `pandas`，不得做文件 IO / 联网 / 本地 import，
否则无法原样注入聚宽脚本。均线等窗口应 ≤ `LOOKBACK`（聚宽盘前取数长度）。

## 三个阶段

### 阶段 1：本地打磨（HL 循环）

```bash
cd example
python backtest.py            # 全窗口打分（score = Sortino）
```

固定评估器口径（一次性设定后**冻结**，之后只改 `strategy.py`）：

| 项 | 值 |
| --- | --- |
| 标的 SECURITY | `510300.XSHG`（沪深300ETF，可交易） |
| 区间 | 2025-03-01 ~ 2026-02-28 |
| 初始资金 | 100,000（个人实盘 10 万） |
| 成本 | 佣金万3、卖出印花千1、最低5元 |
| score | Sortino |

> 注：本地回测用小数股、当日收盘成交，**不含** T+1/整手/涨跌停，属研究口径。

### 阶段 2：部分数据验证（尾部 holdout）

```bash
cd example
python backtest.py --holdout  # 样本内(前~80%) vs 尾部 holdout(后~20%)
```

- 最近约 20%（~2.4 个月）留作 **run-once** 样本外确认。
- **重要约束**：本策略低频（全年个位数交易），holdout 段交易稀薄（常 0~2 笔），
  故 holdout 仅作**方向性确认**，**不作硬门槛**。每轮真正的反过拟合把关靠：
  - 前置经济含义（每轮先写死一句市场解释再看分）；
  - 参数高原（窗口 ±20% 分数平稳，而非单点尖峰）；
  - 全样本交易笔数下限。
- 详见反过拟合方法论四层：训练/验证隔离、双重不退化、经济含义、样本量门槛。

### 阶段 3：聚宽平台终验（实盘口径）

```bash
python scripts/export_jq.py   # 生成 example/jq_strategy_export.py
```

把 `example/jq_strategy_export.py` 复制进聚宽策略编辑器，跑全窗口回测。聚宽引擎
提供本地缺失的**执行真实性**：真实价格、T+1、100 股整手、滑点、涨跌停/停牌。

**时序约定差异（有意为之）**：聚宽在盘前用「已完成 bar（截至昨收）」算信号、
当日开盘成交，避免未来函数；本地用「截至今日收盘」决策、当日收盘成交。两者
不会完全一致——这正是用聚宽做实盘级终验的意义。

**它验证什么、不验证什么**：聚宽终验解决的是**执行真实性**；若跑的是同一段
数据，它不是统计意义上的新样本 OOS。过拟合与执行真实是两个维度，都要过。

## 验证门禁

```bash
./scripts/harness.sh
```

- `git diff --check`：空白/冲突标记。
- `py_compile`：栈内 Python 文件语法。
- `export_jq --check`：聚宽脚本与策略核心一致（防漂移）。

## 改策略后的标准流程

1. 只改 `strategy.py` 的 STRATEGY CORE（保持仅依赖 pandas、窗口 ≤ LOOKBACK）。
2. `python backtest.py` 打分，`--holdout` 看样本外方向。
3. `python scripts/export_jq.py` 重新生成聚宽脚本。
4. `./scripts/harness.sh` 通过后提交。
5. 候选冻结后，在聚宽平台跑一次全窗口终验（run-once）。
