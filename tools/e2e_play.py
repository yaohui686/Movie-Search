"""真实链路验证：搜索 → 取 m3u8 → 本地代理 → 检查分片是否被改写。

    python tools/e2e_play.py 流浪地球
"""
from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import player, resource  # noqa: E402

cfg_path = ROOT / "cfg" / "app.json"
import json  # noqa: E402
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
sources = cfg.get("resource_sources") or []

name = sys.argv[1] if len(sys.argv) > 1 else "流浪地球"
rows = resource.search(sources, name)
rows = [r for r in rows if r.get("m3u8")]
if not rows:
    print("没找到 m3u8 资源")
    raise SystemExit(1)

r = rows[0]
print(f"目标：{r['title']} ({r.get('year')})  {r['quality']}")
print(f"直链：{r['url']}")

srv = player.PlayServer(0)
port = srv.start()
print(f"代理服务：127.0.0.1:{port}")

try:
    # 1) 播放页
    page = f"http://127.0.0.1:{port}/play?u=" + urllib.parse.quote(r["url"], safe="")
    with urllib.request.urlopen(page, timeout=10) as resp:
        html = resp.read().decode("utf-8")
    print(f"\n[1] 播放页 {resp.status}  {len(html)}B  hls.js={'hls.js' in html}")

    # 2) 代理拉 m3u8
    prox = f"http://127.0.0.1:{port}/proxy?u=" + urllib.parse.quote(r["url"], safe="")
    with urllib.request.urlopen(prox, timeout=20) as resp:
        m3u8 = resp.read().decode("utf-8", "replace")
        ct = resp.headers.get("Content-Type")
    lines = [l for l in m3u8.splitlines() if l.strip()]
    segs = [l for l in lines if not l.startswith("#")]
    print(f"[2] 代理 m3u8 {resp.status}  {ct}  {len(lines)} 行  分片 {len(segs)} 个")
    print(f"    首行: {lines[0] if lines else '(空)'}")
    if segs:
        print(f"    首个分片: {segs[0][:100]}")
    ok = segs and all("/proxy?u=" in s for s in segs)
    print(f"    分片已改写: {'✅' if ok else '❌'}")

    # 3) 拉第一个分片，验证代理能取到二进制
    if segs:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{segs[0]}", timeout=25) as resp:
            data = resp.read()
        print(f"[3] 首分片 {resp.status}  {len(data)/1024:.0f} KB  "
              f"type={resp.headers.get('Content-Type')}")

    print(f"\n浏览器打开体验：{page}")
finally:
    srv.stop()
