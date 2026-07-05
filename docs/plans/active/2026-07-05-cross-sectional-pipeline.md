# 横截面选股 v2 评估器 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 交付一台物理隔离、口径冻结、可复现的横截面固定评估器 v2，让 HL 只改 `strategy_xs.py` 的 `score()` 即可迭代沪深300 月度等权 Top3 选股，按信息比率(IR)超额打分，并能导出聚宽 point-in-time 终验脚本。

**Architecture:** 单标的版 `strategy.py`/`backtest.py` 原封不动，新增并行 v2 一套。`strategy_xs.py` 暴露纯打分函数 `score(history)->{code:分数}`（唯一可编辑）；`backtest_xs.py` 固定评估器负责数据、月度撮合（T+1开盘/整手/滑点/成本）、IR 超额打分、尾部 holdout；`export_jq_xs.py` 把策略核心注入聚宽截面模板。

**Tech Stack:** Python 3（conda env `hl-quant`），pandas（已装），标准库 `urllib`/`json`；测试用 pytest（本计划新增）。数据源：腾讯财经公开前复权日线（免认证），基准 `sh000300`。

**设计依据：** `docs/design/cross-sectional-pipeline.md`（已定稿，含两级验收门槛、分红口径、幸存者偏差处理）。

---

## 约定

- 所有命令在 worktree 根 `/.worktrees/xs-pipeline` 下执行；Python 用 `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python`（下称 `PY`）。
- 代码风格对齐现有 `example/backtest.py`（中文注释、类型注解、纯函数优先）。
- 每个 Task 以 `git commit` 收尾；提交前跑 `./scripts/harness.sh`。
- 股票代码统一内部格式 `NNNNNN.XSHG`（沪）/`NNNNNN.XSHE`（深），与聚宽一致；腾讯前缀转换复用 `_tencent_symbol` 思路（6/5→sh，其余→sz）。

## 文件结构（先锁定边界）

| 文件 | 职责 | 状态 |
| --- | --- | --- |
| `example/universe_csi300.py` | 沪深300 固定快照：`SNAPSHOT_DATE` + `CSI300: list[str]` 代码清单 | 新建（生成物，提交） |
| `scripts/fetch_universe.py` | 一次性拉取沪深300成分、生成 `universe_csi300.py` | 新建（工具） |
| `example/strategy_xs.py` | 唯一可编辑：`score()` + `LOOKBACK`/`REBALANCE` + CORE 标记 | 新建 |
| `example/backtest_xs.py` | 固定评估器 v2：数据层/月度撮合/IR打分/holdout/main | 新建 |
| `scripts/export_jq_xs.py` | 截面版聚宽导出 + `--check` | 新建 |
| `example/jq_strategy_xs_export.py` | 聚宽脚本（生成物） | 新建（生成物） |
| `tests/test_xs.py` | 纯函数单测（pytest） | 新建 |
| `requirements.txt` | 增加 pytest | 修改 |
| `scripts/harness.sh` | 纳入 v2 的 py_compile/pytest/export_jq_xs --check | 修改 |
| `docs/design/hl-ledger.md` | 追加 v2 基线锚点 | 修改 |

## 测试策略

- **纯函数 TDD**（pytest）：月度调仓日、组合撮合、IR/超额指标、动量打分——用合成小数据断言确定性结果，不联网。
- **联网数据层**：把「原始 kline 行 → 面板」的纯装配函数单独测；真实拉取只做 smoke（跑一次全窗口不报错）。
- harness 第 4 段接入 `pytest -q`。

---

### Task 0: 测试工具就位（pytest）

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`（空文件）
- Create: `tests/test_xs.py`（先放一个占位通过用例）

- [ ] **Step 1: 安装 pytest 并加入依赖**

Run:
```bash
/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pip install pytest
```
然后在 `requirements.txt` 末尾追加一行：
```
pytest>=8.0
```

- [ ] **Step 2: 建占位测试确认框架可跑**

Create `tests/__init__.py` 为空文件。Create `tests/test_xs.py`：
```python
"""横截面 v2 单元测试（纯函数为主，不联网）。"""


