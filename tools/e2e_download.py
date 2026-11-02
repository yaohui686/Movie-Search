"""下载链路验证：解析 m3u8 → 拉分片 → 拼接 → 校验文件。

    python tools/e2e_download.py                      # 自动找一个小资源
    python tools/e2e_download.py <m3u8-url> 10        # 指定 URL，只取前 10 个分片试跑
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import downloader, resource  # noqa: E402


def pick_url() -> str:
    cfg = json.loads((ROOT / "cfg" / "app.json").read_text(encoding="utf-8"))
    sources = cfg.get("resource_sources") or []
    rows = resource.search(sources, "流浪地球")
    cand = [r for r in rows if r.get("m3u8")]
    if not cand:
        raise SystemExit("没找到 m3u8 资源")
    # 挑分片最少的（预告片/短片），避免下载几个 G
    cand.sort(key=lambda r: r.get("episodes") or 999)
    r = cand[0]
    print(f"选中：{r['title']} ({r.get('year')})  {r['quality']}  {r['url']}")
    return r["url"]


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else pick_url()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    t0 = time.time()

    # 1) 解析（含主清单 / 嵌套清单递归）
    text, final_url = downloader.resolve_playlist(url)
    info = downloader.parse_m3u8(text, final_url)
    print(f"[1] 解析：解析层 {final_url[:70]}")
    print(f"    分片 {len(info['segments'])} 个  加密={info['encrypted']}  "
          f"init={bool(info['init'])}")

    if not info["segments"]:
        raise SystemExit("没有分片")

    # 2) 试拉前 N 个分片，验证防盗链是否通过
    probe = info["segments"][: (limit or 5)]
    total = 0
    for s in probe:
        data = downloader._fetch(s, timeout=30, retries=1)
        total += len(data)
    print(f"[2] 试拉 {len(probe)} 个分片：{total/1024:.0f} KB，平均 {total/len(probe)/1024:.0f} KB/片")

    if limit:
        # 只下前 limit 个分片，验证拼接结果是不是合法 TS
        out = ROOT / "logs" / "_test_download.ts"
        out.parent.mkdir(exist_ok=True)

        def cb(done, tot, note):
            print(f"\r    {done}/{tot} {note}", end="", flush=True)

        rc = downloader.download_python(url, out, on_progress=cb, max_segments=limit)
        print()
        data = out.read_bytes()
        head = data[:4]
        is_ts = data[0:1] == b"\x47"
        print(f"[3] 抽样下载 rc={rc}  {len(data)/1024:.0f} KB  "
              f"首字节={head.hex()}  {'✅ 合法 TS' if is_ts else '⚠️ 非 TS'}")
        print(f"\n耗时 {time.time()-t0:.1f}s")
        return

    # 3) 完整下载
    out = ROOT / "logs" / "_test_download.ts"
    out.parent.mkdir(exist_ok=True)

    def cb(done, tot, note):
        pct = done / tot * 100 if tot else 0
        print(f"\r    {done}/{tot} {pct:5.1f}% {note}", end="", flush=True)

    rc = downloader.download_python(url, out, on_progress=cb)
    print()
    size = out.stat().st_size / 1024 / 1024
    print(f"[3] 完成 rc={rc}  文件 {out.name}  {size:.1f} MB")
    head = out.open("rb").read(4)
    ok = head in (b"\x47\x40\x11\x10", b"\x47") or head[0:1] == b"\x47"
    print(f"[4] TS 同步字节校验：{head.hex()}  {'✅ 是合法 TS' if head[0:1]==b'\x47' else '⚠️ 可能不是 TS'}")
    print(f"\n耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
