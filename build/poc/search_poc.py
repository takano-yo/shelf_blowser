#!/usr/bin/env python3
"""search_poc.py — サイト内検索コーパス（方式①）の PoC・実測スクリプト。

docs/site-search.md 実装手順 1 の計測用。本番実装ではないため build/poc/ に置く
（手順 2 で core の共有正規化＋build.py --search-index に正式実装する）。

計測内容:
  - 類（1 桁 0〜9）ごとのコーパス生成時間・ユニーク件数
  - コーパス生素サイズ／gzip 転送サイズ（Pages CDN 圧縮の目安）
  - includes() 走査時間（代表クエリ）
  - 書誌本体を解決するのに必要な分類ファイル数の分布（上位 1,000 件・上限 K 判断用）
  - 方式②（バイグラム転置索引）の索引サイズ・転送サイズの概算比較

コーパス 1 行 = 類内ユニーク書誌（ncid 重複排除）。内容は
  { "s": <正規化済み検索文字列>, "f": <収録分類記号>, "i": <その分類ファイル内の行位置> }
のみ。書誌本体は site/data/ndc/<f>.json の i 行目から解決する（二重保存しない）。

参照する分類記号 f は「その書誌を含む最も浅い（記号が短い）分類ファイル」を選ぶ。
所蔵館数上位の書誌ほど 1 桁ファイル（<類>.json＝類全体の上位 1,000）に含まれるため、
広い検索語でも上位ヒットは少数の浅いファイルへ集まりやすい、という仮説を検証する。
"""

from __future__ import annotations

import gzip
import json
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
NDC_DIR = ROOT / "site" / "data" / "ndc"

# --- 正規化（手順 2 で core へ正式実装。ここでは PoC 用に同一規則を内蔵） ---
# 小書き仮名 → 並字（ひらがな基準。カタカナ→ひらがな変換後に適用する）。
_SMALL_KANA = str.maketrans({
    "ぁ": "あ", "ぃ": "い", "ぅ": "う", "ぇ": "え", "ぉ": "お",
    "っ": "つ", "ゃ": "や", "ゅ": "ゆ", "ょ": "よ",
    "ゎ": "わ", "ゕ": "か", "ゖ": "け",
})


def _kata_to_hira(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        # カタカナ U+30A1..U+30F6 → ひらがな（-0x60）。長音符 ー(U+30FC) は対象外。
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)  # 全角/半角統一（＋互換分解）
    s = s.lower()                          # 英字小文字化
    s = _kata_to_hira(s)                   # カタカナ→ひらがな（ヴ→ゔ含む）
    s = s.translate(_SMALL_KANA)           # 小書き仮名→並字
    return s


def book_haystack(b: dict) -> str:
    """検索対象フィールド（タイトル・著者・出版社・シリーズ名）を連結。
    server/_book_haystack・site/bookHaystack と同一対象。"""
    parts = [b.get("title") or "", b.get("creatorRaw") or ""]
    parts.extend(b.get("creators") or [])
    parts.extend(b.get("publishers") or [])
    for sname in b.get("series") or []:
        if sname and sname.get("title"):
            parts.append(sname["title"])
    return " ".join(parts)


def class_files(digit: str):
    """類 digit に属する分類ファイルを浅い順（1桁→2桁→3桁）に返す。"""
    files = []
    for length in (1, 2, 3):
        for p in sorted(NDC_DIR.glob("[0-9]" * length + ".json")):
            if p.stem.startswith(digit):
                files.append(p)
    return files


def build_corpus(digit: str):
    """類 digit のコーパスを構築して返す（生成時間も測る）。"""
    t0 = time.perf_counter()
    files = class_files(digit)
    seen = {}          # ncid -> corpus index（重複排除）
    corpus = []        # [{s, f, i}]
    raw_rows = 0
    for p in files:
        code = p.stem
        records = json.loads(p.read_text(encoding="utf-8"))
        for i, b in enumerate(records):
            raw_rows += 1
            ncid = b.get("ncid")
            if not ncid or ncid in seen:
                continue  # 浅いファイルを先に処理するため、初出＝最も浅い参照になる
            seen[ncid] = len(corpus)
            corpus.append({
                "s": normalize(book_haystack(b)),
                "f": code,
                "i": i,
                # 計測用（本番コーパスには入れない）: ランキング用 ownerCount
                "_oc": b.get("ownerCount") or 0,
            })
    # ランキング（所蔵館数降順・同値 ncid 昇順）で並べる。
    # 元ファイルが浅い順のため ncid の昇順キーは corpus 側で復元する必要はなく、
    # ここでは ownerCount 降順のみで近似（PoC）。本番は ncid も second key。
    corpus.sort(key=lambda r: -r["_oc"])
    dt = time.perf_counter() - t0
    return corpus, files, raw_rows, dt


def sizes(corpus):
    """本番形式（_oc を除く）でのコーパス生／gzip サイズを返す。"""
    slim = [{"s": r["s"], "f": r["f"], "i": r["i"]} for r in corpus]
    raw = json.dumps(slim, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, 9)
    return len(raw), len(gz)


