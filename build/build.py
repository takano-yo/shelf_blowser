#!/usr/bin/env python3
"""build.py — source/日本近代文学.json から site/data/books.json を生成する。

build/README.md の要件定義に対応した実装。
処理フロー: load → normalize → (enrich: 表紙) → sort → write

正規化ロジックは core/normalize.py に集約し、API サーバ（server/）と共有する。
このスクリプトは既定キーワードの「事前ビルド（バッチ）」を担い、生成物
site/data/books.json は動的検索が使えないときのフォールバック表示にも使われる。

--ndc 指定時は NDC 棚データの生成モードになり、fetch/ndc_fetch.py が取得した
分類ごとの生レスポンス（.cache/ndc/）を同一の正規化・整列ロジックで
site/data/ndc/<分類記号>.json ＋ NDC マスタ index.json へ出力する
（docs/site-structure.md「データ設計」）。

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
from core.ciniisearch import fetched_at, items_from_response, total_results  # noqa: E402
from core.normalize import normalize_item  # noqa: E402
from core.openbd import enrich_covers  # noqa: E402 — build と server で共有（段階3 表紙取得）
from fetch.ndc_fetch import all_codes  # noqa: E402 — NDC 分類記号の一覧（fetch と共通）


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


# ---------------------------------------------------------------------------
# NDC 棚データの生成（--ndc）
# ---------------------------------------------------------------------------

def build_ndc(cache_dir, out_dir, max_records=1000):
    """`.cache/ndc/` の生レスポンスから NDC 棚データ＋マスタ index.json を生成する。

    - 棚データ `<分類記号>.json` は books.json と同一スキーマ・同一整列
      （ownerCount 降順・同値 ncid 昇順）。max_records 超過分は所蔵館数上位を
      優先して切り詰める（docs/site-structure.md 問題点と対処 #3）。
    - index.json は全 1,110 分類の { code, label, count, hasData } ほかを持つ。
      count はキャッシュの totalResults、無ければ counts.json（件数実測モードの
      出力）から補う。label は labels.json（fetch/ndc_labels.py が JLA 公式の
      NDC9 版 CC-BY データから生成）があれば収録し、無い分類（欠番）や
      labels.json 自体が無い場合は null（docs/site-structure.md 同 #2）。
    - 冪等: 同じキャッシュから常に同じ棚データを生成する（棚ファイルはバイト
      一致。index.json は generatedAt のみ実行時刻で変わる）。
    """
    cache_dir = Path(cache_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = {}
    counts_path = cache_dir / "counts.json"
    if counts_path.is_file():
        counts = json.loads(counts_path.read_text(encoding="utf-8")).get("counts", {})

    labels, label_source = {}, None
    labels_path = cache_dir / "labels.json"
    if labels_path.is_file():
        ldata = json.loads(labels_path.read_text(encoding="utf-8"))
        labels = ldata.get("labels", {})
        label_source = ldata.get("source")

    classes = []
    built = skipped = 0
    for code in all_codes():
        cache_path = cache_dir / f"{code}.json"
        entry = {"code": code, "label": labels.get(code),
                 "count": counts.get(code),
                 "records": 0, "hasData": False, "fetchedAt": None}
        if cache_path.is_file():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            records = [normalize_item(it) for it in items_from_response(data)]
            records.sort(key=lambda r: (-r["ownerCount"], r["ncid"]))
            records = records[:max_records]
            entry["count"] = total_results(data)
            entry["fetchedAt"] = fetched_at(data)
            if records:
                (out_dir / f"{code}.json").write_text(
                    json.dumps(records, ensure_ascii=False,
                               separators=(",", ":")),
                    encoding="utf-8",
                )
                entry["records"] = len(records)
                entry["hasData"] = True
                built += 1
            else:
                skipped += 1
        else:
            skipped += 1
        classes.append(entry)

    index = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sort": "ownerCount desc, ncid asc",
        "maxRecords": max_records,
        "dataSource": {
            "name": "CiNii Books",
            "url": "https://ci.nii.ac.jp/books/",
            "license": "CC BY 4.0",
        },
        # 分類名の出典。labels.json（JLA 公式 NDC9 版・CC-BY）から転記する。
        # 無い場合は null（分類名未収録 → docs/site-structure.md #2）。
        "labelSource": label_source,
        "classes": classes,
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return built, skipped, index


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
    p.add_argument("--ndc", nargs="?", const=".cache/ndc/", default=None,
                   metavar="CACHE_DIR",
                   help="NDC 棚データの生成モード（fetch/ndc_fetch.py の出力を"
                        "読む。既定 .cache/ndc/）")
    p.add_argument("--ndc-out", default="site/data/ndc/",
                   help="NDC 棚データの出力ディレクトリ")
    p.add_argument("--ndc-max", type=int, default=1000,
                   help="NDC 棚 1 分類あたりの件数上限（既定 1000。"
                        "全分類の件数実測にもとづき確定 → docs/site-structure.md #3）")
    args = p.parse_args(argv)

    if args.ndc is not None:
        built, skipped, index = build_ndc(args.ndc, args.ndc_out,
                                          max_records=args.ndc_max)
        print(f"NDC 棚データ生成: {built} 分類 / データなし {skipped} 分類"
              f" -> {Path(args.ndc_out)}")
        print(f"  index.json: 全 {len(index['classes'])} 分類"
              f"（件数上限 {args.ndc_max} 件）")
        return 0

    records, meta = build(
        args.source, args.out, covers=args.covers, cache=args.cache,
        pretty=args.pretty, limit=args.limit,
    )
    print(f"生成: {meta['total']} 件 -> {Path(args.out) / 'books.json'}")
    print(f"  ISBN 保有: {meta['withIsbn']} 件 / 表紙取得: {meta['withCover']} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
