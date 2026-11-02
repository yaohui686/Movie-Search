"""本地播放服务：解决 m3u8 的两个拦路虎。

问题 1：CDN 防盗链
    多数资源站的 m3u8/ts 会校验 Referer，浏览器直接拉会被 403。
    解法：本地起一个代理，转发时补上正确的 Referer 和 UA。

问题 2：Chrome 不支持原生 HLS
    解法：播放页用 hls.js 解码。

用法（由 run.py 自动调用，也可单独跑）：
    python -m filmsearch.player
"""
from __future__ import annotations

import re
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PLAYER_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>播放中</title>
<style>
  html,body{margin:0;background:#0b0b0f;color:#e6e6e6;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}
  #wrap{max-width:1100px;margin:0 auto;padding:12px}
  video{width:100%;max-height:78vh;background:#000;border-radius:8px}
  .bar{margin-top:10px;display:flex;gap:10px;flex-wrap:wrap}
  .bar a,.bar button{background:#1e1e26;color:#cfd3dc;border:1px solid #2c2c38;border-radius:6px;
    padding:6px 12px;text-decoration:none;cursor:pointer;font-size:13px}
  .bar a:hover,.bar button:hover{background:#2a2a36}
  #tip{margin-top:8px;color:#8b8f98;font-size:12px;word-break:break-all}
  #err{color:#ff8080;margin-top:8px}
</style></head><body>
<div id="wrap">
  <video id="v" controls autoplay playsinline></video>
  <div class="bar">
    <button onclick="document.getElementById('v').play()">播放</button>
    <a href="__RAW__" target="_blank">原始 m3u8</a>
    <a href="__DL__" target="_blank">下载命令</a>
  </div>
  <div id="tip">__URL__</div>
  <div id="err"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js"></script>
<script>
const src = "__PROXY__";
const v = document.getElementById('v');
const err = document.getElementById('err');
if (window.Hls && Hls.isSupported()) {
  const h = new Hls({maxBufferLength: 30});
  h.loadSource(src);
  h.attachMedia(v);
  h.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) err.textContent = '播放出错：' + d.type + ' / ' + d.details; });
} else if (v.canPlayType('application/vnd.apple.mpegurl')) {
  v.src = src;
} else {
  err.textContent = '当前浏览器不支持 HLS，请换个浏览器或用 PotPlayer/VLC 打开原始 m3u8。';
}
</script>
</body></html>"""


def _referer_for(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


def _fetch(url: str, timeout: float = 20.0) -> tuple[int, dict, bytes]:
    """带 Referer/UA 抓上游资源。"""
    from .http import encode_url

    req = urllib.request.Request(encode_url(url), headers={
        "User-Agent": UA,
        "Referer": _referer_for(url),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def rewrite_m3u8(text: str, base_url: str, prefix: str = "/proxy?u=") -> str:
    """把 m3u8 里的分片地址改写成经过本地代理，顺带处理加密 KEY。"""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            out.append(raw)
            continue

        if line.startswith("#"):
            if 'URI="' in line:
                def repl(m):
                    absu = urllib.parse.urljoin(base_url, m.group(1))
                    return 'URI="%s%s"' % (prefix, urllib.parse.quote(absu, safe=""))
                line = re.sub(r'URI="([^"]+)"', repl, line)
            out.append(line)
            continue

        absu = urllib.parse.urljoin(base_url, line)
        out.append(prefix + urllib.parse.quote(absu, safe=""))

    return "\n".join(out)


class _Handler(BaseHTTPRequestHandler):
    server_version = "FilmPlay/1.0"

    def log_message(self, fmt, *args):
        pass

    def _plain(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        target = (params.get("u") or [""])[0]

        if parsed.path == "/play":
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            proxy = "/proxy?u=" + urllib.parse.quote(target, safe="")
            html = (PLAYER_HTML
                    .replace("__PROXY__", proxy)
                    .replace("__RAW__", target)
                    .replace("__DL__", "/dl?u=" + urllib.parse.quote(target, safe=""))
                    .replace("__URL__", target))
            self._plain(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if parsed.path == "/dl":
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            name = (params.get("name") or ["video"])[0]
            cmd = build_ffmpeg_command(target, name + ".mp4")
            self._plain(200, cmd.encode("utf-8"), "text/plain; charset=utf-8")
            return

        if parsed.path == "/proxy":
            if not target:
                self._plain(400, b"missing u", "text/plain; charset=utf-8")
                return
            try:
                status, headers, body = _fetch(target)
            except Exception as exc:
                self._plain(502, f"上游错误: {exc}".encode("utf-8"),
                            "text/plain; charset=utf-8")
                return

            ctype = headers.get("Content-Type", "")
            is_m3u8 = ("mpegurl" in ctype.lower()) or target.split("?")[0].endswith(".m3u8")

            if is_m3u8:
                text = body.decode("utf-8", "replace")
                text = rewrite_m3u8(text, target)
                self._plain(200, text.encode("utf-8"),
                            "application/vnd.apple.mpegurl")
            else:
                self._plain(200, body, ctype or "application/octet-stream")
            return

        if parsed.path in ("/", "/index.html"):
            self._plain(200, b"FilmPlay ok. Use /play?u=<encoded m3u8 url>",
                        "text/plain; charset=utf-8")
            return

        self._plain(404, b"not found", "text/plain; charset=utf-8")


def build_ffmpeg_command(url: str, out_name: str) -> str:
    """生成可直接粘贴执行的 ffmpeg 下载命令（含防盗链头）。"""
    ref = _referer_for(url)
    safe_out = out_name.replace('"', "")
    return (
        f'ffmpeg -y -headers "Referer: {ref}\\r\\nUser-Agent: {UA}\\r\\n" '
        f'-i "{url}" -c copy -bsf:a aac_adtstoasc "{safe_out}"'
    )


class PlayServer:
    def __init__(self, port: int = 0):
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def url_for(self, m3u8: str) -> str:
        return f"http://127.0.0.1:{self.port}/play?u={urllib.parse.quote(m3u8, safe='')}"

    def stop(self):
        if self._srv:
            self._srv.shutdown()


def main():
    srv = PlayServer(8765)
    port = srv.start()
    print(f"播放服务已启动: http://127.0.0.1:{port}")
    print("用法: http://127.0.0.1:%d/play?u=<URL编码的 m3u8 地址>" % port)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
