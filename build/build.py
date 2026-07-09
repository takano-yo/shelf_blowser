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
from pathlib import Path

# リポジトリ直下を import パスへ追加し、core を共有モジュールとして読む。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.normalize import normalize_item  # noqa: E402
from core.openbd import enrich_covers  # noqa: E402 — build と server で共有（段階3 表紙取得）


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