def test_pytest_wired():
    assert True
```

- [ ] **Step 3: 运行 pytest 确认通过**

Run: `cd /Users/oncezhou/Downloads/quant/hl-quant/.worktrees/xs-pipeline && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/ -q`
Expected: `1 passed`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt tests/__init__.py tests/test_xs.py
git commit -m "chore: 引入 pytest 作为 v2 单测框架"
```

---

### Task 1: 沪深300 固定快照清单

用 EastMoney 公开接口（免认证）拉当前沪深300成分，落成静态 `universe_csi300.py` 提交。若接口不可用，属数据源阻塞：停下报告，不要伪造清单。

**Files:**
- Create: `scripts/fetch_universe.py`
- Create: `example/universe_csi300.py`（由上面脚本生成后提交）
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写生成脚本**

Create `scripts/fetch_universe.py`：
```python
#!/usr/bin/env python3
"""一次性拉取沪深300成分股，生成 example/universe_csi300.py（静态快照，提交入库）。

数据源：EastMoney push2 clist 公开接口（免认证）。沪深300 板块 fs=b:MK0300。
返回每只 f12=代码, f13=市场(1=沪,0=深)。转成聚宽格式 NNNNNN.XSHG/XSHE。
接口不可用即抛错停止——不要用残缺清单继续。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "example" / "universe_csi300.py"
URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=500&po=1&np=1&fltt=2&invt=2&fid=f12&fs=b:MK0300&fields=f12,f13,f14"
)


def fetch() -> list[str]:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    diff = (payload.get("data") or {}).get("diff") or []
    codes = []
    for row in diff:
        code = str(row["f12"]).zfill(6)
        suffix = "XSHG" if int(row["f13"]) == 1 else "XSHE"
        codes.append(f"{code}.{suffix}")
    codes = sorted(set(codes))
    if not (250 <= len(codes) <= 305):
        raise SystemExit(f"成分数异常({len(codes)})，疑似接口变更/受限，停止。")
    return codes


def main() -> None:
    codes = fetch()
    today = dt.date.today().isoformat()
    body = ",\n".join(f'    "{c}"' for c in codes)
    OUT.write_text(
        '"""沪深300 成分固定快照（由 scripts/fetch_universe.py 生成，提交入库）。\n\n'
        f"快照日期 SNAPSHOT_DATE={today}；共 {len(codes)} 只。\n"
        "⚠️ 固定快照＝用今日成分回看历史，含幸存者偏差（偏乐观）；无偏结论以聚宽\n"
        "get_index_stocks(日期) 的 point-in-time 成分终验为准。\n"
        '"""\n\n'
        f'SNAPSHOT_DATE = "{today}"\n\n'
        f"CSI300 = [\n{body},\n]\n"
    )
    print(f"[fetch_universe] 写入 {OUT}（{len(codes)} 只，快照 {today}）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 生成静态清单**

Run: `cd /Users/oncezhou/Downloads/quant/hl-quant/.worktrees/xs-pipeline && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python scripts/fetch_universe.py`
Expected: 打印 `写入 .../universe_csi300.py（约300 只，快照 YYYY-MM-DD）`；生成 `example/universe_csi300.py`。
若报「成分数异常」或网络错误 → 数据源阻塞，停止并报告，不要手编清单。

- [ ] **Step 3: 写清单校验测试**

在 `tests/test_xs.py` 追加：
```python
import re

from example import universe_csi300 as uni


def test_universe_snapshot_valid():
    assert 250 <= len(uni.CSI300) <= 305
    assert len(set(uni.CSI300)) == len(uni.CSI300)  # 无重复
    pat = re.compile(r"^\d{6}\.(XSHG|XSHE)$")
    assert all(pat.match(c) for c in uni.CSI300)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", uni.SNAPSHOT_DATE)
```
说明：`example/` 需可作为包被 import——建 `example/__init__.py`（空）。若已存在则跳过。

- [ ] **Step 4: 运行测试**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全部 passed（含 `test_universe_snapshot_valid`）。

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_universe.py example/universe_csi300.py example/__init__.py tests/test_xs.py
git commit -m "feat: 沪深300成分固定快照清单 + 生成脚本（标注幸存者偏差）"
```

