"""扫描多条资源，统计真实分辨率分布——回答「标 4K 的到底是不是 4K」。

    python tools/scan_quality.py 泰坦尼克号
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import resource  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def probe(row):
    url = row["url"]
    out = {"title": row["title"], "year": row.get("year"), "tag": row.get("quality"),
           "src": ",".join(row.get("sources") or []), "res": [], "err": ""}
    try:
        text = fetch(url)
    except Exception as exc:
        out["err"] = type(exc).__name__
        return out

    for _ in range(4):
        if "#EXT-X-STREAM-INF" not in text:
            break
        lines = text.splitlines()
        nxt_url = None
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            res = re.search(r"RESOLUTION=(\d+x\d+)", line)
            bw = re.search(r"BANDWIDTH=(\d+)", line)
            out["res"].append((
                res.group(1) if res else "?",
                int(bw.group(1)) // 1000 if bw else 0,
            ))
            for n in lines[i + 1:]:
                s = n.strip()
                if s and not s.startswith("#"):
                    nxt_url = urllib.parse.urljoin(url, s)
                    break
            break
        if not nxt_url:
            break
        url = nxt_url
        try:
            text = fetch(url)
        except Exception:
            break

    if not out["res"]:
        segs = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        out["res"].append(("单码率", len(segs)))
    return out


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "泰坦尼克号"
    cfg = json.loads((ROOT / "cfg" / "app.json").read_text(encoding="utf-8"))
    rows = [r for r in resource.search(cfg.get("resource_sources") or [], name) if r.get("m3u8")]
    print(f"{name}：共 {len(rows)} 条 m3u8 资源，逐条探测真实分辨率\n")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, rows))

    fmt = "{:<26}{:<6}{:<7}{:<14}{:<28}{}"
    print(fmt.format("片名", "年份", "标签", "真实分辨率", "来源", "备注"))
    print("-" * 110)
    stats = {}
    for r in sorted(results, key=lambda x: str(x["res"])):
        res = r["res"]
        if res and isinstance(res[0][1], int) and res[0][1] > 100:
            resstr = " / ".join(f"{a}@{b}k" for a, b in res)
            key = res[0][0]
        else:
            resstr = f"分片{res[0][1]}个" if res else "-"
            key = "单码率"
        stats[key] = stats.get(key, 0) + 1
        print(fmt.format(
            (r["title"] or "")[:24], str(r["year"] or ""), str(r["tag"] or ""),
            resstr[:26], (r["src"] or "")[:26], r["err"],
        ))

    print("\n真实分辨率分布：")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v} 条")

    has_4k = any("2160" in k or "4K" in k.upper() for k in stats)
    print(f"\n是否发现真 4K（2160p）：{'✅ 有' if has_4k else '❌ 没有'}")


if __name__ == "__main__":
    main()
