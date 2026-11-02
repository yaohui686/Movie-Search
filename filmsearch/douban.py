"""豆瓣：取影视元数据（片名、年份、类型、豆瓣链接）。

豆瓣的 suggest 接口返回 JSON，不需要登录，是最稳的元数据源。
"""
from __future__ import annotations

from .http import get_json, q

SUGGEST = "https://movie.douban.com/j/subject_suggest?q={}"


def search(name: str, limit: int = 8) -> list[dict]:
    try:
        data = get_json(SUGGEST.format(q(name)))
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    out = []
    for it in data[:limit]:
        title = it.get("title") or ""
        if not title:
            continue
        sub = it.get("sub_title") or ""
        out.append({
            "source": "豆瓣",
            "title": title,
            "sub_title": sub,
            "year": it.get("year") or None,
            "type": it.get("type") or it.get("type_name") or "",
            "id": it.get("id") or "",
            "url": it.get("url") or (
                f"https://movie.douban.com/subject/{it.get('id')}/" if it.get("id") else ""
            ),
            "poster": it.get("img") or "",
        })
    return out