---

### Task 2: `backtest_xs.py` 骨架 + 月度调仓日（纯函数 TDD）

**Files:**
- Create: `example/backtest_xs.py`（先只放固定口径常量 + `rebalance_dates`）
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（月度首个交易日）**

在 `tests/test_xs.py` 追加：
```python
import pandas as pd

from example import backtest_xs as bx


def test_rebalance_dates_first_trading_day_of_month():
    days = pd.to_datetime([
        "2020-01-02", "2020-01-03",           # 1月
        "2020-02-03", "2020-02-04",           # 2月（2/1、2/2 周末）
        "2020-03-02", "2020-03-31",           # 3月
    ]).tolist()
    got = bx.rebalance_dates(days)
    assert got == pd.to_datetime(["2020-01-02", "2020-02-03", "2020-03-02"]).tolist()
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_rebalance_dates_first_trading_day_of_month -q`
Expected: FAIL（`module 'example.backtest_xs' has no attribute ...` 或 import 失败）。

- [ ] **Step 3: 写骨架与实现**

Create `example/backtest_xs.py`：
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed。

- [ ] **Step 5: Commit**

```bash
git add example/backtest_xs.py tests/test_xs.py
git commit -m "feat: v2评估器骨架 + 月度调仓日纯函数"
```

---

### Task 3: 数据面板装配 + `load_panel`

把「原始 kline 行 → OHLC 表」的纯装配抽出来单测；真实联网拉取只做 smoke。

**Files:**
- Modify: `example/backtest_xs.py`
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（纯装配函数）**

在 `tests/test_xs.py` 追加：
```python
def test_rows_to_ohlc_dedup_and_sort():
    rows = [
        ["2020-01-03", "1.1", "1.2", "1.3", "1.0", "100"],
        ["2020-01-02", "1.0", "1.05", "1.1", "0.9", "100"],
        ["2020-01-03", "1.1", "1.25", "1.3", "1.0", "100"],  # 同日重复，后者覆盖
    ]
    df = bx._rows_to_ohlc(rows, "2020-01-01", "2020-12-31")
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == ["2020-01-02", "2020-01-03"]
    assert df.iloc[1]["close"] == 1.25   # 去重取后者
    assert df.iloc[0]["open"] == 1.0
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_rows_to_ohlc_dedup_and_sort -q`
Expected: FAIL（`_rows_to_ohlc` 未定义）。

- [ ] **Step 3: 实现装配 + load_panel**

在 `example/backtest_xs.py` 追加：
```python
_TENCENT_PAGE = 800
_TENCENT_MAX_PAGES = 30


def _rows_to_ohlc(rows: list, start: str, end: str) -> pd.DataFrame:
    """腾讯 kline 行 [date,open,close,high,low,vol] → 去重排序后的 open/close 表。"""
    acc: dict[str, tuple] = {}
    for r in rows:
        acc[r[0]] = (float(r[1]), float(r[2]))     # date -> (open, close)
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

    bench = _fetch_ohlc(_tencent_symbol(BENCHMARK))
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
```
说明：`_tencent_symbol` 对指数 `000300.XSHG` 会得到 `sh000300`（6 开头→sh），符合腾讯指数约定。

- [ ] **Step 4: 运行装配测试**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_rows_to_ohlc_dedup_and_sort -q`
Expected: PASS。

- [ ] **Step 5: 联网 smoke（人工跑一次，确认能拉到面板）**

Run: `cd example && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python -c "import backtest_xs as b; p,bench=b.load_panel(); print(len(p),'只', len(bench),'基准日')"`
Expected: 打印约 `~300 只 ~1976 基准日`（首次较慢，之后读缓存）。若报数据源阻塞 → 停下报告。

- [ ] **Step 6: Commit**

```bash
git add example/backtest_xs.py tests/test_xs.py
git commit -m "feat: v2数据面板装配(_rows_to_ohlc)与load_panel(逐股+基准+缓存)"
```

---

### Task 4: `strategy_xs.py` 唯一可编辑（12-1 动量打分）

先于撮合引擎建，避免 `backtest_xs` import `strategy_xs` 失败。

**Files:**
- Create: `example/strategy_xs.py`
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（动量排序 + 长度过滤）**

在 `tests/test_xs.py` 追加：
```python
from example import strategy_xs


