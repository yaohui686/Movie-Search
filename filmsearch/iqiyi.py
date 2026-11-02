"""爱奇艺搜索：判断某部片在爱奇艺是否有正版资源。

接口返回 JSON，字段埋在 albumDocInfo / videoDocInfo 里，做多重兜底。
"""
from __future__ import annotations

from .http import get_json, q

API = "https://search.video.iqiyi.com/o?if=html5&key={}"


def _year_of(alb: dict) -> int | None:
    for key in ("releaseDate", "year", "publishTime", "startDate"):
        v = alb.get(key)
        if not v:
            continue
        s = str(v)
        for i in range(len(s) - 3):
            frag = s[i:i + 4]
            if frag.isdigit() and frag[:2] in ("19", "20"):
                return int(frag)
    return None


def search(name: str, limit: int = 10) -> list[dict]:
    try:
        data = get_json(API.format(q(name)))
    except Exception:
        return []

    docs = (data.get("data") or {}).get("docinfos") or []
    out = []
    for it in docs[:limit]:
        alb = it.get("albumDocInfo") or {}
        title = alb.get("albumTitle") or it.get("display_name") or ""
        if not title:
            continue

        album_id = alb.get("albumId") or ""
        tv_id = ""
        vdoc = it.get("videoDocInfo") or {}
        if isinstance(vdoc, dict):
            tv_id = vdoc.get("tvId") or vdoc.get("docId") or ""

        if tv_id:
            url = f"https://www.iqiyi.com/v_{tv_id}.html"
        elif album_id:
            url = f"https://www.iqiyi.com/a_{album_id}.html"
        else:
            url = f"https://so.iqiyi.com/so/q_{q(title)}"

        out.append({
            "source": "爱奇艺",
            "title": title,
            "alias": alb.get("albumAlias") or "",
            "year": _year_of(alb),
            "type": alb.get("channel") or alb.get("contentType") or "",
            "url": url,
            "id": str(album_id or tv_id),
            "vip": alb.get("payMark") or alb.get("vipType") or "",
        })
    return out
