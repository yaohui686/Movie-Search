"""webui 网页下载台的服务端单元测试（不联网）。"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import webui  # noqa: E402

ROWS = [{
    "source": "测试源", "title": "测试影片", "year": "2024", "type": "科幻片",
    "area": "中国大陆", "remarks": "HD", "quality": "高清", "quality_raw": "HD",
    "lines": 1, "episodes": 2, "url": "http://127.0.0.1:1/i.m3u8", "m3u8": True,
    "down": [("第01集", "http://127.0.0.1:1/e01.mp4", "file"),
             ("第02集", "http://127.0.0.1:1/e02.mp4", "file")],
    "direct_down": True, "id": "1", "sources": ["测试源"],
    "score": 1.0, "is_extra": False,
}]


def fake_search(name, cfg, show_progress=True, on_progress=None, on_notice=None):
    if on_notice:
        on_notice("再用清洗后的写法「x」补一轮…")
    for i in range(1, 4):
        if on_progress:
            on_progress(i, 3, f"源{i}", 5, 0.5)
    return ROWS


def fake_info(url):
    return "1080x608@800k"


class _RangeHandler(BaseHTTPRequestHandler):
    """本地 Range-capable 假源站，供下载任务测试。"""
    SAMPLE = b"\x00\x01\x02\x03" * 4096            # 16 KB 假视频

    def do_GET(self):
        import re
        data = self.SAMPLE
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else len(data) - 1
            chunk, status = data[start:end + 1], 206
        else:
            chunk, status = data, 200
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        if status == 206:
            self.send_header(
                "Content-Range", f"bytes {int(m.group(1))}-{end}/{len(data)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *a):
        pass


def _find_node() -> str | None:
    import os
    import shutil
    found = shutil.which("node")
    if found:
        return found
    for cand in (
        ROOT.parent / ".dsh" / "dsh-runtimes" / "dsh-primary-runtime"
        / "dependencies" / "node" / "bin" / "node.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "nodejs" / "node.exe",
    ):
        if cand.exists():
            return str(cand)
    return None


class TestFrontendJs(unittest.TestCase):
    """前端脚本防回归：语法必须可解析、核心流程必须能跑通。"""

    NODE = _find_node()

    def setUp(self):
        if not self.NODE:
            self.skipTest("node 不存在，跳过前端脚本检查")

    def _script(self) -> str:
        import re
        m = re.search(r"<script>(.*?)</script>", webui.INDEX_HTML, re.S)
        assert m
        return m.group(1)

    def test_script_syntax(self):
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(self._script())
            path = f.name
        try:
            r = subprocess.run([self.NODE, "--check", path],
                               capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0,
                             "页面 JS 语法错误（会整段失效）：\n"
                             + r.stderr.decode("utf-8", "replace"))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_script_runtime_smoke(self):
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                         encoding="utf-8") as f:
            f.write(webui.INDEX_HTML)
            page = f.name
        smoke = ROOT / "tools" / "webui_smoke.js"
        try:
            r = subprocess.run([self.NODE, str(smoke), page],
                               capture_output=True, timeout=60)
            out = r.stdout.decode("utf-8", "replace")
            self.assertIn("ALL SMOKE PASS", out,
                          "前端运行时冒烟未通过：\n" + out
                          + r.stderr.decode("utf-8", "replace"))
        finally:
            Path(page).unlink(missing_ok=True)


class TestWebUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = webui.WebUIServer(fake_search, fake_info, {"k": "v"})
        cls.port = cls.srv.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, dict(r.headers), r.read()

    def test_index_page(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("影视检索".encode(), body)
        self.assertIn("/api/search".encode(), body)

    def test_search_stream(self):
        status, headers, raw = self._get("/api/search?wd=%E6%B5%8B%E8%AF%95")
        lines = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x]
        kinds = [x["t"] for x in lines]
        self.assertEqual(kinds[0], "n")               # 阶段提示在前
        self.assertTrue(all(k == "p" for k in kinds[1:-1]))
        self.assertEqual(kinds[-1], "r")              # 结果收尾
        self.assertEqual(lines[-1]["rows"][0]["title"], "测试影片")
        self.assertEqual(lines[-1]["rows"][0]["down"][0][2], "file")

    def test_search_missing_wd(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/search")
        self.assertEqual(cm.exception.code, 400)

    def test_api_info(self):
        status, _, raw = self._get("/api/info?u=http%3A%2F%2Fx%2Fi.m3u8")
        self.assertEqual(json.loads(raw)["res"], "1080x608@800k")

    def test_api_ping(self):
        _, _, raw = self._get("/api/ping")
        self.assertEqual(json.loads(raw), {"ok": True})

    def test_play_page(self):
        status, headers, body = self._get("/play?u=http%3A%2F%2Fx%2Fi.m3u8")
        self.assertIn("hls.js", body.decode("utf-8"))

    def test_proxy_and_dl_missing_u(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/proxy")
        self.assertEqual(cm.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as cm2:
            self._get("/file")
        self.assertEqual(cm2.exception.code, 400)

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/nowhere")
        self.assertEqual(cm.exception.code, 404)


class TestDownloadTask(unittest.TestCase):
    """「下载到本机」后台任务：启动 → 轮询进度 → 完成落盘。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from http.server import ThreadingHTTPServer as _THS
        cls.upstream = _THS(("127.0.0.1", 0), _RangeHandler)
        cls.up_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        cls.dl_dir = tempfile.mkdtemp(prefix="fsdl_")
        cls.srv = webui.WebUIServer(fake_search, fake_info, {}, download_dir=cls.dl_dir)
        cls.port = cls.srv.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.upstream.shutdown()

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, dict(r.headers), r.read()

    def test_download_task_flow(self):
        u = urllib.parse.quote(f"http://127.0.0.1:{self.up_port}/a.mp4", safe="")
        name = urllib.parse.quote("测试影片(2024) 第01集.mp4", safe="")
        with urllib.request.urlopen(
                f"{self.base}/api/download?u={u}&name={name}", timeout=10) as r:
            first = json.loads(r.read())
        self.assertIn("id", first)

        task = None
        for _ in range(50):
            with urllib.request.urlopen(f"{self.base}/api/downloads", timeout=10) as r:
                tasks = json.loads(r.read())["tasks"]
            task = next((t for t in tasks if t["id"] == first["id"]), None)
            if task and task["status"] == "done":
                break
            time.sleep(0.1)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["total"], len(_RangeHandler.SAMPLE))
        self.assertTrue(task["path"].endswith(".mp4"))
        self.assertEqual(Path(task["path"]).read_bytes(), _RangeHandler.SAMPLE)

    def test_download_missing_u(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/download")
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
