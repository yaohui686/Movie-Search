"""优酷搜索：originNodes 是深度嵌套的组件树。

标题在 data.titleDTO.displayName，播放地址在 data.leftButtonDTO.action.value，
VIP 标记在 data.posterDTO.iconCorner.tagText。
"""
from __future__ import annotations

from .http import get_json, q

API = "https://search.youku.com/api/search?keyword={}"


def _dig(node, out: list, depth: int = 0):
    """收集所有带 docId 的 data 节点。"""
    if depth > 14 or len(out) >= 80:
        return
    if isinstance(node, dict):
        data = node.get("data")
        if isinstance(data, dict) and ("docId" in data or "showId" in data):
            out.append(data)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _dig(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _dig(v, out, depth + 1)


def _title(data: dict) -> str:
    dto = data.get("titleDTO") or {}
    if isinstance(dto, dict) and dto.get("displayName"):
        return str(dto["displayName"]).strip()
    for k in ("displayName", "title", "name"):
        if isinstance(data.get(k), str) and data[k].strip():
            return data[k].strip()
    return ""


def _url(data: dict) -> str:
    for key in ("leftButtonDTO", "rightButtonDTO"):
        dto = data.get(key) or {}
        if not isinstance(dto, dict):
            continue
        act = dto.get("action") or {}
        val = (act.get("value") if isinstance(act, dict) else "") or ""
        if val.startswith("http"):
            return val
    sid = data.get("showId") or ""
    return f"https://v.youku.com/v_show/id_{sid}.html" if sid else ""


def _vip(data: dict) -> str:
    dto = data.get("posterDTO") or {}
    if isinstance(dto, dict):
        corner = dto.get("iconCorner") or {}
        if isinstance(corner, dict) and corner.get("tagText"):
            return str(corner["tagText"])
    return ""


def search(name: str, limit: int = 12) -> list[dict]:
    try:
        data = get_json(API.format(q(name)))
    except Exception:
        return []

    raw: list[dict] = []
    _dig(data.get("originNodes") or [], raw)
    _dig(data.get("pageComponentList") or [], raw)

    out, seen = [], set()
    for d in raw:
        title = _title(d)
        if not title or title in seen:
            continue
        seen.add(title)
        vid = str(d.get("showId") or d.get("docId") or "")
        url = _url(d)
        # 优酷搜不到时会给出「跳转到别家」的链接（比如 v.qq.com），这不是优酷有版权
        external = bool(url) and "youku.com" not in url
        out.append({
            "source": "优酷",
            "title": title,
            "alias": "",
            "year": None,
            "type": "",
            "url": url,
            "id": vid,
            "vip": _vip(d),
            "external": external,
        })
        if len(out) >= limit:
            break
    return out
