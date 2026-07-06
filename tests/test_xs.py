"""横截面 v2 单元测试（纯函数为主，不联网）。"""

import math
import re
import subprocess
import sys
from pathlib import Path

from example import universe_csi300 as uni
from example import universe_tech50 as tech50

_ROOT = Path(__file__).resolve().parent.parent


def test_pytest_wired():
    assert True


def test_universe_snapshot_valid():
    assert 250 <= len(uni.CSI300) <= 305
    assert len(set(uni.CSI300)) == len(uni.CSI300)
    pat = re.compile(r"^\d{6}\.(XSHG|XSHE)$")
    assert all(pat.match(c) for c in uni.CSI300)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", uni.SNAPSHOT_DATE)


def test_tech50_snapshot_valid():
    assert len(tech50.TECH50) == 50
    assert len(set(tech50.TECH50)) == 50
    assert all(code in uni.CSI300 for code in tech50.TECH50)
    assert all(not code.startswith("688") for code in tech50.TECH50)


import pandas as pd
import backtest_xs as bx
import strategy_xs


def test_momentum_score_ranks_and_filters():
    n = strategy_xs.LOOKBACK
    idx = pd.date_range("2019-01-01", periods=n)
    up = pd.DataFrame({"date": idx, "open": 1.0, "close": [1.0 + i * 0.01 for i in range(n)]})
    flat = pd.DataFrame({"date": idx, "open": 1.0, "close": [1.0] * n})
    short = up.iloc[-10:].reset_index(drop=True)
    s = strategy_xs.score({"A.XSHG": up, "B.XSHG": flat, "C.XSHG": short})
    assert "C.XSHG" not in s
    assert s["A.XSHG"] > s["B.XSHG"]


def test_rebalance_dates_first_trading_day_of_month():
    days = pd.to_datetime([
        "2020-01-02", "2020-01-03",
        "2020-02-03", "2020-02-04",
        "2020-03-02", "2020-03-31",
    ]).tolist()
    got = bx.rebalance_dates(days)
    assert got == pd.to_datetime(["2020-01-02", "2020-02-03", "2020-03-02"]).tolist()


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
    monkeypatch.setattr(bx, "_pit_constituents_at", lambda d: set(panel), raising=False)
    reb = set(bx.rebalance_dates(idx))
    port, benchc, log, turnover, sp, sb = bx._simulate_xs(panel, bench, reb)
    assert len(log) >= 2
    assert all(len(codes) == 2 for _, codes in log)
    assert all("A.XSHG" in codes for _, codes in log)      # 最强恒被选
    assert all("C.XSHG" not in codes for _, codes in log)  # 最弱不入选
    assert port.iloc[-1] > 0 and len(port) == len(idx)
    port2, *_ = bx._simulate_xs(panel, bench, reb)
    assert list(port) == list(port2)                       # 确定性


def test_simulate_xs_uses_previous_day_signal_on_rebalance_open(monkeypatch):
    idx = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"]).tolist()
    panel = {
        "A.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 20, 20],
            "close": [10, 20, 21, 21],
        }),
        "B.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 50, 50],
            "close": [10, 10, 50, 50],
        }),
    }
    bench = pd.DataFrame({"date": idx, "open": [100] * 4, "close": [100] * 4})

    monkeypatch.setattr(bx, "TOP_N", 1)
    monkeypatch.setattr(
        strategy_xs,
        "score",
        lambda hist: {code: float(df["close"].iloc[-1]) for code, df in hist.items()},
    )
    monkeypatch.setattr(bx, "_pit_constituents_at", lambda d: set(panel), raising=False)

    _, _, log, _, _, _ = bx._simulate_xs(panel, bench, {pd.Timestamp("2020-02-03")})

    assert log == [(pd.Timestamp("2020-02-03"), ["A.XSHG"])]


