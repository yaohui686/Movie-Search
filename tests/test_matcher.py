"""matcher 单元测试：python -m unittest discover -s tests -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filmsearch import matcher  # noqa: E402
from filmsearch import fuzzy    # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_brackets_and_punct(self):
        self.assertEqual(matcher.normalize("《流浪地球》"), "流浪地球")
        self.assertEqual(matcher.normalize("流浪 地球·2"), "流浪地球2")
        self.assertEqual(matcher.normalize("Ｄｕｎｅ"), "dune")


class TestSimilarity(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(matcher.similarity("流浪地球", "流浪地球"), 1.0)

    def test_sequel_penalty(self):
        """续集必须显著低于同名正片，否则会推荐错片。"""
        s = matcher.similarity("流浪地球", "流浪地球2")
        self.assertLess(s, 0.95)
        self.assertGreater(s, 0.7)

    def test_unrelated(self):
        self.assertLess(matcher.similarity("霸王别姬", "流浪地球"), 0.3)


class TestScore(unittest.TestCase):
    def test_year_bonus(self):
        a = matcher.score("流浪地球", 2019, "流浪地球", 2019)
        b = matcher.score("流浪地球", 2015, "流浪地球", 2019)
        self.assertGreater(a, b)

    def test_alias_match(self):
        s = matcher.score("The Wandering Earth", None, "流浪地球", None,
                          aliases=("流浪地球",))
        self.assertGreaterEqual(s, 0.9)

    def test_extract_year(self):
        self.assertEqual(matcher.extract_year("2024-03-01"), 2024)
        self.assertEqual(matcher.extract_year("1993"), 1993)
        self.assertIsNone(matcher.extract_year("无年份"))


class TestFuzzy(unittest.TestCase):
    def test_typo_tolerated(self):
        """错别字：流浪地求 vs 流浪地球，应该能匹配上。"""
        s = matcher.fuzzy_ratio("流浪地求", "流浪地球")
        self.assertGreaterEqual(s, 0.72)

    def test_short_query_no_boost(self):
        """两个字以内的查询不做 LCS 加成，避免「地球」匹配一切。"""
        s = matcher.fuzzy_ratio("地球", "流浪地球")
        self.assertLess(s, 0.72)

    def test_sequel_not_boosted(self):
        """包含关系不给 LCS 加成，续集不能被当成同一部。"""
        s1 = matcher.fuzzy_ratio("流浪地球", "流浪地球2")
        s2 = matcher.fuzzy_ratio("流浪地球", "流浪地球")
        self.assertLess(s1, s2 - 0.01)

    def test_unrelated_still_low(self):
        self.assertLess(matcher.fuzzy_ratio("流浪地求", "环太平洋"), 0.5)


class TestQueryClean(unittest.TestCase):
    def test_strip_year(self):
        self.assertEqual(fuzzy.clean("流浪地球2 2019"), "流浪地球2")
        self.assertEqual(fuzzy.clean("流浪地球(2019)"), "流浪地球")

    def test_strip_noise(self):
        self.assertEqual(fuzzy.clean("流浪地球 电影 1080P"), "流浪地球")
        self.assertEqual(fuzzy.clean("流浪地球 国语版"), "流浪地球")

    def test_variants_dedup(self):
        v = fuzzy.variants("流浪地球")
        self.assertEqual(v[0], "流浪地球")
        self.assertEqual(len(v), len(set(v)))

    def test_variants_include_clean(self):
        v = fuzzy.variants("流浪地球2 2019")
        self.assertIn("流浪地球2", v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
