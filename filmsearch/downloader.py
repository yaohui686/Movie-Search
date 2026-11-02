"""下载：把 m3u8 拉成可播放的本地文件。

两条路：
  A. 有 ffmpeg（bin/ffmpeg.exe 或 PATH 里）→ 直接合并成 mp4，最快最省事
  B. 没有 ffmpeg → 纯 Python 下载所有分片并拼接成 .ts

m3u8 本身不是视频，是一张播放清单；画面在一堆 .ts 分片里。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .http import encode_url

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PROJECT_BIN = Path(__file__).resolve().parent.parent / "bin"


# ---------------- 公共 ----------------


def ffmpeg_path() -> str | None:
    local = PROJECT_BIN / ("ffmpeg.exe" if shutil.which("cmd") else "ffmpeg")
    if local.exists():
        return str(local)
    return shutil.which("ffmpeg")


def has_ffmpeg() -> bool:
    return ffmpeg_path() is not None


def safe_filename(name: str, ext: str = ".mp4") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "video").strip(" .")
    if not s:
        s = "video"
    return s if s.lower().endswith(ext) else s + ext


def _referer(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


def _fetch(url: str, timeout: float = 30.0, retries: int = 2) -> bytes:
    from .http import encode_url

    target = encode_url(url)
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(target, headers={
                "User-Agent": UA,
                "Referer": _referer(url),
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            last = exc
            if i < retries:
                time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"拉取失败: {url} -> {last}")


# ---------------- ffmpeg 路线 ----------------


def build_command(url: str, out: Path, referer: str, ua: str) -> list[str]:
    exe = ffmpeg_path() or "ffmpeg"
    return [
        exe, "-y",
        "-headers", f"Referer: {referer}\r\nUser-Agent: {ua}\r\n",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        str(out),
    ]


def download_with_ffmpeg(url: str, out: Path, referer: str, ua: str) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.call(build_command(url, out, referer, ua))


# ---------------- 直链整文件下载 ----------------


def probe_direct(url: str, ua: str = UA, referer: str = "") -> dict:
    """HEAD/Range 探测直链：拿文件大小与是否支持断点。

    有些 CDN 对 HEAD 返回异常，所以统一用 Range: bytes=0-0 的 GET 探测。
    """
    headers = {"User-Agent": ua, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    target = encode_url(url)
    req = urllib.request.Request(target, headers={**headers, "Range": "bytes=0-0"})
    size, accept = 0, False
    with urllib.request.urlopen(req, timeout=15) as r:
        cr = r.headers.get("Content-Range") or ""
        if cr.startswith("bytes") and "/" in cr:
            size = int(cr.rsplit("/", 1)[1] or 0)
            accept = True
        cl = r.headers.get("Content-Length")
        if not size and cl:
            size = int(cl)
        accept = accept or bool(r.headers.get("Accept-Ranges"))
    return {"size": size, "resume": accept}


def download_direct(url: str, out: Path, ua: str = UA, referer: str = "",
                    workers: int = 0, on_progress=None) -> int:
    """直链单文件下载：分块流式写盘，支持从已有 .part 断点续传。

    产出就是完整视频文件（mp4 等），不经 m3u8 拼接，最快最稳。
    返回 0 成功；文件以 .part 落盘、成功后原子改名，中断不留残缺成品。
    """
    headers = {"User-Agent": ua, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    target = encode_url(url)

    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(out.suffix + ".part")

    done = part.stat().st_size if part.exists() else 0
    total = 0
    last: Exception | None = None

    for attempt in range(3):
        try:
            hdrs = dict(headers)
            if done:
                hdrs["Range"] = f"bytes={done}-"
            req = urllib.request.Request(target, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status}")
                cr = r.headers.get("Content-Range") or ""
                cl = r.headers.get("Content-Length") or "0"
                if r.status == 206 and "/" in cr:
                    total = int(cr.rsplit("/", 1)[1] or 0)
                else:
                    done = 0              # 服务器不支持续传，从头重来
                    total = int(cl or 0)
                mode = "ab" if done else "wb"
                with part.open(mode) as f:
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, max(total, done), "")
            break
        except Exception as exc:
            last = exc
            done = part.stat().st_size if part.exists() else 0
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))

    if total and done < total * 0.999:
        raise RuntimeError(f"直链下载不完整：{done}/{total} 字节（{last}）")
    if not done:
        raise RuntimeError(f"直链拉取失败：{url} -> {last}")

    part.replace(out)
    if on_progress:
        on_progress(total or done, total or done, f"{done/1024/1024:.1f} MB")
    return 0


# ---------------- 纯 Python 路线 ----------------


def parse_m3u8(text: str, base_url: str) -> dict:
    """解析播放清单，取出分片地址、初始化段、加密信息。"""
    segs: list[str] = []
    init_map = ""
    key_uri = ""
    key_method = ""
    encrypted = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-KEY"):
            m = re.search(r'METHOD=([A-Z0-9-]+)', line)
            u = re.search(r'URI="([^"]+)"', line)
            if m and m.group(1).upper() != "NONE":
                key_method = m.group(1).upper()
                if key_method == "AES-128":
                    encrypted = True
                if u:
                    key_uri = urllib.parse.urljoin(base_url, u.group(1))
            continue
        if line.startswith("#EXT-X-MAP"):
            u = re.search(r'URI="([^"]+)"', line)
            if u:
                init_map = urllib.parse.urljoin(base_url, u.group(1))
            continue
        if line.startswith("#"):
            continue
        segs.append(urllib.parse.urljoin(base_url, line))

    return {
        "segments": segs,
        "init": init_map,
        "encrypted": encrypted,
        "key_method": key_method,
        "key_uri": key_uri,
    }


def resolve_playlist(url: str, depth: int = 0) -> tuple[str, str]:
    """把 m3u8 解析到「真正的分片清单」层。

    会遇到三种情况：
      1) 主清单（#EXT-X-STREAM-INF）→ 选最高码率的变体，继续往下
      2) 只有一个条目、但该条目本身又是 m3u8 → 继续往下
      3) 正常分片清单 → 返回
    """
    if depth > 4:
        raise RuntimeError("m3u8 嵌套层数过深，可能是循环引用")

    text = _fetch(url).decode("utf-8", "replace")

    if "#EXT-X-STREAM-INF" in text:
        lines = text.splitlines()
        best_url, best_bw = "", -1
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith("#"):
                    if bw > best_bw:
                        best_bw, best_url = bw, urllib.parse.urljoin(url, s)
                    break
        if best_url:
            return resolve_playlist(best_url, depth + 1)

    info = parse_m3u8(text, url)
    if len(info["segments"]) == 1:
        try:
            head = _fetch(info["segments"][0], timeout=15, retries=0)[:8]
        except Exception:
            head = b""
        if head.startswith(b"#EXTM3U"):
            return resolve_playlist(info["segments"][0], depth + 1)

    return text, url


def download_python(url: str, out: Path, workers: int = 8,
                    on_progress=None, max_segments: int = 0) -> int:
    """纯 Python 下载：拉分片 → 按顺序拼接。

    max_segments > 0 时只下前 N 个分片（用于试跑/抽样，产出的文件是不完整的）。
    输出是 .ts 容器，VLC / PotPlayer / MPV 都能直接播。
    """
    text, final_url = resolve_playlist(url)
    info = parse_m3u8(text, final_url)
    segs = info["segments"]
    if max_segments:
        segs = segs[:max_segments]

    if not segs:
        raise RuntimeError("m3u8 里没有解析到任何分片，可能不是标准 HLS")
    if info["encrypted"]:
        raise RuntimeError(
            f"该资源用 {info['key_method']} 加密，纯 Python 路线解不了。\n"
            "  装个 ffmpeg 就能自动处理：winget install Gyan.FFmpeg"
        )

    total = len(segs)
    if on_progress:
        on_progress(0, total, "开始下载")

    parts: list[bytes | None] = [None] * total
    done = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_map = {pool.submit(_fetch, s, 30.0, 2): i for i, s in enumerate(segs)}
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            try:
                parts[idx] = fut.result()
            except Exception:
                parts[idx] = b""
                failed += 1
            done += 1
            if on_progress and (done % 5 == 0 or done == total):
                on_progress(done, total, f"失败 {failed}" if failed else "")

    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out.open("wb") as f:
        if info["init"]:
            try:
                f.write(_fetch(info["init"]))
            except Exception:
                pass
        for p in parts:
            if p:
                f.write(p)
                written += len(p)

    if on_progress:
        on_progress(total, total, f"完成 {written/1024/1024:.1f} MB")
    return 0 if failed == 0 else 3


def download(url: str, out: Path, workers: int = 8, on_progress=None) -> int:
    """统一入口：有 ffmpeg 走 mp4，没有就走 .ts。"""
    ref = _referer(url)
    if has_ffmpeg():
        out.parent.mkdir(parents=True, exist_ok=True)
        return download_with_ffmpeg(url, out, ref, UA)
    return download_python(url, out.with_suffix(".ts"), workers, on_progress)
