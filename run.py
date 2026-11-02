"""影视检索 —— 个人自用。

不接参数时自动进入交互模式（双击运行也不会闪退）。

    python run.py                      # 交互模式
    python run.py 流浪地球             # 直接查资源
    python run.py 流浪地球 --official  # 查正版在哪个平台
    python run.py 流浪地球 --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"


def _setup_console() -> None:
    """控制台编码对齐。

    关键坑：Windows 下如果只 chcp 65001 而 Python 仍按 locale(cp936) 读 stdin，
    输入中文会直接 UnicodeDecodeError 崩掉。所以这里主动把 stdio 拉成 UTF-8。
    """
    for stream, kw in ((sys.stdout, {"errors": "replace"}),
                       (sys.stderr, {"errors": "replace"})):
        try:
            stream.reconfigure(encoding="utf-8", **kw)
        except Exception:
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_setup_console()
sys.path.insert(0, str(ROOT))


def safe_input(prompt: str) -> str:
    """读一行输入，任何解码问题都不让程序崩。"""
    try:
        return input(prompt)
    except UnicodeDecodeError:
        # 极端情况下 stdin 编码与终端不一致，退回按字节读
        try:
            raw = sys.stdin.buffer.readline()
            return raw.decode("utf-8", "replace").rstrip("\r\n")
        except Exception:
            return ""
    except (EOFError, KeyboardInterrupt):
        raise


def write_crash(exc: BaseException) -> None:
    try:
        LOGS.mkdir(exist_ok=True)
        with (LOGS / "crash.log").open("a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass

from filmsearch import (douban, iqiyi, youku, matcher, report,  # noqa: E402
                        resource, fuzzy, player, downloader, webdl, webui)

OFFICIAL = {"iqiyi": iqiyi, "youku": youku}


def load_cfg() -> dict:
    p = ROOT / "cfg" / "app.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def log_query(payload: dict) -> None:
    LOGS.mkdir(exist_ok=True)
    with (LOGS / f"query_{time.strftime('%Y%m%d')}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ---------------- 资源检索 ----------------


def _score_rows(rows: list[dict], against: list[str]) -> list[dict]:
    """对每条结果按「所有查询变体」取最高分。

    关键：不能只拿用户原始输入打分。用户打「流浪地球2 2019」时，
    原词与「流浪地球2」是包含关系会被降权，反而把正确结果筛掉。
    """
    for r in rows:
        r["score"] = max(
            matcher.score(r["title"], r.get("year"), q) for q in against
        )
    return rows


def do_resource(name: str, cfg: dict, threshold: float = 0.72, show_progress: bool = True,
                on_progress=None, on_notice=None) -> list[dict]:
    """分阶段检索：先把原词打满所有源，不够再逐级收敛。

    阶段化是为了控制耗时 —— 15 个源 × 3 个变体会变成 45 个请求，一次十几秒。
    on_progress / on_notice 供网页版取进度；show_progress 控制终端打印。
    """
    sources = cfg.get("resource_sources") or []
    queries = fuzzy.variants(name)          # 用于打分（永远包含原词）
    cleaned = fuzzy.clean(name) or name

    def make_cb():
        def cb(done, total, label, count, elapsed):
            if on_progress:
                on_progress(done, total, label, count, elapsed)
            if not show_progress:
                return
            filled = int(12 * done / max(1, total))
            bar = "█" * filled + "░" * (12 - filled)
            print(f"  [{bar}] {done}/{total}  {label:<16} {count:>2} 条  {elapsed:>4}s",
                  flush=True)
        return cb

    def notice(msg: str):
        if on_notice:
            on_notice(msg)
        if show_progress:
            print(f"  {msg}")

    def fetch(recall_queries: list[str], pool: list[dict]) -> list[dict]:
        got = resource.search_multi(pool, recall_queries, progress=make_cb())
        got = _score_rows(got, queries)
        return [r for r in got if r["score"] >= threshold]

    if show_progress and sources:
        print(f"正在检索 {len(sources)} 个资源源…")
    elif on_notice and sources:
        on_notice(f"正在检索 {len(sources)} 个资源源…")

    rows = fetch([name], sources)
    best = max((r["score"] for r in rows), default=0.0)

    # 阶段 2：清洗后的写法（去掉年份/噪声词）
    if best < 0.95 and cleaned and cleaned != name:
        notice(f"再用清洗后的写法「{cleaned}」补一轮…")
        rows += fetch([cleaned], sources)
        best = max((r["score"] for r in rows), default=0.0)

    # 阶段 3：宽召回（只打前 6 个源，控制耗时）
    if best < 0.95 and len(cleaned) >= 3:
        wide = cleaned[:2]
        notice(f"匹配仍不确定，宽召回「{wide}」（限前 6 源）…")
        rows += fetch([wide], sources[:6])

    # 去重
    uniq, seen = [], set()
    for r in rows:
        k = (r["title"], r["year"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    rows = uniq

    for r in rows:
        t = r["title"]
        r["is_extra"] = any(k in t for k in ("解说", "预告", "花絮", "片花", "特辑"))

    # 排序：正片在前 → 画质高在前 → m3u8 直链优先 → 有下载直链优先 → 相关度高 → 线路多
    rows.sort(key=lambda r: (
        r["is_extra"],
        resource.QUALITY_RANK.get(r.get("quality"), 99),
        0 if r["m3u8"] else 1,
        0 if r.get("direct_down") else 1,
        -r["score"],
        -r["lines"],
    ))
    return rows


# ---------------- 正版可用性 ----------------


def pick_douban(name: str) -> dict | None:
    cands = douban.search(name)
    if not cands:
        import re as _re
        alt = _re.sub(r"[0-9]+$", "", name).strip()
        if alt and alt != name:
            cands = douban.search(alt)
    if not cands:
        return None
    best, best_score = None, 0.0
    for c in cands:
        s = matcher.score(c["title"], c.get("year"), name)
        if c.get("sub_title"):
            s = max(s, matcher.score(c["sub_title"], c.get("year"), name))
        if s > best_score:
            best, best_score = c, s
    if best:
        best["_score"] = round(best_score, 3)
    return best


def check_platform(mod, name: str, hit: dict | None, threshold: float) -> dict:
    results = mod.search(name)
    base_title = hit["title"] if hit else name
    base_year = hit.get("year") if hit else None

    best = None
    for r in results:
        s = matcher.score(r["title"], r.get("year"), base_title, base_year,
                          aliases=(r.get("alias") or "",))
        if best is None or s > best[0]:
            best = (s, r)
    if not best:
        return {"source": mod.__name__.split(".")[-1], "matched": False, "score": None}

    s, r = best
    external = bool(r.get("external"))
    note = "站外跳转" if external else (r.get("vip") or "")
    if any(k in note for k in ("预告", "花絮", "片花", "片段", "解说")):
        s = min(s, threshold - 0.01)
        note = "仅预告/花絮"
    elif external:
        s = min(s, threshold - 0.01)

    return {
        "source": r["source"],
        "matched": s >= threshold,
        "title": r["title"],
        "year": r.get("year"),
        "score": s,
        "url": r.get("url", ""),
        "note": note,
    }


def do_official(name: str, cfg: dict, threshold: float):
    hit = pick_douban(name)
    rows = []
    for mod in OFFICIAL.values():
        rows.append(check_platform(mod, name, hit, threshold))
        time.sleep(0.3)
    return hit, rows


# ---------------- 入口 ----------------


def run_once(name: str, cfg: dict, official: bool, as_json: bool, threshold: float) -> None:
    name = name.strip()
    if not name:
        return
    t0 = time.time()

    try:
        if official:
            hit, rows = do_official(name, cfg, threshold)
            if as_json:
                print(report.to_json(name, {"douban": hit, "platforms": rows}))
            else:
                report.print_table(name, hit, rows)
            log_query({"mode": "official", "query": name, "douban": hit, "platforms": rows})
        else:
            rows = do_resource(name, cfg)
            if as_json:
                print(report.to_json(name, {"results": rows}))
            else:
                report.print_resources(name, rows)
            log_query({"mode": "resource", "query": name, "count": len(rows)})
    except Exception as exc:  # 交互模式下不要因为一次异常就退出
        print(f"[!] 查询出错：{type(exc).__name__}: {exc}")
        traceback.print_exc()

    if not as_json:
        print(f"耗时 {round(time.time() - t0, 1)}s\n")


# 交互模式支持的命令词（全词和单字母都认，中英文别名都收）
CMD_ALIASES = {
    "l": "link", "link": "link", "链接": "link",
    "d": "download", "down": "download", "download": "download", "下载": "download",
    "p": "play", "play": "play", "播放": "play",
    "o": "official", "official": "official", "正版": "official",
    "info": "info", "画质": "info",
    "web": "web", "w": "web", "网页": "web",
}


def interactive(cfg: dict) -> int:
    print("=" * 60)
    print("  影视资源检索　（命令行模式）")
    print("=" * 60)
    print("  直接输入片名回车即可检索")
    print("  web　　　 切换到网页下载台（推荐）")
    print("  link 片名　下载页（浏览器点一下就下，例：link 流浪地球）")
    print("  p 片名　　 直接播放第一条（例：p 流浪地球）")
    print("  d 片名　　 下载第一条，直链优先（例：d 流浪地球）")
    print("  o 片名　　 查正版在哪个平台（例：o 流浪地球）")
    print("  info 片名　查真实画质（例：info 泰坦尼克号）")
    print("  q　　　　　退出")
    print("  （单字母 l / d / p / o 同样有效；带参数如 link 片名 -n 2 也行）")
    print("-" * 60)

    official, as_json, threshold = False, False, 0.75

    def dispatch(raw: str) -> None:
        """把一行输入路由到对应动作；不认识就当片名搜。"""
        m = re.match(r"(?:python(?:3)?|py)?\s*run\.py\s+(.+)$", raw, re.I)
        if m:                                   # 粘进来整条命令也认
            tokens = m.group(1).split()
            print(f"(识别为命令，按 run.py {' '.join(tokens)} 执行)")
            try:
                main(tokens)
            except SystemExit:
                pass
            return

        parts = raw.split(None, 1)
        if raw.lower() in ("web", "w", "网页"):      # 无参数命令：开网页
            cmd_web()
            return
        canon = CMD_ALIASES.get(parts[0].lower()) if parts else None
        if canon and len(parts) == 2 and parts[1].strip():
            arg = parts[1].strip()
            if canon == "link":
                tokens = arg.split()
                name = tokens[0] if tokens and not tokens[0].startswith("-") else arg
                if len(tokens) > 1 or (tokens and tokens[0].startswith("-")):
                    print(f"(按命令执行: link {' '.join(tokens)})")
                    try:
                        main(["link", *tokens])
                    except SystemExit:
                        pass
                else:
                    cmd_link(name, cfg, limit=10)
            elif canon == "download":
                tokens = arg.split()
                if len(tokens) > 1 or (tokens and tokens[0].startswith("-")):
                    print(f"(按命令执行: download {' '.join(tokens)})")
                    try:
                        main(["download", *tokens])
                    except SystemExit:
                        pass
                else:
                    cmd_download(arg, cfg, 1)
            elif canon == "play":
                cmd_play(arg, cfg, 1)
            elif canon == "official":
                run_once(arg, cfg, True, False, threshold)
            elif canon == "web":
                cmd_web()
            elif canon == "info":
                tokens = arg.split()
                if len(tokens) > 1 or (tokens and tokens[0].startswith("-")):
                    print(f"(按命令执行: info {' '.join(tokens)})")
                    try:
                        main(["info", *tokens])
                    except SystemExit:
                        pass
                else:
                    cmd_info(arg, cfg, 5)
            return
        if canon:                               # 只敲了命令没给片名
            print(f"命令 {parts[0]} 后面要跟片名，例：{parts[0]} 流浪地球")
            return
        run_once(raw, cfg, False, False, threshold)

    while True:
        try:
            raw = safe_input("片名> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if not raw:
            continue
        if raw.lower() in ("q", "quit", "exit"):
            print("再见。")
            return 0

        try:
            dispatch(raw)
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        except Exception as exc:      # 单次查询失败不能带崩整个程序
            write_crash(exc)
            print(f"[!] 出错了：{type(exc).__name__}: {exc}")
            print("    详情已写入 logs\\crash.log，可以继续输入下一个片名。\n")


def _pick(rows: list[dict], index: int) -> dict | None:
    if not rows:
        return None
    return rows[max(0, min(index - 1, len(rows) - 1))]


def _parse_episodes(spec: str, total: int) -> list[int]:
    """把「1-3,5」解析成 1-based 序号列表（越界忽略）；空串 = 全部。"""
    spec = (spec or "").strip().lower()
    if not spec or spec in ("all", "全部"):
        return list(range(1, total + 1))
    picked: list[int] = []
    for part in re.split(r"[,，\s]+", spec):
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            picked += [i for i in range(lo, hi + 1) if 1 <= i <= total]
        elif part.isdigit() and 1 <= int(part) <= total:
            picked.append(int(part))
    return sorted(set(picked))


def _ep_key(ep: str) -> int:
    """集名里的第一个数字，用于自然排序（无数字排最后）。"""
    m = re.search(r"\d+", ep or "")
    return int(m.group()) if m else 9999


def _select_down(row: dict, episodes: str = "") -> list[tuple[str, str, str]]:
    """挑出一条结果的链接：直链文件优先，其次每集播放清单；按集数筛选。

    剧集没有 mp4 直链时，靠 play（每集 m3u8）实现按集下载。
    """
    links: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for ep, u, k in row.get("down") or []:
        if u not in seen:
            links.append((ep, u, k))
            seen.add(u)
    for ep, u in row.get("play") or []:
        if u in seen:
            continue
        seen.add(u)
        kind = "m3u8" if u.split("?")[0].lower().endswith(".m3u8") else "link"
        links.append((ep, u, kind))
    if not links:
        links = [("全片", row.get("url") or "", "m3u8" if row.get("m3u8") else "link")]
    ranked = sorted(links, key=lambda x: (_ep_key(x[0]), 0 if x[2] == "file" else 1))
    idx = _parse_episodes(episodes, len(ranked))
    return [ranked[i - 1] for i in idx]


def cmd_play(name: str, cfg: dict, index: int = 1) -> int:
    """本地起代理播放器，绕过防盗链 + 用 hls.js 解码。"""
    rows = do_resource(name, cfg)
    r = _pick(rows, index)
    if not r:
        print("没有搜到可播放的资源。")
        return 1
    if not r.get("m3u8"):
        print(f"第 {index} 条不是 m3u8 直链，播放器播不了：{r.get('url')}")
        return 1

    srv = player.PlayServer(0)
    port = srv.start()
    url = srv.url_for(r["url"])

    print()
    print(f"▶ {r['title']} ({r.get('year') or '—'})　{r.get('quality')}　共 {r.get('episodes')} 集")
    print(f"  播放页：{url}")
    print(f"  直链：  {r['url']}")
    print()
    print("已在浏览器打开。播放服务随窗口关闭而结束（按 Ctrl+C 或关掉窗口）。")

    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass

    try:
        safe_input("按回车停止播放服务…")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        srv.stop()
    return 0


def cmd_web(port: int = 0) -> int:
    """网页下载台：浏览器里搜片、看直链、点下载、直接播放。"""
    srv = webui.WebUIServer(do_resource, _real_resolution, load_cfg(), port)
    srv.start()
    page = srv.url()
    print("=" * 60)
    print("  影视检索 · 网页下载台")
    print("=" * 60)
    print(f"  已打开：{page}")
    print("  · 搜索框输片名 → 结果卡片点「下载」直接另存为")
    print("  · 「复制全部直链」整批给迅雷；「播放」直接看")
    print("  · 关闭方式：回到本窗口按 Ctrl+C 或回车")
    print("=" * 60)
    try:
        import webbrowser
        webbrowser.open(page)
    except Exception:
        pass

    try:
        safe_input("\n按回车（或 Ctrl+C）停止网页服务…")
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        srv.stop()
    print("网页服务已停止。")
    return 0


def _down_progress(label: str):
    def on_progress(done, total, note):
        pct = done / total * 100 if total else 0
        bar_len = 20
        filled = int(bar_len * min(done, total) / max(1, total))
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {done/1048576:8.1f}/{total/1048576:.1f} MB"
              f"  {pct:5.1f}%  {label}{('　' + note) if note else ''}",
              end="", flush=True)
        if total and done >= total:
            print()
    return on_progress


def _ext_of(url: str, default: str = ".mp4") -> str:
    path = (url or "").split("?")[0].lower()
    for ext in (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".rmvb", ".ts", ".m4v"):
        if path.endswith(ext):
            return ext
    return default


def _download_direct_links(r: dict, picks: list, outdir: str, dry: bool) -> int:
    """逐个下载直链文件；--dry 只列计划。"""
    out_dir = Path(outdir or ".")
    year = r.get("year") or ""
    rc = 0
    for ep, u, k in picks:
        out = out_dir / downloader.safe_filename(
            f"{r['title']}({year}) {ep}", _ext_of(u)
        )
        size_note = ""
        if not dry:
            try:
                info = downloader.probe_direct(u, player.UA,
                                               player._referer_for(u))
                if info["size"]:
                    size_note = f"{info['size']/1048576:.0f} MB" + \
                                ("　支持断点续传" if info["resume"] else "")
            except Exception:
                pass
        print(f"\n▶ {r['title']}　{ep}　[{LINK_TAG.get(k, k)}]"
              + (f"　约 {size_note}" if size_note else ""))
        print(f"  {u}")
        print(f"  输出：{out}"
              + ("　（已存在 .part，将断点续传）" if out.with_suffix(out.suffix + '.part').exists() else ""))
        if dry:
            continue
        try:
            downloader.download_direct(u, out, player.UA, player._referer_for(u),
                                       on_progress=_down_progress(ep))
            print(f"\n✅ 完成：{out}　{out.stat().st_size/1024/1024:.1f} MB")
        except KeyboardInterrupt:
            print("\n已中断；.part 已保留，重新执行同一命令即可断点续传。")
            return 130
        except Exception as exc:
            print(f"\n[!] 直链下载失败：{type(exc).__name__}: {exc}")
            print("    回退用清单路线：python run.py download " + r["title"]
                  + " -n 1 ——（该条无直链时会自动走 m3u8 拼接）")
            rc = rc or 2
    return rc


def cmd_download(name: str, cfg: dict, index: int = 1, outdir: str = "",
                 run: bool = True, episodes: str = "") -> int:
    """下载第 index 条：优先整文件直链（mp4），没有才走 m3u8 两条老路。"""
    rows = do_resource(name, cfg)
    r = _pick(rows, index)
    if not r:
        print("没有搜到可下载的资源。")
        return 1

    down = r.get("down") or []
    direct = [(ep, u, k) for ep, u, k in down if k == "file"]
    if direct:
        picks = _select_down(r, episodes)
        if not run:
            print("\n--dry 计划：")
            return _download_direct_links(r, picks, outdir, dry=True)
        return _download_direct_links(r, picks, outdir, dry=False)

    # ---- 以下是没有整文件直链时的路线：逐集下载播放清单 ----
    use_ffmpeg = downloader.has_ffmpeg()
    ext = ".mp4" if use_ffmpeg else ".ts"
    picks = [p for p in _select_down(r, episodes) if p[2] in ("m3u8", "link")]
    year = r.get("year") or ""

    if not picks:
        picks = [("全片", r.get("url") or "", "link")]
    if not picks[0][1]:
        print("没有搜到可下载的链接。")
        return 1

    mode_txt = "ffmpeg 合并 mp4" if use_ffmpeg else "纯 Python 拼 .ts（未装 ffmpeg）"
    print()
    print(f"▶ {r['title']} ({year or '—'})　{r.get('quality')}　"
          f"{r.get('episodes')} 集　清单 {len(picks)} 项　{mode_txt}")
    print("  说明：该源没有整文件直链，下载播放清单再拼接；迅雷也可以直接下这些 m3u8。")
    if not run:
        for ep, u, _k in picks:
            out = Path(outdir or ".") / downloader.safe_filename(
                f"{r['title']}({year}) {ep}", ext)
            print(f"  ▸ {ep}　{u}")
            print(f"    输出：{out}")
        return 0

    last = [0.0]

    def on_progress(done, total, note):
        pct = done / total * 100 if total else 0
        bar_len = 20
        filled = int(bar_len * done / max(1, total))
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {done}/{total}  {pct:5.1f}%  {note}", end="", flush=True)
        if done >= total:
            print()

    rc = 0
    try:
        for ep, u, _k in picks:
            out = Path(outdir or ".") / downloader.safe_filename(
                f"{r['title']}({year}) {ep}", ext)
            print(f"\n  ▸ {ep}　{u}")
            print(f"    输出：{out}")
            if use_ffmpeg:
                item_rc = downloader.download_with_ffmpeg(
                    u, out, player._referer_for(u), player.UA
                )
                if item_rc == 0 and out.exists():
                    print(f"\n    ✅ 完成：{out.name}　{out.stat().st_size/1024/1024:.1f} MB")
                else:
                    print(f"\n    ❌ ffmpeg 返回 {item_rc}")
                    rc = rc or item_rc
            else:
                item_rc = downloader.download_python(u, out, on_progress=on_progress)
                if item_rc == 0 and out.exists():
                    print(f"    ✅ 完成：{out.name}　{out.stat().st_size/1024/1024:.1f} MB")
                else:
                    print(f"    ⚠️ 部分分片失败（返回 {item_rc}）：{out.name}")
                    rc = rc or item_rc
        if rc == 0 and use_ffmpeg is False:
            print("\n提示：.ts 可以直接用 VLC / PotPlayer 播放；想转 mp4 装个 ffmpeg 即可。")
        return rc
    except KeyboardInterrupt:
        print("\n已中断。可用 --dry 先看命令。")
        return 130
    except Exception as exc:
        print(f"\n[!] 下载失败：{type(exc).__name__}: {exc}")
        return 2


def _real_resolution(url: str, hops: int = 4) -> str:
    """顺着主清单往下钻，返回真实分辨率/码率。

    资源站标的「4K」基本不可信，所以要看 m3u8 里到底写了什么。
    """
    from filmsearch.http import encode_url
    import re as _re
    import urllib.request

    target = encode_url(url)
    resolutions: list[str] = []
    for _ in range(hops):
        try:
            req = urllib.request.Request(target, headers={"User-Agent": player.UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception:
            return "拉取失败"

        if "#EXT-X-STREAM-INF" not in text:
            break

        lines = text.splitlines()
        nxt = None
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            res = _re.search(r"RESOLUTION=(\d+x\d+)", line)
            bw = _re.search(r"BANDWIDTH=(\d+)", line)
            if res:
                kbps = int(bw.group(1)) // 1000 if bw else 0
                resolutions.append(f"{res.group(1)}@{kbps}k" if kbps else res.group(1))
            for n in lines[i + 1:]:
                s = n.strip()
                if s and not s.startswith("#"):
                    import urllib.parse as _up
                    nxt = _up.urljoin(target, s)
                    break
            break
        if not nxt:
            break
        target = encode_url(nxt)

    if resolutions:
        return " / ".join(resolutions)
    return "单码率（无画质可选）"


LINK_TAG = {
    "file": "直链文件",
    "m3u8": "播放清单",
    "magnet": "磁力",
    "ed2k": "电骡",
    "thunder": "迅雷专链",
    "ftp": "FTP",
    "link": "链接",
    "other": "链接",
}


def cmd_link(name: str, cfg: dict, limit: int = 5, index: int = 0,
             episodes: str = "", outdir: str = ".",
             to_txt: bool = False, to_clip: bool = False) -> int:
    """输出下载直链：能复制进迅雷/IDM/浏览器另存为的完整文件链接。

    量子等源会给出每集一个 mp4 直链（vod_down_url），这类链接才是
    真正的「下载链接」；没给直链的源退回 m3u8 播放清单。
    """
    rows = do_resource(name, cfg)
    if not rows:
        print("没有搜到。")
        return 1

    picked = rows if not index else [_pick(rows, index)]
    picked = [r for r in picked if r][: max(1, limit)]

    all_links: list[str] = []
    print()
    print("=" * 100)
    for i, r in enumerate(picked, 1 if not index else index):
        links = _select_down(r, episodes)
        n_down = len([1 for _, _, k in (r.get("down") or []) if k == "file"])
        head = "有直链" if n_down else "无直链（仅播放清单）"
        srcs = "/".join(r.get("sources") or [r.get("source", "?")])
        print(f"[{i}] {r['title']} ({r.get('year') or '—'})　{r.get('quality')}"
              f"　{r.get('episodes')} 集　{head}　[{srcs}]")
        for ep, u, k in links:
            tag = LINK_TAG.get(k, "链接")
            print(f"    [{tag}] {ep}　{u}")
            if k in ("file", "magnet", "ed2k", "thunder", "ftp"):
                all_links.append(u)
        if not any(k != "m3u8" for _, _, k in links) and r.get("url"):
            all_links.append(r["url"])       # 只有清单也要能复制走
        print()
    print("=" * 100)

    if to_txt or to_clip:
        txt_path = (Path(outdir or ".") /
                    downloader.safe_filename(f"下载链接_{picked[0]['title']}", ".txt"))
        txt_path.write_text("\r\n".join(all_links) + "\r\n", encoding="utf-8-sig")
        print(f"已写入 {len(all_links)} 条链接 → {txt_path}")
        if to_clip:
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Set-Clipboard -Value ([System.IO.File]::ReadAllText("
                     f"'{txt_path}', [System.Text.Encoding]::UTF8))"],
                    check=False, timeout=15,
                )
                print("已复制到剪贴板（迅雷新建任务可直接批量粘贴）。")
            except Exception as exc:
                print(f"[!] 剪贴板失败：{exc}；手动打开 txt 全选复制即可")
        print()

    print("用法：")
    print("  · 直链文件：迅雷新建任务粘贴 / IDM / 浏览器直接另存为（就是完整 mp4，不是清单）")
    print("  · 电视剧加 -e 选集：-e 3、-e 1-5、-e 1-3,7；不加 = 全部")
    print("  · --txt 存成文件、--txt --clip 顺带复制进剪贴板，可整批丢给迅雷")
    print("  · 只有「播放清单」的源：迅雷可试边下边播，稳妥用 python run.py download")
    print(f"  · 先看真实画质：python run.py info {name}")
    print()

    # 本地下载页：浏览器点「下载」直接另存为（代理补 Referer + 断点续传）
    items = []
    for r in picked:
        links = [{"ep": ep, "url": u, "kind": k, "tag": LINK_TAG.get(k, k)}
                 for ep, u, k in _select_down(r, episodes)]
        items.append({
            "title": r["title"], "year": r.get("year") or "",
            "quality": r.get("quality") or "", "episodes": r.get("episodes"),
            "sources": r.get("sources") or [r.get("source", "?")],
            "links": links,
        })
    srv = webdl.DownloadServer(items)
    srv.start()
    page = srv.url()
    print(f"下载页已在本地打开：{page}")
    print("  点「下载」= 浏览器直接另存为；点「复制全部直链」= 批量给迅雷。")
    print("  （关闭方式：回到本窗口按回车，服务随之结束）")
    try:
        import webbrowser
        webbrowser.open(page)
    except Exception:
        pass

    try:
        safe_input("\n按回车停止下载服务…")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        srv.stop()
    return 0


def cmd_info(name: str, cfg: dict, limit: int = 5) -> int:
    """查真实分辨率——资源站标的画质经常虚高。"""
    rows = do_resource(name, cfg)
    if not rows:
        print("没有搜到。")
        return 1

    print()
    print(f"{'#':<4}{'片名':<26}{'年份':<7}{'标签':<8}{'真实分辨率':<28}")
    print("-" * 78)
    for i, r in enumerate(rows[:limit], 1):
        real = _real_resolution(r["url"]) if r.get("m3u8") else "非m3u8"
        print(f"{i:<4}{(r['title'] or '')[:24]:<26}{str(r.get('year') or '—'):<7}"
              f"{str(r.get('quality') or '—'):<8}{real:<28}")
    print("-" * 78)
    print("说明：「标签」来自资源站自己的标注，虚标很常见；「真实分辨率」是 m3u8 里写死的。")
    print()
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 网页下载台（无参数的默认入口）
    if argv and argv[0] == "web":
        sp = argparse.ArgumentParser(prog="run.py web")
        sp.add_argument("--port", type=int, default=0, help="固定端口（默认随机）")
        sa = sp.parse_args(argv[1:])
        return cmd_web(sa.port)
    if argv and argv[0] == "cli":
        return interactive(load_cfg())

    # 子命令：play / download / link / info
    if argv and argv[0] in ("play", "download", "link", "info"):
        sub, rest = argv[0], argv[1:]
        sp = argparse.ArgumentParser(prog=f"run.py {sub}")
        sp.add_argument("name", nargs="+", help="片名")
        sp.add_argument("-n", "--index", type=int, default=0,
                        help="第几条结果（download/link/info 用）")
        sp.add_argument("-e", "--episodes", default="",
                        help="选集：3、1-5、1-3,7；不加 = 全部（download/link 用）")
        sp.add_argument("-o", "--outdir", default=".", help="输出目录")
        sp.add_argument("--txt", action="store_true", help="link：把链接存成 txt")
        sp.add_argument("--clip", action="store_true", help="link：顺带复制进剪贴板")
        sp.add_argument("--dry", action="store_true", help="只打印命令不执行")
        sp.add_argument("-l", "--limit", type=int, default=5, help="显示条数")
        sa = sp.parse_args(rest)
        cfg = load_cfg()
        title = " ".join(sa.name)
        if sub == "play":
            return cmd_play(title, cfg, sa.index or 1)
        if sub == "download":
            return cmd_download(title, cfg, sa.index or 1, sa.outdir,
                                run=not sa.dry, episodes=sa.episodes)
        if sub == "link":
            return cmd_link(title, cfg, sa.limit, sa.index, sa.episodes,
                            sa.outdir, sa.txt, sa.clip)
        return cmd_info(title, cfg, sa.limit)

    ap = argparse.ArgumentParser(description="影视资源检索（个人自用）")
    ap.add_argument("name", nargs="*", help="片名；不给则打开网页下载台")
    ap.add_argument("--official", action="store_true", help="查正版在哪个平台")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--play", action="store_true", help="搜完直接播放第一条")
    ap.add_argument("--threshold", type=float, default=0.75)
    args = ap.parse_args(argv)

    cfg = load_cfg()

    if not args.name:
        return cmd_web()

    if args.play:
        return cmd_play(" ".join(args.name), cfg, 1)

    run_once(" ".join(args.name), cfg, args.official, args.json, args.threshold)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:          # 连 KeyboardInterrupt 之外的都要留痕
        write_crash(exc)
        print("\n[!] 程序异常：")
        traceback.print_exc()
        print("\n详情已写入 logs\\crash.log")
        try:
            safe_input("\n按回车退出…")
        except Exception:
            pass
        raise SystemExit(1)
