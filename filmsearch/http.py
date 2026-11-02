"""统一 HTTP 层：UA、超时、gzip、重试。"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class FetchError(Exception):
    pass


def encode_url(url: str) -> str:
    """把 URL 里非 ASCII 的路径/查询编码掉。

    很多资源站的 m3u8 路径带中文（如 /电影解说/index.m3u8），
    直接把这种 URL 交给 urllib 会抛 UnicodeEncodeError。
    """
    if not url:
        return url
    try:
        url.encode("ascii")
        return url
    except UnicodeEncodeError:
        pass

    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%")
    query = urllib.parse.quote(parts.query, safe="=&%+/:?@!$'()*,;")
    netloc = parts.netloc
    try:
        netloc.encode("ascii")
    except UnicodeEncodeError:
        host = parts.hostname or ""
        netloc = netloc.replace(host, host.encode("idna").decode("ascii"))
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


def get(url: str, headers: dict | None = None, timeout: float = 15.0,
        retries: int = 1) -> bytes:
    hdrs = dict(DEFAULT_HEADERS)
    if headers:
        hdrs.update(headers)

    target = encode_url(url)

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(target, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return raw
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise FetchError(f"{url} -> {last}")


def get_json(url: str, **kw) -> dict:
    raw = get(url, **kw)
    text = raw.decode("utf-8", "replace").strip()
    # 有些接口返回 JSONP
    if text.startswith(("jQuery", "callback(")) and "(" in text:
        text = text[text.index("(") + 1: text.rindex(")")]
    return json.loads(text)


def q(text: str) -> str:
    return urllib.parse.quote(text)