def test_momentum_score_ranks_and_filters():
    n = strategy_xs.LOOKBACK
    idx = pd.date_range("2019-01-01", periods=n)
    up = pd.DataFrame({"date": idx, "open": 1.0, "close": [1.0 + i * 0.01 for i in range(n)]})
    flat = pd.DataFrame({"date": idx, "open": 1.0, "close": [1.0] * n})
    short = up.iloc[-10:].reset_index(drop=True)              # 长度不足
    s = strategy_xs.score({"A.XSHG": up, "B.XSHG": flat, "C.XSHG": short})
    assert "C.XSHG" not in s                                  # 长度不足被过滤
    assert s["A.XSHG"] > s["B.XSHG"]                          # 涨得多的分高
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_momentum_score_ranks_and_filters -q`
Expected: FAIL（`example.strategy_xs` 不存在）。

- [ ] **Step 3: 实现 strategy_xs**

Create `example/strategy_xs.py`：
```python
"""横截面策略程序 —— 唯一可编辑文件（the single editable program）。

HL 只允许改本文件；固定评估器 backtest_xs.py 不动。score() 是纯函数：
输入每只股票截至调仓日的 OHLC，输出 {code: 分数}，引擎排序取 Top-N 等权。

当前基线：12-1 横截面动量——用「约1个月前 / 约12个月前」的累计收益排序，
跳过最近约1个月以规避短期反转。经济含义：过去一年相对强势的股票倾向延续。
"""
from __future__ import annotations

import pandas as pd

REBALANCE = "monthly"   # 声明式：调仓节奏由引擎固定执行（引擎读取，不进 CORE）

# >>> STRATEGY CORE >>>
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
# <<< STRATEGY CORE <<<
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed。

- [ ] **Step 5: Commit**

```bash
git add example/strategy_xs.py tests/test_xs.py
git commit -m "feat: 横截面策略基线(12-1动量) strategy_xs.score"
```

---

### Task 5: 组合撮合引擎 `_simulate_xs`（次日开盘/整手/滑点/等权 Top-N）

**Files:**
- Modify: `example/backtest_xs.py`
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（选股 + 确定性 + 净值为正）**

在 `tests/test_xs.py` 追加：
```python
def _ramp(idx, start, step):
    vals = [start + step * i for i in range(len(idx))]
    return pd.DataFrame({"date": idx, "open": vals, "close": vals})


def test_simulate_xs_selects_top_and_is_deterministic(monkeypatch):
    idx = pd.bdate_range("2020-01-01", periods=90).tolist()
    panel = {
        "A.XSHG": _ramp(idx, 10, 0.20),   # 最强
        "B.XSHG": _ramp(idx, 10, 0.10),
        "C.XSHG": _ramp(idx, 10, 0.02),   # 最弱
    }
    bench = _ramp(idx, 100, 0.05)
    monkeypatch.setattr(strategy_xs, "LOOKBACK", 5)
    monkeypatch.setattr(strategy_xs, "SKIP", 1)
    monkeypatch.setattr(bx, "TOP_N", 2)
    reb = set(bx.rebalance_dates(idx))
    port, benchc, log, turnover, sp, sb = bx._simulate_xs(panel, bench, reb)
    assert len(log) >= 2
    assert all(len(codes) == 2 for _, codes in log)
    assert all("A.XSHG" in codes for _, codes in log)      # 最强恒被选
    assert all("C.XSHG" not in codes for _, codes in log)  # 最弱不入选
    assert port.iloc[-1] > 0 and len(port) == len(idx)
    port2, *_ = bx._simulate_xs(panel, bench, reb)
    assert list(port) == list(port2)                       # 确定性
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_simulate_xs_selects_top_and_is_deterministic -q`
Expected: FAIL（`_simulate_xs` 未定义）。

