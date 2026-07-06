#!/usr/bin/env python3
"""生成科技50多因子当前快照（ROE + PE）。

数据源：EastMoney push2 stock/get
- f173: ROE(%)
- f162: 市盈率(动态)，作为 PE 近似；EP = 1 / PE

注意：这是“当前快照、全窗口冻结”的近似口径，不是 point-in-time 基本面。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "example" / "factor_snapshot_tech50.py"
if str(ROOT / "example") not in sys.path:
    sys.path.insert(0, str(ROOT / "example"))

from universe_tech50 import TECH50  # noqa: E402

PARTIAL = ROOT / "scripts" / ".cache" / "factor_snapshot_tech50_partial.json"
RETRIES = 5


def secid(code: str) -> str:
    sym = code.split(".")[0]
    market = "1" if sym.startswith("6") else "0"
    return f"{market}.{sym}"


def fetch_one(code: str) -> dict:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {"secid": secid(code), "fields": "f57,f58,f162,f173"}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()["data"]
            return {
                "roe": float(data.get("f173") or 0.0),
                "pe": float(data.get("f162") or 0.0) / 100.0,
            }
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= RETRIES:
                raise
            time.sleep(delay)
            delay *= 2
    raise last_exc  # type: ignore[misc]


def build() -> dict:
    PARTIAL.parent.mkdir(exist_ok=True)
    done = {}
    if PARTIAL.exists():
        done = json.loads(PARTIAL.read_text(encoding="utf-8"))
        for item in done.values():
            if item.get("pe", 0) > 100:
                item["pe"] = item["pe"] / 100.0
        print(f"[fetch_factor_snapshot_tech50] 续跑 partial：已有 {len(done)} 只")
    for code in TECH50:
        if code in done:
            continue
        try:
            done[code] = fetch_one(code)
            PARTIAL.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print(f"[fetch_factor_snapshot_tech50] skip {code}: {type(exc).__name__}")
        time.sleep(0.3)
    return done


def main() -> None:
    snap = build()
    if len(snap) < len(TECH50):
        missing = [code for code in TECH50 if code not in snap]
        raise SystemExit(
            f"快照未拉全：{len(snap)}/{len(TECH50)}。缺失: {missing[:10]}"
            + (" ..." if len(missing) > 10 else "")
        )
    today = dt.date.today().isoformat()
    rows = []
    for code in TECH50:
        item = snap[code]
        rows.append(f'    "{code}": {{"roe": {item["roe"]!r}, "pe": {item["pe"]!r}}}')
    body = ",\n".join(rows)
    OUT.write_text(
        '"""科技50 多因子快照（由 scripts/fetch_factor_snapshot_tech50.py 生成，提交入库）。\n\n'
        f"快照日期 FACTOR_SNAPSHOT_DATE={today}；字段：roe(%)、pe(动态)。\n"
        "⚠️ 基本面是当前快照、全窗口冻结，仅作多因子入门基线近似，不是 point-in-time 基本面。\n"
        '"""\n\n'
        f'FACTOR_SNAPSHOT_DATE = "{today}"\n\n'
        "FACTOR_SNAPSHOT = {\n"
        f"{body},\n"
        "}\n",
        encoding="utf-8",
    )
    PARTIAL.unlink(missing_ok=True)
    print(f"[fetch_factor_snapshot_tech50] 写入 {OUT}（{len(snap)} 只，快照 {today}）")


if __name__ == "__main__":
    main()
