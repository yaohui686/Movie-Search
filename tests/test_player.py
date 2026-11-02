"""播放代理的端到端测试：起服务 → 拉真实 m3u8 → 验证改写。

    python tests/test_player.py
"""
from __future__ import annotations

import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import player, resource  # noqa: E402


class TestRewrite(unittest.TestCase):
    def test_relative_segments_become_proxied_absolute(self):
        m3u8 = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXTINF:10.0,\n"
            "seg-1.ts\n"
            "#EXTINF:10.0,\n"
            "sub/seg-2.ts\n"
        )
        out = player.rewrite_m3u8(m3u8, "https://cdn.example.com/a/b/index.m3u8")
        self.assertIn("#EXTM3U", out)
        self.assertIn("/proxy?u=https%3A%2F%2Fcdn.example.com%2Fa%2Fb%2Fseg-1.ts", out)
        self.assertIn("/proxy?u=https%3A%2F%2Fcdn.example.com%2Fa%2Fb%2Fsub%2Fseg-2.ts", out)

    def test_absolute_segments_kept_absolute(self):
        m3u8 = "#EXTM3U\n#EXTINF:5,\nhttps://other.cdn/x.ts\n"
        out = player.rewrite_m3u8(m3u8, "https://cdn.example.com/index.m3u8")
        self.assertIn("other.cdn%2Fx.ts", out)

    def test_key_uri_rewritten(self):
        m3u8 = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nseg.ts\n'
        out = player.rewrite_m3u8(m3u8, "https://cdn.example.com/v/index.m3u8")
        self.assertIn('URI="/proxy?u=https%3A%2F%2Fcdn.example.com%2Fv%2Fkey.bin"', out)

    def test_referer_from_url(self):
        self.assertEqual(
            player._referer_for("https://vip.lzcdn2.com/2022/x/index.m3u8"),
            "https://vip.lzcdn2.com/",
        )


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = player.PlayServer(0)
        cls.port = cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def test_root(self):
        st, ct, body = self._get("/")
        self.assertEqual(st, 200)
        self.assertIn("FilmPlay", body.decode("utf-8"))

    def test_play_page(self):
        import urllib.parse
        target = "https://cdn.example.com/a/index.m3u8"
        st, ct, body = self._get("/play?u=" + urllib.parse.quote(target, safe=""))
        self.assertEqual(st, 200)
        html = body.decode("utf-8")
        self.assertIn("hls.js", html)
        self.assertIn("/proxy?u=", html)

    def test_proxy_missing_param(self):
        try:
            self._get("/proxy")
            self.fail("应当返回 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    unittest.main(verbosity=2)
