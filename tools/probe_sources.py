"""探测苹果CMS（MacCMS）资源站采集接口的可用性。

用法:
    python tools/probe_sources.py
    python tools/probe_sources.py --write      # 把可用源写回 cfg/app.json

判定标准：
  1) HTTP 200
  2) 返回合法 JSON，含 code=list 字段
  3) list 非空（说明接口真的在提供数据）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch.http import get  # noqa: E402

# 候选源（名称, API 根地址）。都按 /api.php/provide/vod/ 的苹果CMS标准接口探测。
CANDIDATES = [
    ("量子资源",   "https://cj.lziapi.com"),
    ("非凡资源",   "https://api.apibdzy.com"),
    ("电影天堂",   "https://caiji.dyttzyapi.com"),
    ("无尽资源",   "https://api.wujinapi.me"),
    ("如意资源",   "https://cj.rycjapi.com"),
    ("优子资源",   "https://cj.yzzyapi.com"),
    ("光速资源",   "https://api.guangsuapi.com"),
    ("天空资源",   "https://api.tiankongapi.com"),
    ("魔都资源",   "https://caiji.moduapi.cc"),
    ("黑木耳",     "https://json.heimuer.xyz"),
    ("非凡影视",   "https://cj.ffzyapi.com"),
    ("虎牙资源",   "https://www.huyaapi.com"),
    ("金鹰资源",   "https://jyzyapi.com"),
    ("快车资源",   "https://caiji.kczyapi.com"),
    ("暴风资源",   "https://bfzyapi.com"),
    ("星空资源",   "https://xkzyapi.com"),
    ("极速资源",   "https://jszyapi.com"),
    ("卧龙资源",   "https://collect.wolongzyw.com"),
    ("闪电资源",   "https://sdzyapi.com"),
    ("索尼资源",   "https://suoniapi.com"),
    ("红牛资源",   "https://www.hongniuzy2.com"),
    ("豪华资源",   "https://hhzyapi.com"),
    ("最大资源",   "https://api.zuidapi.com"),
    ("百川资源",   "https://bczyapi.com"),
    ("大米资源",   "https://www.damizy.com"),
    ("飘零资源",   "https://p2100.net"),
    ("魔爪资源",   "https://mozhuazy.com"),
    ("ikun资源",   "https://ikunzyapi.com"),
    ("ck资源",     "https://ckzy.me"),
    ("360资源",    "https://360zy.com"),
]

PROBE_KEYWORD = "流浪地球"


def probe(item: tuple[str, str]) -> dict:
    name, api = item
    url = f"{api.rstrip('/')}/api.php/provide/vod/?ac=detail&wd={quote(PROBE_KEYWORD)}"
    t0 = time.time()
    rec = {"name": name, "api": api, "ok": False, "count": 0,
           "elapsed": 0.0, "note": ""}
    try:
        raw = get(url, timeout=10, retries=0)
    except Exception as exc:
        rec["elapsed"] = round(time.time() - t0, 2)
        rec["note"] = f"{type(exc).__name__}"
        return rec

    rec["elapsed"] = round(time.time() - t0, 2)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        rec["note"] = "非 JSON"
        return rec

    if not isinstance(data, dict):
        rec["note"] = "结构异常"
        return rec

    lst = data.get("list")
    if not isinstance(lst, list):
        rec["note"] = "无 list 字段"
        return rec

    rec["ok"] = bool(lst)
    rec["count"] = len(lst)
    rec["note"] = "OK" if lst else "空结果"
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="把可用源写回 cfg/app.json")
    args = ap.parse_args(argv)

    print(f"探测 {len(CANDIDATES)} 个候选源（关键词：{PROBE_KEYWORD}）…\n")
    rows = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(probe, c): c for c in CANDIDATES}
        for i, fut in enumerate(as_completed(futs), 1):
            rows.append(fut.result())

    rows.sort(key=lambda r: (not r["ok"], -r["count"], r["elapsed"]))

    fmt = "{:<12}{:<6}{:<7}{:<8}{:<7}{} "
    print(fmt.format("名称", "可用", "条数", "耗时", "状态", "API"))
    print("-" * 96)
    for r in rows:
        print(fmt.format(
            r["name"][:10], "✅" if r["ok"] else "❌",
            r["count"], f"{r['elapsed']}s", r["note"][:6], r["api"],
        ))

    good = [r for r in rows if r["ok"]]
    print(f"\n可用 {len(good)} / {len(rows)}")

    if args.write and good:
        cfg_path = ROOT / "cfg" / "app.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["resource_sources"] = [{"name": r["name"], "api": r["api"]} for r in good]
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {cfg_path}（{len(good)} 个源）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
