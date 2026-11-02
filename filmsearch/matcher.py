"""片名归一化与匹配打分。

不同平台对同一部片的叫法差别很大：
  豆瓣「流浪地球」/ 爱奇艺「流浪地球2」/ 优酷「流浪地球 第1季」
所以必须做归一化 + 打分，不能靠字符串相等。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_STRIP = re.compile(r"[《》〈〉\"'“”‘’\s\-—_:：·•,，.。!！?？()（）\[\]【】]")
_YEAR = re.compile(r"(19|20)(\d{2})")


def normalize(title: str) -> str:
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title)
    t = _STRIP.sub("", t)
    return t.lower()


def extract_year(*texts) -> int | None:
    for t in texts:
        if not t:
            continue
        if isinstance(t, int):
            return t
        m = _YEAR.search(str(t))
        if m:
            return int(m.group(0))
    return None


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    base = SequenceMatcher(None, na, nb).ratio()
    # 包含关系（「繁花」vs「双面繁花」、「流浪地球」vs「流浪地球2」）要降权，
    # 否则「双面繁花」这种毫不相干的片子会被当成命中。
    if na in nb or nb in na:
        extra = (nb.replace(na, "", 1) if na in nb else na.replace(nb, "", 1))
        # 多出来的部分是英文别名（「奥本海默 Oppenheimer」）→ 视为同一部
        alpha = sum(1 for c in extra if c.isascii() and c.isalpha())
        if alpha >= 3:
            base = max(base, 0.95)
        else:
            base *= max(0.45, 1.0 - 0.15 * len(extra))
    return base


def _lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度。用于容忍错别字（流浪地求 vs 流浪地球）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur.append(prev[j - 1] + 1)
            else:
                cur.append(max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def fuzzy_ratio(query: str, title: str) -> float:
    """模糊匹配分：相似度 + 错别字容忍。

    规则：
      - 存在包含关系（繁花 / 双面繁花、流浪地球 / 流浪地球2）时，只用降权后的相似度，
        不做 LCS 加成，避免把续集和无关片当成命中。
      - 否则按 LCS 覆盖率给分：查询至少 3 个字，且 60% 以上字符按顺序出现在片名里。
    """
    na, nb = normalize(query), normalize(title)
    if not na or not nb:
        return 0.0

    base = similarity(query, title)

    if na in nb or nb in na:
        return base

    if len(na) < 3:
        return base

    cover = _lcs_len(na, nb) / len(na)
    if cover >= 0.6:
        return max(base, 0.6 + 0.4 * cover)
    return max(base, cover * 0.5)


def score(candidate_title: str, candidate_year, query_title: str,
          query_year=None, aliases=()) -> float:
    """综合片名相似度与年份给出 0~1 的分。"""
    best = fuzzy_ratio(query_title, candidate_title)
    for a in aliases:
        if a:
            best = max(best, fuzzy_ratio(query_title, a))

    if query_year and candidate_year:
        gap = abs(int(query_year) - int(candidate_year))
        if gap == 0:
            best = min(1.0, best + 0.15)
        elif gap <= 1:
            best = min(1.0, best + 0.05)
        else:
            best *= 0.75
    return round(best, 3)
