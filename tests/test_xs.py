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


def test_rebalance_dates_first_trading_day_of_month():
    days = pd.to_datetime([
        "2020-01-02", "2020-01-03",
        "2020-02-03", "2020-02-04",
        "2020-03-02", "2020-03-31",
    ]).tolist()
    got = bx.rebalance_dates(days)
    assert got == pd.to_datetime(["2020-01-02", "2020-02-03", "2020-03-02"]).tolist()
