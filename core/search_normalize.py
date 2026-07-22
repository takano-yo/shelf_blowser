"""core/search_normalize.py — サイト内検索の照合用テキスト正規化（Python 側）。

docs/site-search.md「照合仕様」で固定した規則を実装する。**コーパス構築（build）と
クエリ側（JS）の双方が同一規則で正規化してから照合する**ため、この規則は Python と
JS で一致していなければならない。一致は共有テストベクタ
（core/search_normalize_vectors.json）を双方のテストから読むことで担保する。

正規化の 4 段（この順で適用。表示は常に原文のまま・正規化は照合専用）:
  1. Unicode NFKC 正規化（全角/半角統一。副作用として ㈱→(株)・①→1 等の互換分解も許容）
  2. 英字の小文字化（A→a）
  3. カタカナ→ひらがな（ヴ→ゔ を含む。長音「ー」はそのまま）
  4. 小書き仮名→並字（ぁぃぅぇぉっゃゅょゎゕゖ → あいうえおつやゆよわかけ）

濁点/半濁点の統一（は≠ば）・長音の除去は行わない。

規則を変えたら NORMALIZE_VERSION を上げる（manifest に記録し、コーパスの再生成判断に使う）。
"""

from __future__ import annotations

import unicodedata

# 正規化規則のバージョン。規則（下記の 4 段のいずれか）を変更したら必ず上げる。
NORMALIZE_VERSION = "1"

# 小書き仮名 → 並字（ひらがな基準。カタカナ→ひらがな変換後に適用する）。
_SMALL_KANA = str.maketrans({
    "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
    "っ": "つ", "ゃ": "や", "ゅ": "ゆ", "ょ": "よ",
    "ゎ": "わ", "ゕ": "か", "ゖ": "け",
})


def _katakana_to_hiragana(s: str) -> str:
    """カタカナ（U+30A1..U+30F6）をひらがなへ（-0x60）。長音符 ー(U+30FC) は対象外。"""
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def normalize_search(s):
    """検索照合用にテキストを正規化する（表示には使わない）。空・None は "" を返す。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)  # 1. 全角/半角統一（＋互換分解）
    s = s.lower()                          # 2. 英字小文字化
    s = _katakana_to_hiragana(s)           # 3. カタカナ→ひらがな（ヴ→ゔ含む）
    s = s.translate(_SMALL_KANA)           # 4. 小書き仮名→並字
    return s


def book_haystack(book):
    """1 レコードの検索対象文字列（タイトル・著者・出版社・シリーズ名）を連結。

    server/_book_haystack・site/bookHaystack と同一対象・同一思想。**正規化前**の
    連結を返す（呼び出し側で normalize_search をかける）。
    """
    parts = [book.get("title") or "", book.get("creatorRaw") or ""]
    parts.extend(book.get("creators") or [])
    parts.extend(book.get("publishers") or [])
    for s in book.get("series") or []:
        if s and s.get("title"):
            parts.append(s["title"])
    return " ".join(parts)
