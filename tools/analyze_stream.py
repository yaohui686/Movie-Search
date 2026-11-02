"""分析一条资源的 m3u8 真实结构：是否多码率、各变体分辨率、是否需要 Referer。

    python tools/analyze_stream.py 泰坦尼克号
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import resource  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def fetch(url, referer=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def referer_of(url):
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


def analyze(url, depth=0, label=""):
    pad = "  " * depth
    print(f"{pad}URL: {url}")

    # 1) 不带 Referer 试着拉（模拟迅雷）
    try:
        no_ref = fetch(url)
        print(f"{pad}不带 Referer: ✅ 可拉 {len(no_ref)}B")
        blocked = False
    except Exception as exc:
        print(f"{pad}不带 Referer: ❌ {type(exc).__name__} {str(exc)[:50]}")
        blocked = True

    # 2) 带 Referer
    try:
        text = fetch(url, referer_of(url))
        print(f"{pad}带 Referer:   ✅ 可拉 {len(text)}B")
    except Exception as exc:
        print(f"{pad}带 Referer:   ❌ {type(exc).__name__}")
        return

    # 3) 是不是主清单
    if "#EXT-X-STREAM-INF" in text:
        print(f"{pad}类型: 多码率主清单（master playlist）")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            res = re.search(r"RESOLUTION=(\d+x\d+)", line)
            bw = re.search(r"BANDWIDTH=(\d+)", line)
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith("#"):
                    sub = urllib.parse.urljoin(url, s)
                    print(f"{pad}  ├─ 分辨率={res.group(1) if res else '?'}  "
                          f"码率={int(bw.group(1))//1000 if bw else '?'}kbps")
                    print(f"{pad}  │  {sub}")
                    break
        return

    # 4) 普通清单
    segs = [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    print(f"{pad}类型: 单码率清单   分片 {len(segs)} 个")

    if len(segs) == 1:
        print(f"{pad}→ 单条目，可能是嵌套清单，继续解析")
        analyze(segs[0], depth + 1)
        return

    # 5) 看第一个分片能不能不带 Referer 拉
    try:
        seg_url = segs[0]
        req = urllib.request.Request(seg_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(16)
        print(f"{pad}首分片不带 Referer: ✅ {len(data)}B 头={data[:4].hex()}")
    except Exception as exc:
        print(f"{pad}首分片不带 Referer: ❌ {type(exc).__name__}")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "泰坦尼克号"
    cfg = json.loads((ROOT / "cfg" / "app.json").read_text(encoding="utf-8"))
    rows = resource.search(cfg.get("resource_sources") or [], name)
    rows = [r for r in rows if r.get("m3u8")]
    if not rows:
        print("没找到 m3u8")
        return

    print(f"共 {len(rows)} 条 m3u8 资源，逐条分析：\n")
    for r in rows[:6]:
        print("=" * 70)
        print(f"{r['title']} ({r.get('year')})  标签画质={r.get('quality')}  "
              f"备注={r.get('remarks')!r}  源={r.get('sources')}")
        try:
            analyze(r["url"])
        except Exception as exc:
            print(f"  分析失败: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