- [ ] **Step 3: 实现撮合引擎**

在 `example/backtest_xs.py` 追加：
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed。

- [ ] **Step 5: Commit**

```bash
git add example/backtest_xs.py tests/test_xs.py
git commit -m "feat: v2组合撮合引擎(_simulate_xs/_rebalance/_order_value)"
```

---

### Task 6: IR 超额指标 `_compute_xs_metrics`

**Files:**
- Modify: `example/backtest_xs.py`
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（正超额→IR/胜率/t 为正）**

在 `tests/test_xs.py` 追加：
```python
def test_xs_metrics_basic():
    idx = pd.bdate_range("2021-01-01", periods=252).tolist()
    port = pd.Series([100000 * (1.0006 ** i) for i in range(len(idx))], index=idx)
    bench = pd.Series([100000 * (1.0002 ** i) for i in range(len(idx))], index=idx)
    m = bx._compute_xs_metrics(port, bench, [], 0.0, port.iloc[0], bench.iloc[0], {})
    assert m.score > 0                    # IR 正（组合日日跑赢基准）
    assert m.ann_excess > 0
    assert 0.9 <= m.monthly_win <= 1.0
    assert m.t_stat > 0
    assert 0.0 <= m.top_contrib <= 1.0
    assert m.n_holdings == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_xs_metrics_basic -q`
Expected: FAIL（`_compute_xs_metrics` 未定义）。

- [ ] **Step 3: 实现指标**

在 `example/backtest_xs.py` 追加：
```python
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
    pm = port.resample("M").last().pct_change().dropna()
    bm = bench.resample("M").last().pct_change().dropna()
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
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed。

- [ ] **Step 5: Commit**

```bash
git add example/backtest_xs.py tests/test_xs.py
git commit -m "feat: v2 IR超额指标(_compute_xs_metrics: IR/超额/胜率/换手/单点贡献)"
```

---

### Task 7: `run_backtest` / `evaluate_holdout` / `main` 装配

**Files:**
- Modify: `example/backtest_xs.py`
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（合成面板端到端）**

在 `tests/test_xs.py` 顶部补 `import math`，并追加：
```python
def test_run_backtest_on_synthetic(monkeypatch):
    idx = pd.bdate_range("2020-01-01", periods=120).tolist()
    panel = {
        "A.XSHG": _ramp(idx, 10, 0.20), "B.XSHG": _ramp(idx, 10, 0.10),
        "C.XSHG": _ramp(idx, 10, 0.05), "D.XSHG": _ramp(idx, 10, 0.01),
    }
    bench = _ramp(idx, 100, 0.05)
    monkeypatch.setattr(strategy_xs, "LOOKBACK", 5)
    monkeypatch.setattr(strategy_xs, "SKIP", 1)
    monkeypatch.setattr(bx, "TOP_N", 2)
    m = bx.run_backtest(panel=panel, bench=bench)
    assert isinstance(m, bx.XSMetrics)
    assert math.isfinite(m.score)
    assert m.n_holdings > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_run_backtest_on_synthetic -q`
Expected: FAIL（`run_backtest` 未定义）。

- [ ] **Step 3: 实现装配与 CLI**

在 `example/backtest_xs.py` 追加：
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed。

- [ ] **Step 5: 真实全窗口 smoke（人工）**

Run: `cd example && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python backtest_xs.py`
Expected: 打印全窗口 IR 及各指标（数值即基线，Task 10 记账）。再跑 `python backtest_xs.py --holdout` 确认样本内/holdout 两段都出数。

- [ ] **Step 6: Commit**

```bash
git add example/backtest_xs.py tests/test_xs.py
git commit -m "feat: v2 run_backtest/evaluate_holdout/main 装配与报告"
```

---

### Task 8: 聚宽截面导出 `export_jq_xs.py`（point-in-time 成分）

聚宽终验用 `get_index_stocks(基准, date)` 取**历史真实成分**，从而规避本地固定快照的幸存者偏差。CORE（`score`+`LOOKBACK`+`SKIP`）自动注入，`TOP_N` 从 `backtest_xs.py` 同步。

**Files:**
- Create: `scripts/export_jq_xs.py`
- Create: `example/jq_strategy_xs_export.py`（生成物）
- Modify: `tests/test_xs.py`

- [ ] **Step 1: 写失败测试（生成 + 内容 + --check）**

在 `tests/test_xs.py` 顶部补 `import subprocess, sys`、`from pathlib import Path`，并追加：
```python
_ROOT = Path(__file__).resolve().parent.parent


