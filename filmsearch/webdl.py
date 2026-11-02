"""本地下载页服务：和播放页同一套思路。

浏览器点链接就下载，不用粘贴迅雷：
  · /dl         下载页，列出每条结果的直链
  · /file?u=    代理下载端点 —— 补 Referer/UA 过防盗链，
                加 Content-Disposition 强制浏览器「另存为」，
                透传 Range 断点续传，大文件流式转发不占内存
  · m3u8 清单   只展示可复制文本（浏览器点开没意义）

用法（由 run.py 的 link 命令自动调用）。
"""
from __future__ import annotations

import html
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PAGE_TMPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>下载直链 — __TITLE__</title>
<style>
  html,body{margin:0;background:#0b0b0f;color:#e6e6e6;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
  #wrap{max-width:960px;margin:0 auto;padding:16px}
  h1{font-size:18px;margin:6px 0 2px}
  .sub{color:#8b8f98;font-size:12px;margin-bottom:14px}
  .topbar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
  .topbar button{background:#2d5af0;color:#fff;border:0;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:13px}
  .topbar button:hover{filter:brightness(1.15)}
  .card{background:#14141c;border:1px solid #262633;border-radius:10px;padding:14px 16px;margin-bottom:14px}
  .card h2{font-size:15px;margin:0 0 4px}
  .meta{color:#8b8f98;font-size:12px;margin-bottom:10px}
  .row{display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid #1e1e2a;flex-wrap:wrap}
  .row:first-of-type{border-top:0}
  .ep{color:#cfd3dc;min-width:86px}
  a.dl{background:#2d5af0;color:#fff;text-decoration:none;border-radius:6px;padding:5px 14px;font-size:13px;white-space:nowrap}
  a.dl:hover{filter:brightness(1.15)}
  a.dl.list{background:#3a3a4a}
  button.cp{background:#1e1e26;color:#cfd3dc;border:1px solid #2c2c38;border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px}
  .url{color:#6f7480;font-size:12px;word-break:break-all;flex:1;min-width:240px}
  .none{color:#8b8f98;font-size:12px}
  #toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);background:#2d5af0;color:#fff;
    padding:8px 18px;border-radius:6px;font-size:13px;display:none}
  .tip{color:#8b8f98;font-size:12px;line-height:1.8;margin-top:18px;border-top:1px solid #262633;padding-top:12px}
</style></head><body>
<div id="wrap">
  <h1>__TITLE__ <span style="color:#8b8f98;font-size:13px">下载直链</span></h1>
  <div class="sub">点「下载」浏览器直接另存为（本地代理已补 Referer，支持断点续传）；或复制链接丢给迅雷/IDM</div>
  <div class="topbar">
    <button onclick="copyAll()">复制全部直链（给迅雷整批粘贴）</button>
  </div>
  __BODY__
  <div class="tip">
    · 「下载」= 浏览器直接下载完整 mp4，可随时暂停再点继续（断点续传）<br>
    · 「复制」= 复制原始直链，粘贴到迅雷新建任务 / IDM<br>
    · 标「清单」的是 m3u8 播放列表，浏览器下不了完整片 —— 关掉本页回终端用
      <b>python run.py download 片名</b>（自动拼接）<br>
    · 画质以 python run.py info 查到的真实分辨率为准，站标 4K 多为虚标
  </div>
</div>
<div id="toast"></div>
<script>
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(()=>t.style.display='none', 1800);
}
function copyText(txt, msg){
  (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject())
    .then(()=>toast(msg || '已复制'))
    .catch(()=>{
      const ta = document.createElement('textarea');
      ta.value = txt; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); toast(msg || '已复制');
    });
}
function copyAll(){
  const urls = [...document.querySelectorAll('a.dl[data-url]')].map(a=>a.dataset.url);
  if (!urls.length) { toast('没有直链可复制'); return; }
  copyText([...new Set(urls)].join('\\r\\n'), '已复制 ' + new Set(urls).size + ' 条直链');
}
</script>
</body></html>"""


def suggest_name(title: str, year: str, ep: str, url: str) -> str:
    """给浏览器另存为的建议文件名。"""
    from .downloader import safe_filename

    path = (url or "").split("?")[0].lower()
    ext = ".mp4"
    for e in (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".rmvb", ".ts", ".m4v"):
        if path.endswith(e):
            ext = e
            break
    return safe_filename(f"{title}({year or ''}) {ep}", ext)


def render_page(items: list[dict]) -> str:
    """items: [{title, year, quality, episodes, sources, links:[{ep,url,kind,tag}]}]"""
    body: list[str] = []
    for it in items:
        meta = "　".join(x for x in (
            it.get("year") or "", it.get("quality") or "",
            f"{it.get('episodes') or 1} 集",
            "来源 " + "/".join(it.get("sources") or []) if it.get("sources") else "",
        ) if x)
        rows: list[str] = []
        n_file = 0
        for lk in it.get("links") or []:
            ep = lk.get("ep") or "全片"
            url = lk.get("url") or ""
            kind = lk.get("kind") or "link"
            if kind in ("file", "magnet", "ed2k", "thunder", "ftp"):
                name = suggest_name(it["title"], it.get("year") or "", ep, url)
                fu = "/file?u=" + urllib.parse.quote(url, safe="") + \
                     "&name=" + urllib.parse.quote(name, safe="")
                if kind == "file":
                    n_file += 1
                    rows.append(
                        f'<div class="row"><span class="ep">{html.escape(ep)}</span>'
                        f'<a class="dl" href="{html.escape(fu, quote=True)}" '
                        f'   data-url="{html.escape(url, quote=True)}">下载</a>'
                        f'<button class="cp" onclick="copyText(this.dataset.u,\'链接已复制\')" '
                        f'        data-u="{html.escape(url, quote=True)}">复制</button>'
                        f'<span class="url">{html.escape(url)}</span></div>')
                else:    # 磁力/电骡等协议链接：复制给专用下载器
                    rows.append(
                        f'<div class="row"><span class="ep">{html.escape(ep)}</span>'
                        f'<button class="cp" onclick="copyText(this.dataset.u,\'链接已复制\')" '
                        f'        data-u="{html.escape(url, quote=True)}">复制{html.escape(lk.get("tag") or kind)}</button>'
                        f'<span class="url">{html.escape(url)}</span></div>')
            else:        # m3u8 / 未知：仅展示
                rows.append(
                    f'<div class="row"><span class="ep">{html.escape(ep)}</span>'
                    f'<button class="cp" onclick="copyText(this.dataset.u,\'清单已复制\')" '
                    f'        data-u="{html.escape(url, quote=True)}">复制{html.escape(lk.get("tag") or "清单")}</button>'
                    f'<span class="url">{html.escape(url)}</span></div>')
        if not n_file:
            rows.append('<div class="none">该条没有整文件直链 —— 回终端用 python run.py download 走清单拼接。</div>')
        body.append(
            f'<div class="card"><h2>{html.escape(it["title"])}</h2>'
            f'<div class="meta">{html.escape(meta)}</div>'
            + "".join(rows) + "</div>")
    if not body:
        body.append('<div class="card"><div class="none">没有搜到结果。</div></div>')

    first = items[0]["title"] if items else "影视"
    return (PAGE_TMPL
            .replace("__TITLE__", html.escape(first))
            .replace("__BODY__", "".join(body)))


class _Handler(BaseHTTPRequestHandler):
    server_version = "FilmDl/1.0"

    def log_message(self, fmt, *args):
        pass

    def _plain(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/file":
            target = (params.get("u") or [""])[0]
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            name = (params.get("name") or ["video.mp4"])[0]
            proxy_file(self, target, name)
            return

        if parsed.path == "/dl":
            page = self.server.page
            self._plain(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        if parsed.path in ("/", "/index.html"):
            self._plain(200, b"FilmDl ok. Use /dl", "text/plain; charset=utf-8")
            return

        self._plain(404, b"not found", "text/plain; charset=utf-8")


def proxy_file(handler, target: str, name: str) -> None:
    """代理下载（防盗链 + attachment + Range 断点透传），供 webdl/webui 共用。

    handler 需要有 send_response / send_header / end_headers / wfile。
    """
    from .http import encode_url

    headers = {
        "User-Agent": UA,
        "Referer": f"{urllib.parse.urlsplit(target).scheme}://"
                   f"{urllib.parse.urlsplit(target).netloc}/",
        "Accept": "*/*",
    }
    rng = handler.headers.get("Range")
    if rng:
        headers["Range"] = rng

    try:
        req = urllib.request.Request(encode_url(target), headers=headers)
        remote = urllib.request.urlopen(req, timeout=30)
    except Exception as exc:
        handler._plain(502, f"上游错误: {exc}".encode("utf-8"),
                       "text/plain; charset=utf-8")
        return

    try:
        with remote:
            handler.send_response(remote.status)
            for h in ("Content-Length", "Content-Range",
                      "Accept-Ranges", "Content-Type"):
                v = remote.headers.get(h)
                if v:
                    handler.send_header(h, v)
            if not remote.headers.get("Content-Type"):
                handler.send_header("Content-Type", "application/octet-stream")
            # RFC 5987：filename* 支持中文；filename 给 ASCII 兜底
            fallback = "".join(c if c.isascii() and c.isalnum() or c in "._- "
                               else "_" for c in name) or "video.mp4"
            handler.send_header(
                "Content-Disposition",
                f'attachment; filename="{fallback}"; '
                f"filename*=UTF-8''{urllib.parse.quote(name)}")
            handler.end_headers()
            try:
                while True:
                    chunk = remote.read(65536)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass     # 用户取消下载/暂停，属正常
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        pass


class DownloadServer:
    """持有搜索结果，起本地服务渲染下载页。"""

    def __init__(self, items: list[dict], port: int = 0):
        self.items = items
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        page = render_page(self.items)

        class Srv(ThreadingHTTPServer):
            pass

        srv = Srv(("127.0.0.1", self.port), _Handler)
        srv.page = page                    # 页面挂在 server 实例上
        self._srv = srv
        self.port = srv.server_address[1]
        self._thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._srv:
            self._srv.shutdown()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/dl"
