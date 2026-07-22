"""tests/test_search_normalize.py — 検索正規化の共有テストベクタ検証（Python 側）。

core/search_normalize_vectors.json を読み、core.search_normalize.normalize_search が
各ケースの期待値と一致することを検証する。**同じベクタを JS 側テスト
（site/js/ の検索正規化）からも読む**ことで、Python と JS の正規化一致を担保する
（docs/site-search.md 問題点と対処 #1）。

実行: python3 -m unittest tests.test_search_normalize
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.search_normalize import normalize_search, NORMALIZE_VERSION  # noqa: E402

VECTORS = json.loads(
    (ROOT / "core" / "search_normalize_vectors.json").read_text(encoding="utf-8")
)


class TestSearchNormalizeVectors(unittest.TestCase):
    def test_version_matches(self):
        """ベクタの version が実装の NORMALIZE_VERSION と一致する。"""
        self.assertEqual(VECTORS["version"], NORMALIZE_VERSION)

    def test_all_vectors(self):
        """全ベクタで normalize_search が期待値と一致する。"""
        for case in VECTORS["cases"]:
            with self.subTest(desc=case["desc"], input=case["input"]):
                self.assertEqual(normalize_search(case["input"]), case["expected"])

    def test_idempotent(self):
        """正規化は冪等（正規化済みをもう一度かけても変わらない）。"""
        for case in VECTORS["cases"]:
            once = normalize_search(case["input"])
            self.assertEqual(normalize_search(once), once)


if __name__ == "__main__":
    unittest.main()