def test_export_jq_xs_render_and_check():
    py = sys.executable
    subprocess.run([py, "scripts/export_jq_xs.py"], cwd=_ROOT, check=True)
    out = (_ROOT / "example" / "jq_strategy_xs_export.py").read_text(encoding="utf-8")
    assert "get_index_stocks" in out          # point-in-time 成分
    assert "def score" in out                  # 核心已注入
    assert "TOP_N = 3" in out                   # 与 backtest_xs 同步
    r = subprocess.run([py, "scripts/export_jq_xs.py", "--check"], cwd=_ROOT)
    assert r.returncode == 0                    # 漂移检查通过
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py::test_export_jq_xs_render_and_check -q`
Expected: FAIL（`scripts/export_jq_xs.py` 不存在，subprocess 非零退出）。

- [ ] **Step 3: 实现导出脚本**

Create `scripts/export_jq_xs.py`：
```python
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
    universe = get_index_stocks(BENCHMARK, date=date)   # point-in-time 成分
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
```
注意：模板里聚宽回调用到的 `{{}}`/`{{'close': ...}}` 是为了 `str.format` 转义花括号，生成后会还原成正常的 `{}`。

- [ ] **Step 4: 生成并运行测试**

Run: `/Users/oncezhou/miniconda3/envs/hl-quant/bin/python -m pytest tests/test_xs.py -q`
Expected: 全 passed（测试内部会生成 `jq_strategy_xs_export.py` 并 --check）。

- [ ] **Step 5: Commit**

```bash
git add scripts/export_jq_xs.py example/jq_strategy_xs_export.py tests/test_xs.py
git commit -m "feat: 聚宽截面导出(export_jq_xs) + point-in-time成分终验脚本"
```

---

### Task 9: 把 v2 纳入 harness 门禁

**Files:**
- Modify: `scripts/harness.sh`

- [ ] **Step 1: py_compile 增补 v2 文件**

把 `scripts/harness.sh` 第 2 段的 `py_compile` 调用改为（新增 4 个文件）：
```bash
echo "[harness] py_compile"
"${PY}" -m py_compile \
  example/strategy.py \
  example/backtest.py \
  example/jq_strategy_export.py \
  scripts/export_jq.py \
  example/strategy_xs.py \
  example/backtest_xs.py \
  example/jq_strategy_xs_export.py \
  scripts/export_jq_xs.py
