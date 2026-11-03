"""资源检索：聚合多个苹果CMS（MacCMS）资源站的采集接口。

这类站点的标准接口是  /api.php/provide/vod/?ac=detail&wd=关键词
返回 JSON，其中 vod_play_url 就是可直接播放的 m3u8 直链。

个人自用检索，不做分发。
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .http import get_json, q

LINE_SEP = "$$$"
EP_SEP = "#"

# 画质分级：从高到低匹配，命中即返回
QUALITY_LEVELS: list[tuple[str, tuple[str, ...]]] = [
    ("4K",    ("4K", "2160", "UHD")),
    ("1080P", ("1080", "FHD")),
    ("720P",  ("720",)),
    ("蓝光",  ("蓝光", "BLURAY", "BLU-RAY", "BDRIP", "BD")),
    ("高清",  ("高清", "HD")),
    ("标清",  ("标清", "SD")),
    ("枪版",  ("枪版", "TC", "TS", "CAM", "抢先", "盗摄")),
]

QUALITY_RANK = {name: i for i, (name, _) in enumerate(QUALITY_LEVELS)}
QUALITY_RANK["未标注"] = 99


def classify_quality(remarks: str) -> str:
    text = (remarks or "").upper()
    for level, keys in QUALITY_LEVELS:
        for k in keys:
            if k.upper() in text:
                return level
    return "未标注"


def parse_play_url(raw: str) -> list[list[tuple[str, str]]]:
    """把 vod_play_url 拆成 [[(集名, 地址), ...], ...]，每个子列表是一条线路。"""
    lines: list[list[tuple[str, str]]] = []
    for seg in (raw or "").split(LINE_SEP):
        eps: list[tuple[str, str]] = []
        for item in seg.split(EP_SEP):
            if "$" not in item:
                continue
            name, url = item.split("$", 1)
            url = url.strip()
            if url.startswith("http"):
                eps.append((name.strip() or "第1集", url))
        if eps:
            lines.append(eps)
    return lines


# 下载直链的协议前缀：http(s) 的整文件、网盘/BT 类协议都能交给下载器
DOWN_SCHEMES = ("http://", "https://", "ftp://", "magnet:", "ed2k:", "thunder:")


def _is_m3u8(url: str) -> bool:
    return (url or "").split("?")[0].lower().endswith(".m3u8")


def classify_down(url: str) -> str:
    """给下载链接分类：file=单文件直链，m3u8=播放清单，其余按协议名。"""
    low = (url or "").lower()
    if low.startswith("magnet:"):
        return "magnet"
    if low.startswith("ed2k:"):
        return "ed2k"
    if low.startswith("thunder:"):
        return "thunder"
    if low.startswith("ftp://"):
        return "ftp"
    u = (url or "").split("?")[0].lower()
    if u.endswith((".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
                   ".rmvb", ".ts", ".m4v", ".mpg")):
        return "file"
    if u.endswith(".m3u8"):
        return "m3u8"
    if low.startswith(("http://", "https://")):
        return "file"   # 网盘跳转/无后缀直链，下载器照样能拉
    return "other"


def parse_down_url(raw: str) -> list[tuple[str, str, str]]:
    """把 vod_down_url 拍平成 [(集名, 地址, 类型), ...]。

    与播放字段同一套分隔符：$$$ 拆线路、# 拆集、$ 拆「集名$地址」。
    多线路时会拿到重复集名，这里保留全部、由上层去重。
    """
    out: list[tuple[str, str, str]] = []
    for seg in (raw or "").split(LINE_SEP):
        for item in seg.split(EP_SEP):
            if "$" not in item:
                continue
            name, url = item.split("$", 1)
            url = url.strip()
            if not url.lower().startswith(DOWN_SCHEMES):
                continue
            out.append((name.strip() or "第1集", url, classify_down(url)))
    return out


def search_source(source: dict, name: str, limit: int = 20) -> tuple[list[dict], float]:
    """查单个源，返回 (结果列表, 耗时秒)。"""
    t0 = time.time()
    api = source["api"]
    url = f"{api.rstrip('/')}/api.php/provide/vod/?ac=detail&wd={q(name)}"
    try:
        # 批量检索不重试：单个源挂掉无所谓，重试只会把整体耗时翻倍
        data = get_json(url, timeout=8, retries=0)
    except Exception:
        return [], round(time.time() - t0, 2)

    out = []
    for it in (data.get("list") or [])[:limit]:
        title = it.get("vod_name") or ""
        if not title:
            continue
        lines = parse_play_url(it.get("vod_play_url") or "")
        if not lines:
            continue

        urls = [u for line in lines for _, u in line]
        m3u8_url = next((u for u in urls if ".m3u8" in u), "")
        first = m3u8_url or urls[0]

        remarks = it.get("vod_remarks") or ""
        down = parse_down_url(it.get("vod_down_url") or "")

        # 每集各自的播放链接也带回（剧集按集下载靠它）。
        # 同一集会出现在多条线路里：按集名去重，同名集优先保留 m3u8。
        play_map: dict[str, str] = {}
        for line in lines:
            for ep_name, u in line:
                ep = ep_name.strip() or "第1集"
                cur_u = play_map.get(ep)
                if cur_u is None:
                    play_map[ep] = u
                elif _is_m3u8(u) and not _is_m3u8(cur_u):
                    play_map[ep] = u
        play_flat = list(play_map.items())

        out.append({
            "source": source["name"],
            "title": title,
            "year": it.get("vod_year") or "",
            "type": it.get("type_name") or "",
            "area": it.get("vod_area") or "",
            "remarks": remarks,
            "quality": classify_quality(remarks),
            "quality_raw": remarks,
            "lines": len(lines),
            "episodes": max(len(l) for l in lines),
            "url": first,
            "m3u8": bool(m3u8_url),
            "play": play_flat,
            "down": down,
            "direct_down": any(k == "file" for _, _, k in down),
            "id": str(it.get("vod_id") or ""),
        })
    return out, round(time.time() - t0, 2)


def _merge(all_rows: list[dict]) -> list[dict]:
    """按「片名+年份」去重，保留画质最高、线路最多的那条。

    下载直链不受「谁当主条目」影响：各来源的直链并集都收进来，
    这样即使最佳画质来源没给 mp4，量子给的直链也不会丢。
    """
    merged: dict[tuple, dict] = {}
    for r in all_rows:
        key = (r["title"], r["year"])
        cur = merged.get(key)
        if cur is None:
            r["sources"] = [r["source"]]
            merged[key] = r
            continue

        cur["sources"].append(r["source"])
        cur["lines"] = max(cur["lines"], r["lines"])

        # 下载直链按地址去重后并入主条目
        have = {u for _, u, _ in cur.get("down") or []}
        for ep, u, k in r.get("down") or []:
            if u not in have:
                cur.setdefault("down", []).append((ep, u, k))
                have.add(u)

        # 每集播放清单同样并集：按集名去重，同名集优先 m3u8
        have_play: dict[str, str] = {}
        for ep, u in cur.get("play") or []:
            have_play.setdefault(ep, u)
        for ep, u in r.get("play") or []:
            cur_u = have_play.get(ep)
            if cur_u is None:
                have_play[ep] = u
            elif _is_m3u8(u) and not _is_m3u8(cur_u):
                have_play[ep] = u
        cur["play"] = list(have_play.items())

        better_quality = QUALITY_RANK.get(r["quality"], 99) < QUALITY_RANK.get(cur["quality"], 99)
        if (r["m3u8"] and not cur["m3u8"]) or better_quality:
            cur.update({
                "url": r["url"] if (r["m3u8"] and not cur["m3u8"]) else cur["url"],
                "m3u8": cur["m3u8"] or r["m3u8"],
                "quality": r["quality"] if better_quality else cur["quality"],
                "quality_raw": r["quality_raw"] if better_quality else cur["quality_raw"],
                # 备注跟着画质一起换，否则会出现「标签4K、备注HD」这种自相矛盾
                "remarks": r["remarks"] if better_quality else cur["remarks"],
            })
    for v in merged.values():
        v["sources"] = sorted(set(v["sources"]))
        v["down"] = v.get("down") or []
        v["play"] = v.get("play") or []
        v["direct_down"] = any(k == "file" for _, _, k in v["down"])
    return list(merged.values())


def search(sources: list[dict], name: str, limit: int = 20, progress=None) -> list[dict]:
    """单查询多源并发检索。

    progress(done, total, label, count, elapsed) 每完成一个源回调一次。
    """
    return search_multi(sources, [name], limit, progress)


def search_multi(sources: list[dict], queries: list[str], limit: int = 20,
                 progress=None, max_wait: float = 6.0) -> list[dict]:
    """多查询 × 多源 并发检索（模糊搜索的多路召回）。

    每个 (源, 查询变体) 组合是一个任务，全部并发跑。
    设有整体截止时间 max_wait：慢源超时就丢下先返回，不让一个源拖垮整次查询。
    """
    if not sources or not queries:
        return []

    tasks = [(s, q) for q in queries for s in sources]
    rows: list[dict] = []
    total = len(tasks)
    done = 0
    deadline = time.time() + max_wait

    pool = ThreadPoolExecutor(max_workers=min(16, total))
    try:
        fut_map = {pool.submit(search_source, s, q, limit): (s, q) for s, q in tasks}
        pending = set(fut_map)

        while pending:
            remain = deadline - time.time()
            if remain <= 0:
                break
            finished, pending = wait(
                pending, timeout=remain, return_when=FIRST_COMPLETED
            )
            if not finished:
                break
            for fut in finished:
                src, qv = fut_map[fut]
                done += 1
                try:
                    got, elapsed = fut.result()
                except Exception:
                    got, elapsed = [], 0.0
                if progress:
                    label = src.get("name", "?")
                    if len(queries) > 1:
                        label = f"{label}·{qv}"
                    progress(done, total, label, len(got), elapsed)
                rows.extend(got)
    finally:
        # 关键：不等慢线程。wait=True 会把截止时间完全废掉。
        # cancel_futures 需要 Python 3.9+；老版本只能 wait=False 兜底
        #（退出时会等在途任务收尾，最坏多等几秒，功能不受影响）。
        if sys.version_info >= (3, 9):
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=False)

    return _merge(rows)
