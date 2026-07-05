#!/usr/bin/env python3
"""一次性拉取沪深300成分股，生成 example/universe_csi300.py（静态快照，提交入库）。

主数据源：EastMoney push2 clist（免认证）。备用：新浪 Market_Center hs300 分页。
转成聚宽格式 NNNNNN.XSHG/XSHE。接口均不可用即抛错——不要用残缺清单继续。
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "example" / "universe_csi300.py"

EASTMONEY_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=500&po=1&np=1&fltt=2&invt=2&fid=f12&fs=b:MK0300&fields=f12,f13,f14"
)
SINA_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs300"
)


def _validate(codes: list[str]) -> list[str]:
    codes = sorted(set(codes))
    if not (250 <= len(codes) <= 305):
        raise SystemExit(f"成分数异常({len(codes)})，疑似接口变更/受限，停止。")
    return codes


def _sina_symbol(raw: str) -> str:
    """sh600000 / sz000001 → 600000.XSHG / 000001.XSHE"""
    prefix, code = raw[:2], raw[2:]
    suffix = "XSHG" if prefix == "sh" else "XSHE"
    return f"{code}.{suffix}"


def fetch_eastmoney() -> list[str]:
    req = urllib.request.Request(
        EASTMONEY_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    diff = (payload.get("data") or {}).get("diff") or []
    codes = []
    for row in diff:
        code = str(row["f12"]).zfill(6)
        suffix = "XSHG" if int(row["f13"]) == 1 else "XSHE"
        codes.append(f"{code}.{suffix}")
    return _validate(codes)


def fetch_sina() -> list[str]:
    codes: list[str] = []
    for page in range(1, 6):
        url = SINA_URL.format(page=page)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            break
        for row in rows:
            sym = row.get("symbol") or ""
            if sym.startswith(("sh", "sz")) and len(sym) >= 8:
                codes.append(_sina_symbol(sym))
    return _validate(codes)


def fetch() -> list[str]:
    try:
        return fetch_eastmoney()
    except Exception as exc:
        print(f"[fetch_universe] EastMoney 不可用({exc})，改用新浪备用源…")
        return fetch_sina()


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