```

- [ ] **Step 2: 漂移检查增补 export_jq_xs**

在第 3 段 `python scripts/export_jq.py --check` 之后追加一行：
```bash
"${PY}" scripts/export_jq_xs.py --check
```

- [ ] **Step 3: 第 4 段接入 pytest**

把第 4 段 TODO 注释替换为实际单测调用：
```bash
# ---- 4. 单元测试 -----------------------------------------------------------
echo "[harness] pytest"
"${PY}" -m pytest tests/ -q
```

- [ ] **Step 4: 跑整套 harness**

Run: `cd /Users/oncezhou/Downloads/quant/hl-quant/.worktrees/xs-pipeline && ./scripts/harness.sh`
Expected: 依次打印 `git diff --check` / `py_compile` / 两个 `export_jq*.py 与源一致` / `pytest ... passed` / `[harness] OK`。
注意：harness 里 `PY` 默认 `python3`；若该解释器无 pandas/pytest，先 `export PYTHON=/Users/oncezhou/miniconda3/envs/hl-quant/bin/python` 再跑。

- [ ] **Step 5: Commit**

```bash
git add scripts/harness.sh
git commit -m "chore: harness 纳入 v2(py_compile/export_jq_xs --check/pytest)"
```

---

### Task 10: 冻结 v2 基线并记账

评估器闭环后，跑一次基线并把数值登记为「横截面基线锚点」，作为后续 HL 轮次的比较基准和门槛数值来源。**这是记账，不是提分**：无论 IR 正负都如实记录。

**Files:**
- Modify: `docs/design/hl-ledger.md`

- [ ] **Step 1: 采基线数值**

Run: `cd example && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python backtest_xs.py && /Users/oncezhou/miniconda3/envs/hl-quant/bin/python backtest_xs.py --holdout`
记录：全窗口 IR、年化超额、超额最大回撤、月度胜率、t 统计量、有效持仓次数、单点最大贡献；以及样本内 vs holdout 的 IR。

- [ ] **Step 2: 写入账本**

在 `docs/design/hl-ledger.md` 追加一节「横截面 v2 基线（12-1 动量，沪深300快照 Top3 月度等权）」，填入 Step 1 的实测数值，并据此把设计文档两级门槛里以「基线」为参照的项落成具体数字（IR 基线值、样本内−holdout 分差阈值）。格式对齐现有账本条目（假设/口径/指标/诊断/决策）。

- [ ] **Step 3: 验证并提交**

Run: `./scripts/harness.sh`（应仍 OK）
```bash
git add docs/design/hl-ledger.md
git commit -m "docs: 登记横截面v2基线锚点(12-1动量)作为HL比较基准"
```

- [ ] **Step 4: 闭环收尾**

- 若基线可用（能跑通、数值合理）：v2 评估器即交付，后续按 `docs/design/cross-sectional-pipeline.md` 两级门槛跑 HL 轮次改 `strategy_xs.py`。
- 把本计划从 `docs/plans/active/` 移到 `docs/plans/completed/`（任务状态随目录）。
- 分支 `feat/xs-pipeline` 是否合入 `main`：评估器/基础设施属工具性变更，可按常规评审合入；**策略提分**仍须过两级门槛且经用户确认后才合。

---

## 自检（Self-Review）

**1. 设计文档覆盖**（对照 `docs/design/cross-sectional-pipeline.md`）：
- 沪深300固定快照 → Task 1；月度调仓 → Task 2；等权 Top3 → Task 5/`TOP_N`；12-1 动量 → Task 4；
  次日开盘/整手/滑点/成本 → Task 5；IR 打分 → Task 6；尾部 holdout → Task 7；
  point-in-time 终验 → Task 8；两级门槛数值锚定 → Task 10。
- 分红处理：本地保留前复权（Task 3 `_fetch_ohlc` 用腾讯前复权），差异在设计文档「G. 分红处理」已登记，聚宽模板注释重申——无需代码，属已知缺口。
- 幸存者偏差：本地快照在 `universe_csi300.py` 头注 + 聚宽 `get_index_stocks(date)` point-in-time 对冲，覆盖。

**2. 占位符扫描**：无 TBD/TODO 式空步骤；每个代码步骤含完整代码；命令含预期输出。

**3. 类型/命名一致性**：
- `score(history: dict)->dict`（Task 4）与引擎调用（Task 5）、聚宽模板（Task 8）一致；
- `_simulate_xs` 返回 6 元组，`run_backtest`/`evaluate_holdout`（Task 7）按该顺序解包；
- `XSMetrics` 字段（Task 2 定义）与 `_compute_xs_metrics` 返回（Task 6）、`_fmt`（Task 7）字段名一致；
- `LOOKBACK`/`SKIP` 在 CORE 段（Task 4），被引擎、聚宽模板共用；`TOP_N` 在 backtest_xs（Task 2），被引擎与 export_jq_xs 读取。

**已知取舍**：`top_contrib` 为「单只×单期」close-to-close 等权近似（忽略整手/成本），作为单点主导的量级探针，够用；`turnover` 为粗略年化单边估计。二者只报告/软判，不做硬门槛，符合设计。

