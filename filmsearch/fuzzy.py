"""查询清洗与多路变体。

用户输入的片名往往带噪声：
  「流浪地球2 2019」「流浪地球 电影」「流浪地球（国语版）」「流浪地球 1080P」
直接丢给资源站会大幅降低召回率，所以要拆出干净的变体分别去查。
"""
from __future__ import annotations

import re

# 只去掉明确的噪声词，不动「2」「第二季」这类会改变作品身份的标记
_NOISE = (
    "完整版", "未删减", "删减版", "国语版", "粤语版", "中字版", "中文字幕",
    "高清", "超清", "蓝光", "4K", "1080P", "720P", "HD", "BD", "电影",
    "在线观看", "免费", "下载",
)
_YEAR = re.compile(r"(19|20)\d{2}\s*年?")
_BRACKETS = re.compile(r"[（(【\[].*?[）)】\]]")


def clean(query: str) -> str:
    """去掉年份、括号内容、画质/版本噪声词。"""
    s = query or ""
    s = _BRACKETS.sub(" ", s)
    s = _YEAR.sub(" ", s)
    for w in _NOISE:
        s = s.replace(w, " ")
        s = re.sub(re.escape(w), " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -_·:：")
    return s


def variants(query: str, max_n: int = 3) -> list[str]:
    """生成多路召回的查询变体（去重，原词永远排第一）。"""
    out: list[str] = []

    def add(s: str):
        s = (s or "").strip()
        if s and s not in out and len(out) < max_n:
            out.append(s)

    add(query)
    c = clean(query)
    add(c)

    # 还是查不到时，退一步去掉尾部数字（「沙丘2」→「沙丘」）作为最后一路
    if c:
        no_digit = re.sub(r"[0-9０-９]+$", "", c).strip()
        if no_digit and no_digit != c:
            add(no_digit)

    return out
