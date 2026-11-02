"""webdl 下载页服务的离线单元测试。"""
from __future__ import annotations

import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import webdl  # noqa: E402

ITEMS = [{
    "title": "测试影片", "year": "2024", "quality": "高清",
    "episodes": 2, "sources": ["测试源"],
    "links": [
        {"ep": "正片", "url": "http://127.0.0.1:1/a.mp4", "kind": "file", "tag": "直链文件"},
        {"ep": "正片", "url": "http://127.0.0.1:1/i.m3u8", "kind": "m3u8", "tag": "播放清单"},
    ],
}]

SAMPLE = b"\x00\x01" * 64          # 128 字节假视频


class _RangeHandler(BaseHTTPRequestHandler):
    """本地 Range-capable 假源站。"""

    def do_GET(self):
        m = None
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            import re
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else len(SAMPLE) - 1
            chunk, status = SAMPLE[start:end + 1], 206
        else:
            chunk, status = SAMPLE, 200
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        if status == 206:
            self.send_header(
                "Content-Range", f"bytes {int(m.group(1))}-{end}/{len(SAMPLE)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *a):
        pass


class TestPage(unittest.TestCase):
    def test_page_contains_links(self):
        page = webdl.render_page(ITEMS)
        self.assertIn("测试影片", page)
        self.assertIn("/file?u=", page)
        self.assertIn("download", page.lower())     # 下载按钮存在

    def test_page_marks_no_direct(self):
        page = webdl.render_page([{"title": "无直链", "year": "", "quality": "",
                                   "episodes": 1, "sources": ["x"],
                                   "links": [{"ep": "全片",
                                              "url": "http://x/i.m3u8",
                                              "kind": "m3u8", "tag": "播放清单"}]}])
        self.assertIn("没有整文件直链", page)

    def test_suggest_name(self):
        name = webdl.suggest_name("片名:测试", "2024", "第01集",
                                  "http://x/a/名字.mp4?token=1")
        self.assertTrue(name.endswith(".mp4"))
        self.assertNotIn(":", name)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        cls.up_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        cls.srv = webdl.DownloadServer(ITEMS)
        cls.port = cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.upstream.shutdown()

    def _get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()

    def test_dl_page_ok(self):
        status, headers, body = self._get("/dl")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("测试影片".encode("utf-8"), body)

    def test_file_missing_arg(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/file")
        self.assertEqual(cm.exception.code, 400)

    def test_file_download_full(self):
        u = urllib.parse.quote(f"http://127.0.0.1:{self.up_port}/a.mp4", safe="")
        status, headers, body = self._get(f"/file?u={u}&name=test.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, SAMPLE)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    def test_file_download_range(self):
        u = urllib.parse.quote(f"http://127.0.0.1:{self.up_port}/a.mp4", safe="")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/file?u={u}&name=test.mp4",
            headers={"Range": "bytes=2-9"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.read(), SAMPLE[2:10])
            self.assertIn("bytes", r.headers.get("Content-Range", ""))

    def test_file_name_utf8(self):
        u = urllib.parse.quote(f"http://127.0.0.1:{self.up_port}/a.mp4", safe="")
        name = urllib.parse.quote("测试影片(2024) 第01集.mp4", safe="")
        status, headers, body = self._get(f"/file?u={u}&name={name}")
        self.assertEqual(body, SAMPLE)
        cd = headers.get("Content-Disposition", "")
        self.assertIn("filename*", cd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
