#!/usr/bin/env python3
"""build.py — source/日本近代文学.json から site/data/books.json を生成する。

build/README.md の要件定義に対応した実装。
処理フロー: load → normalize → (enrich: 表紙) → sort → write

正規化ロジックは core/normalize.py に集約し、API サーバ（server/）と共有する。
このスクリプトは既定キーワードの「事前ビルド（バッチ）」を担い、生成物
site/data/books.json は動的検索が使えないときのフォールバック表示にも使われる。

標準ライブラリのみで動作する（表紙取得 --covers 時のみ urllib でネットワークを使う）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

# リポジトリ直下を import パスへ追加し、core を共有モジュールとして読む。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.normalize import normalize_item  # noqa: E402


# ---------------------------------------------------------------------------
# 段階 3: 表紙取得（OpenBD）。--covers 指定時のみ実行する。
# ---------------------------------------------------------------------------

OPENBD_API = "https://api.openbd.jp/v1/get"


def enrich_covers(records, cache_dir, batch=100, retries=4):
    """先頭 ISBN を代表に OpenBD で表紙 URL を引き、coverUrl を埋める。

    取得結果は cache_dir に ISBN 単位でキャッシュし、再実行時は再取得しない。
    ネットワークは本関数内に閉じ込める（標準ライブラリ urllib）。
    """
    import urllib.parse

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_path(isbn):
        return cache_dir / f"{isbn}.json"

    # 代表 ISBN を持つレコードを集める
    targets = {}  # isbn -> [record, ...]
    for r in records:
        if r["isbn"]:
            targets.setdefault(r["isbn"][0], []).append(r)

    # キャッシュ済みを先に反映し、未取得分だけ問い合わせる
    pending = []
    cache_mem = {}
    for isbn in targets:
        p = cache_path(isbn)
        if p.exists():
            cache_mem[isbn] = json.loads(p.read_text(encoding="utf-8"))
        else:
            pending.append(isbn)

    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        url = OPENBD_API + "?" + urllib.parse.urlencode({"isbn": ",".join(chunk)})
        data = _http_get_json(url, retries)
        if data is None:
            data = [None] * len(chunk)
        for isbn, entry in zip(chunk, data):
            cover = None
            if entry:
                cover = (entry.get("summary") or {}).get("cover") or None
            rec = {"coverUrl": cover}
            cache_mem[isbn] = rec
            cache_path(isbn).write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8"
            )
        time.sleep(0.2)  # マナー: 間隔を空ける

    filled = 0
    for isbn, recs in targets.items():
        cover = (cache_mem.get(isbn) or {}).get("coverUrl") or None
        for r in recs:
            r["coverUrl"] = cover
            if cover:
                filled += 1
    return filled


def _http_get_json(url, retries):
    import urllib.request
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "shelf_blowser-build/0.1"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — リトライ対象を広く取る
            if attempt == retries - 1:
                print(f"  [warn] OpenBD 取得失敗: {e}", file=sys.stderr)
                return None
            time.sleep(delay)
            delay *= 2
    return None


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

def load_items(source_path):
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    return data["@graph"][0]["items"]


def build(source, out_dir, covers=False, cache=".cache/openbd/",
          pretty=False, limit=None):
    items = load_items(source)
    if limit is not None:
        items = items[:limit]

    records = [normalize_item(it) for it in items]

    filled = 0
    if covers:
        filled = enrich_covers(records, cache)

    # 整列: ownerCount 降順、同値は ncid 昇順（冪等）
    records.sort(key=lambda r: (-r["ownerCount"], r["ncid"]))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    indent = 2 if pretty else None
    separators = None if pretty else (",", ":")
    (out_dir / "books.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=indent,
                   separators=separators),
        encoding="utf-8",
    )

    with_isbn = sum(1 for r in records if r["isbn"])
    meta = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sourceFile": str(source),
        "total": len(records),
        "withIsbn": with_isbn,
        "withCover": filled,
        "sort": "ownerCount desc, ncid asc",
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records, meta


def main(argv=None):
    p = argparse.ArgumentParser(description="source 一覧 → site/data/books.json")
    p.add_argument("--source", default="source/日本近代文学.json",
                   help="入力 OpenSearch JSON")
    p.add_argument("--out", default="site/data/", help="出力ディレクトリ")
    p.add_argument("--covers", action="store_true",
                   help="OpenBD で表紙取得（段階3）を有効化")
    p.add_argument("--cache", default=".cache/openbd/",
                   help="OpenBD キャッシュ先")
    p.add_argument("--pretty", action="store_true", help="整形出力（デバッグ用）")
    p.add_argument("--limit", type=int, default=None,
                   help="先頭 N 件のみ処理（動作テスト用）")
    args = p.parse_args(argv)

    records, meta = build(
        args.source, args.out, covers=args.covers, cache=args.cache,
        pretty=args.pretty, limit=args.limit,
    )
    print(f"生成: {meta['total']} 件 -> {Path(args.out) / 'books.json'}")
    print(f"  ISBN 保有: {meta['withIsbn']} 件 / 表紙取得: {meta['withCover']} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
