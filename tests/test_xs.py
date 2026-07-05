"""横截面 v2 单元测试（纯函数为主，不联网）。"""

import re

from example import universe_csi300 as uni


def test_pytest_wired():
    assert True


def test_universe_snapshot_valid():
    assert 250 <= len(uni.CSI300) <= 305
    assert len(set(uni.CSI300)) == len(uni.CSI300)
    pat = re.compile(r"^\d{6}\.(XSHG|XSHE)$")
    assert all(pat.match(c) for c in uni.CSI300)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", uni.SNAPSHOT_DATE)


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
    reb = set(bx.rebalance_dates(idx))
    port, benchc, log, turnover, sp, sb = bx._simulate_xs(panel, bench, reb)
    assert len(log) >= 2
    assert all(len(codes) == 2 for _, codes in log)
    assert all("A.XSHG" in codes for _, codes in log)      # 最强恒被选
    assert all("C.XSHG" not in codes for _, codes in log)  # 最弱不入选
    assert port.iloc[-1] > 0 and len(port) == len(idx)
    port2, *_ = bx._simulate_xs(panel, bench, reb)
    assert list(port) == list(port2)                       # 确定性
