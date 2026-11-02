"""downloader 的离线单元测试（不联网）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import downloader, resource  # noqa: E402
from run import _parse_episodes, _select_down  # noqa: E402

PLAIN = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
seg-1.ts
#EXTINF:10.0,
seg-2.ts
#EXT-X-ENDLIST
"""

KEYED = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://k.example.com/key.bin"
#EXTINF:5,
a.ts
"""

FMP4 = """#EXTM3U
#EXT-X-MAP:URI="init.mp4"
#EXTINF:5,
a.m4s
"""


class TestParseM3u8(unittest.TestCase):
    def test_plain_segments(self):
        info = downloader.parse_m3u8(PLAIN, "https://cdn.x.com/v/index.m3u8")
        self.assertEqual(len(info["segments"]), 2)
        self.assertEqual(info["segments"][0], "https://cdn.x.com/v/seg-1.ts")
        self.assertFalse(info["encrypted"])

    def test_absolute_segment_preserved(self):
        text = "#EXTM3U\n#EXTINF:5,\nhttps://other.cdn/x.ts\n"
        info = downloader.parse_m3u8(text, "https://cdn.x.com/index.m3u8")
        self.assertEqual(info["segments"][0], "https://other.cdn/x.ts")

    def test_aes_detected(self):
        info = downloader.parse_m3u8(KEYED, "https://cdn.x.com/v/index.m3u8")
        self.assertTrue(info["encrypted"])
        self.assertEqual(info["key_method"], "AES-128")
        self.assertEqual(info["key_uri"], "https://k.example.com/key.bin")

    def test_fmp4_init(self):
        info = downloader.parse_m3u8(FMP4, "https://cdn.x.com/v/index.m3u8")
        self.assertEqual(info["init"], "https://cdn.x.com/v/init.mp4")
        self.assertEqual(info["segments"][0], "https://cdn.x.com/v/a.m4s")

    def test_empty(self):
        info = downloader.parse_m3u8("#EXTM3U\n", "https://cdn.x.com/i.m3u8")
        self.assertEqual(info["segments"], [])


class TestFilename(unittest.TestCase):
    def test_illegal_chars(self):
        self.assertEqual(downloader.safe_filename("a/b:c*d?.mp4"), "a_b_c_d_.mp4")

    def test_appends_ext(self):
        self.assertTrue(downloader.safe_filename("流浪地球").endswith(".mp4"))

    def test_empty_name(self):
        self.assertEqual(downloader.safe_filename(""), "video.mp4")


class TestCommand(unittest.TestCase):
    def test_headers_present(self):
        cmd = downloader.build_command("https://x.com/a.m3u8", Path("out.mp4"),
                                       "https://x.com/", "UA-TEST")
        joined = " ".join(cmd)
        self.assertIn("Referer: https://x.com/", joined)
        self.assertIn("UA-TEST", joined)
        self.assertIn("-c copy", joined)
        self.assertIn("aac_adtstoasc", joined)

    def test_has_ffmpeg_returns_bool(self):
        self.assertIsInstance(downloader.has_ffmpeg(), bool)


class TestDownLinks(unittest.TestCase):
    def test_parse_mp4_lines(self):
        raw = ("第01集$https://dow.x.com/a/S01E01.mp4#第02集$https://dow.x.com/a/S01E02.mp4")
        got = resource.parse_down_url(raw)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], ("第01集", "https://dow.x.com/a/S01E01.mp4", "file"))
        self.assertEqual(got[1][2], "file")

    def test_parse_multi_line_and_schemes(self):
        raw = ("全片$https://x.com/a.mp4$$$全片$magnet:?xt=urn:btih:ABC"
               "$$$全片$ftp://f.x.com/a.mkv")
        got = resource.parse_down_url(raw)
        kinds = [k for _, _, k in got]
        self.assertEqual(kinds, ["file", "magnet", "ftp"])

    def test_parse_rejects_garbage(self):
        self.assertEqual(resource.parse_down_url("no"), [])
        self.assertEqual(resource.parse_down_url(""), [])
        self.assertEqual(resource.parse_down_url("第1集$not-a-url"), [])

    def test_classify(self):
        self.assertEqual(resource.classify_down("https://x.com/a/b.MP4?token=1"), "file")
        self.assertEqual(resource.classify_down("https://x.com/i/index.m3u8"), "m3u8")
        self.assertEqual(resource.classify_down("ed2k://|file|a.mp4|"), "ed2k")
        self.assertEqual(resource.classify_down("thunder://QUFodHRw"), "thunder")
        self.assertEqual(resource.classify_down("https://pan.x.com/s/abc"), "file")


class TestEpisodesSelect(unittest.TestCase):
    DOWN = [("第01集", "https://x.com/1.mp4", "file"),
            ("第02集", "https://x.com/2.mp4", "file"),
            ("第03集", "https://x.com/3.mp4", "file")]

    def test_all_by_default(self):
        row = {"down": self.DOWN, "url": "https://x.com/i.m3u8", "m3u8": True}
        self.assertEqual(len(_select_down(row, "")), 3)

    def test_range_and_single(self):
        row = {"down": self.DOWN, "url": "", "m3u8": False}
        got = _select_down(row, "1-2")
        self.assertEqual([ep for ep, _, _ in got], ["第01集", "第02集"])
        got = _select_down(row, "3")
        self.assertEqual(got[0][1], "https://x.com/3.mp4")

    def test_mixed_spec_and_out_of_range(self):
        row = {"down": self.DOWN, "url": "", "m3u8": False}
        got = _select_down(row, "2,9,1-3,，")
        self.assertEqual([ep for ep, _, _ in got], ["第01集", "第02集", "第03集"])

    def test_fallback_to_playlist(self):
        row = {"down": [], "url": "https://x.com/i.m3u8", "m3u8": True}
        got = _select_down(row, "")
        self.assertEqual(got[0][2], "m3u8")

    def test_file_ranked_first(self):
        row = {"down": [("第01集", "https://x.com/i.m3u8", "m3u8"),
                        ("第01集", "https://x.com/1.mp4", "file")],
               "url": "", "m3u8": False}
        got = _select_down(row, "1")
        self.assertEqual(got[0][1], "https://x.com/1.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