def search_scan(corpus, query):
    """正規化済みコーパスを includes()（部分一致 AND）で走査。時間とヒット index を返す。"""
    terms = [normalize(t) for t in query.split() if t]
    t0 = time.perf_counter()
    hits = []
    for idx, r in enumerate(corpus):
        s = r["s"]
        if all(t in s for t in terms):
            hits.append(idx)
    dt = time.perf_counter() - t0
    return hits, dt


def files_for_topk(corpus, hits, top=1000):
    """上位 top 件のヒットを解決するのに必要な分類ファイルの集合サイズを返す。"""
    files = {}
    for idx in hits[:top]:
        f = corpus[idx]["f"]
        files[f] = files.get(f, 0) + 1
    return files


# 分類ファイルの生バイト数を一度だけ計測してキャッシュ（書誌解決の転送量見積り用）。
_FILE_BYTES: dict[str, int] = {}


def file_bytes(code: str) -> int:
    if code not in _FILE_BYTES:
        p = NDC_DIR / f"{code}.json"
        _FILE_BYTES[code] = p.stat().st_size if p.is_file() else 0
    return _FILE_BYTES[code]


def k_budget_curve(corpus, hits, top=1000):
    """書誌解決を「ヒット件数の多いファイルから順に取得」する貪欲戦略で、
    取得ファイル数 K を増やしたとき何件の書誌を表示できるか（＝カバー率）と、
    その累積転送バイト（生）を返す。K の妥当値を決めるための曲線。"""
    fmap = files_for_topk(corpus, hits, top)
    # ヒット件数降順でファイルを並べ、カバー件数と累積バイトを積み上げる。
    order = sorted(fmap.items(), key=lambda kv: -kv[1])
    curve = []
    cum_hits = 0
    cum_bytes = 0
    for k, (code, cnt) in enumerate(order, 1):
        cum_hits += cnt
        cum_bytes += file_bytes(code)
        curve.append((k, cum_hits, cum_bytes))
    return curve, len(order)


def bigram_index_estimate(corpus):
    """方式②（バイグラム転置索引）の索引規模を概算する。
    postings は uint32（4byte）想定。gzip はしない（転送は検索語のバイグラム分のみ）。"""
    from collections import defaultdict
    post = defaultdict(int)
    for idx, r in enumerate(corpus):
        s = r["s"]
        grams = {s[i:i + 2] for i in range(len(s) - 1)}
        for g in grams:
            post[g] += 1
    n_grams = len(post)
    total_postings = sum(post.values())
    approx_bytes = total_postings * 4 + n_grams * 8  # postings + キー概算
    return n_grams, total_postings, approx_bytes


# 代表クエリ（広い語・中頻度・複合 AND・カナ/英字/正規化が効く語）
QUERIES = ["日本", "歴史", "研究", "東京 大学", "python", "コンピュータ", "経済学"]


BIGRAM = "--bigram" in sys.argv


def main():
    digits = [a for a in sys.argv[1:] if not a.startswith("-")] or \
        [str(d) for d in range(10)]
    print("=" * 78)
    print("方式① コーパス走査 — 類ごとの実測")
    print("=" * 78)
    grand = {"raw": 0, "gz": 0, "uniq": 0}
    for d in digits:
        corpus, files, raw_rows, dt = build_corpus(d)
        raw, gz = sizes(corpus)
        grand["raw"] += raw
        grand["gz"] += gz
        grand["uniq"] += len(corpus)
        print(f"\n── 類 {d} ──────────────────────────────────────────────")
        print(f"  収録ファイル数: {len(files)}  収録行(延べ): {raw_rows:,}"
              f"  ユニーク: {len(corpus):,}")
        print(f"  生成時間: {dt:.2f}s")
        print(f"  コーパス 生: {raw/1e6:.2f}MB  gzip: {gz/1e6:.2f}MB"
              f"  (圧縮率 {gz/raw*100:.1f}%)")
        # 走査＋書誌解決ファイル数・K予算曲線
        for q in QUERIES:
            hits, sdt = search_scan(corpus, q)
            curve, nf = k_budget_curve(corpus, hits, top=1000)
            shown = min(len(hits), 1000)
            # K=20/30/50 でのカバー件数・累積転送（生 MB）
            def at(k):
                if not curve:
                    return (0, 0.0)
                row = curve[min(k, len(curve)) - 1]
                return (row[1], row[2] / 1e6)
            c20, b20 = at(20)
            c30, b30 = at(30)
            call, ball = (curve[-1][1], curve[-1][2] / 1e6) if curve else (0, 0.0)
            print(f"    q={q!r:14} ヒット {len(hits):6,}  走査 {sdt*1000:5.1f}ms"
                  f"  解決ファイル数 {nf:3}"
                  f"  K20:{c20:4}件/{b20:4.1f}MB"
                  f"  K30:{c30:4}件/{b30:4.1f}MB"
                  f"  全{shown}件:{ball:4.1f}MB")
        if BIGRAM:
            ng, tp, ab = bigram_index_estimate(corpus)
            print(f"  [方式②概算] バイグラム種 {ng:,}  postings {tp:,}"
                  f"  索引概算 {ab/1e6:.1f}MB")
    print("\n" + "=" * 78)
    print(f"合計（対象類 {len(digits)}）: ユニーク {grand['uniq']:,}"
          f"  生 {grand['raw']/1e6:.1f}MB  gzip {grand['gz']/1e6:.1f}MB")
    print("=" * 78)


if __name__ == "__main__":
    main()
