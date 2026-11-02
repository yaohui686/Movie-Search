"""输出层：终端表格 + JSON。"""
from __future__ import annotations

import json


def _w(text: str, width: int) -> str:
    """按显示宽度补空格（中文占 2 列）。"""
    s = str(text or "")

    def dw(t: str) -> int:
        return sum(2 if ord(c) > 0x2E80 else 1 for c in t)

    w = dw(s)
    if w > width:
        out = ""
        for c in s:
            if dw(out + c) > width - 1:
                break
            out += c
        s = out + "…"
        w = dw(s)
    return s + " " * max(0, width - w)


# ---------------- 正版可用性 ----------------


def print_table(query: str, douban_hit: dict | None, rows: list[dict]):
    print()
    print(f"查询：{query}")
    if douban_hit:
        print(f"豆瓣命中：{douban_hit['title']} ({douban_hit.get('year') or '—'})"
              f"　类型：{douban_hit.get('type') or '—'}")
    print("-" * 96)

    print(f"{_w('平台', 10)}{_w('状态', 10)}{_w('片名', 28)}"
          f"{_w('年份', 7)}{_w('匹配', 7)}{_w('备注', 14)}链接")
    print("-" * 96)

    for r in rows:
        state = "有正版" if r.get("matched") else "未找到"
        print(
            _w(r["source"], 10)
            + _w(state, 10)
            + _w(r.get("title") or "—", 28)
            + _w(r.get("year") or "—", 7)
            + _w(r.get("score") or "—", 7)
            + _w(r.get("note") or "", 14)
            + (r.get("url") or "")
        )

    hit = [r for r in rows if r.get("matched")]
    print("-" * 96)
    if hit:
        print(f"共 {len(hit)} 个平台有正版：" + "、".join(r["source"] for r in hit))
    else:
        print("这些平台都没搜到明确匹配的正版。")
    print()


# ---------------- 资源检索 ----------------


def print_resources(query: str, rows: list[dict], limit: int = 25):
    print()
    print(f"资源检索：{query}")
    print("-" * 112)

    if not rows:
        print("没有搜到。")
        print()
        return

    print(f"{_w('#', 4)}{_w('片名', 26)}{_w('年份', 7)}{_w('类型', 10)}"
          f"{_w('画质', 8)}{_w('线路', 5)}{_w('集数', 5)}{_w('匹配', 7)}播放地址")
    print("-" * 112)

    for i, r in enumerate(rows[:limit], 1):
        print(
            _w(i, 4)
            + _w(r.get("title") or "—", 26)
            + _w(r.get("year") or "—", 7)
            + _w(r.get("type") or "—", 10)
            + _w(r.get("quality") or "未标注", 8)
            + _w(r.get("lines") or 1, 5)
            + _w(r.get("episodes") or 1, 5)
            + _w(r.get("score") or "—", 7)
            + (r.get("url") or "")
        )

    print("-" * 112)
    print(f"共 {len(rows)} 条结果（显示前 {min(limit, len(rows))} 条）")

    # 画质分布
    dist: dict[str, int] = {}
    for r in rows:
        q = r.get("quality") or "未标注"
        dist[q] = dist.get(q, 0) + 1
    if dist:
        order = ["4K", "1080P", "720P", "蓝光", "高清", "标清", "枪版", "未标注"]
        parts = [f"{k}×{dist[k]}" for k in order if k in dist]
        print("画质分布：" + "　".join(parts))

    srcs = sorted({s for r in rows for s in (r.get("sources") or [r.get("source")])})
    if srcs:
        print("来源：" + "、".join(srcs))

    print("提示：")
    print("  · python run.py link     <片名>      输出下载直链（--txt --clip 批量进迅雷，-e 选集）")
    print("  · python run.py info     <片名>      看真实分辨率（资源站标的画质经常虚高）")
    print("  · python run.py play     <片名>      本地播放器直接看")
    print("  · python run.py download <片名>      命令行下载（直链优先，-n 2 选第 2 条）")
    print()


def to_json(query: str, payload: dict) -> str:
    return json.dumps({"query": query, **payload}, ensure_ascii=False, indent=2)