def test_simulate_xs_executes_on_rebalance_day_open(monkeypatch):
    idx = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"]).tolist()
    panel = {
        "A.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 10, 20],
            "close": [10, 10, 20, 20],
        }),
        "B.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 10, 10],
            "close": [10, 10, 10, 10],
        }),
    }
    bench = pd.DataFrame({"date": idx, "open": [100] * 4, "close": [100] * 4})

    monkeypatch.setattr(bx, "TOP_N", 1)
    monkeypatch.setattr(strategy_xs, "score", lambda hist: {"A.XSHG": 1.0, "B.XSHG": 0.0})
    monkeypatch.setattr(bx, "_pit_constituents_at", lambda d: set(panel), raising=False)

    port, _, _, _, _, _ = bx._simulate_xs(panel, bench, {pd.Timestamp("2020-02-03")})

    assert port.loc[pd.Timestamp("2020-02-03")] > bx.INITIAL_CASH


def test_simulate_xs_respects_point_in_time_universe(monkeypatch):
    idx = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-04"]).tolist()
    panel = {
        "A.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 10, 10],
            "close": [10, 20, 20, 20],
        }),
        "B.XSHG": pd.DataFrame({
            "date": idx,
            "open": [10, 10, 10, 10],
            "close": [10, 30, 30, 30],
        }),
    }
    bench = pd.DataFrame({"date": idx, "open": [100] * 4, "close": [100] * 4})

    monkeypatch.setattr(bx, "TOP_N", 1)
    monkeypatch.setattr(
        strategy_xs,
        "score",
        lambda hist: {code: float(df["close"].iloc[-1]) for code, df in hist.items()},
    )
    monkeypatch.setattr(
        bx,
        "_pit_constituents_at",
        lambda d: {"A.XSHG"},
        raising=False,
    )

    _, _, log, _, _, _ = bx._simulate_xs(panel, bench, {pd.Timestamp("2020-02-03")})

    assert log == [(pd.Timestamp("2020-02-03"), ["A.XSHG"])]


def test_pit_constituents_at_excludes_star_board():
    old = bx._pit_history_cache
    old_tech = bx.TECH50
    bx._pit_history_cache = pd.DataFrame({
        "symbol": ["SH688256", "SZ000001"],
        "name": ["寒武纪-U", "平安银行"],
        "opt-in": pd.to_datetime(["2023-12-08", "2005-04-08"]),
        "opt-out": pd.to_datetime([None, None]),
    })
    bx.TECH50 = {"000001.XSHE", "688256.XSHG"}
    try:
        got = bx._pit_constituents_at(pd.Timestamp("2024-11-01"))
        assert "000001.XSHE" in got
        assert "688256.XSHG" not in got
    finally:
        bx._pit_history_cache = old
        bx.TECH50 = old_tech


def test_pit_constituents_at_respects_tech50_whitelist():
    old_hist = bx._pit_history_cache
    old_tech = getattr(bx, "TECH50", None)
    bx._pit_history_cache = pd.DataFrame({
        "symbol": ["SZ000001", "SZ000063"],
        "name": ["平安银行", "中兴通讯"],
        "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08"]),
        "opt-out": pd.to_datetime([None, None]),
    })
    bx.TECH50 = {"000063.XSHE"}
    try:
        got = bx._pit_constituents_at(pd.Timestamp("2024-11-01"))
        assert got == {"000063.XSHE"}
    finally:
        bx._pit_history_cache = old_hist
        bx.TECH50 = old_tech


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
    monkeypatch.setattr(bx, "_pit_constituents_at", lambda d: set(panel), raising=False)
    m = bx.run_backtest(panel=panel, bench=bench)
    assert isinstance(m, bx.XSMetrics)
    assert math.isfinite(m.score)
    assert m.n_holdings > 0


def test_export_jq_xs_render_and_check():
    py = sys.executable
    subprocess.run([py, "scripts/export_jq_xs.py"], cwd=_ROOT, check=True)
    out = (_ROOT / "example" / "jq_strategy_xs_export.py").read_text(encoding="utf-8")
    assert "get_index_stocks" in out          # point-in-time 成分
    assert "def score" in out                  # 核心已注入
    assert "TOP_N = 5" in out                   # 与 backtest_xs 同步
    assert "startswith('688')" in out          # 非科创板过滤同步到聚宽终验
    assert "TECH50 = [" in out                 # 科技50白名单同步到聚宽终验
    r = subprocess.run([py, "scripts/export_jq_xs.py", "--check"], cwd=_ROOT)
    assert r.returncode == 0                    # 漂移检查通过
