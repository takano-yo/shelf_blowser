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
from core.search_normalize import (  # noqa: E402 — 検索コーパス生成（手順2）
    NORMALIZE_VERSION, book_haystack, normalize_search,
)
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

def build_ndc(cache_dir, out_dir, max_records=1000, covers=False,
              cover_cache=".cache/openbd/", cover_batch=1000,
              cover_interval=1.0):
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
    built = skipped = total_covers = 0
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
            # 表紙付与（OpenBD）。ISBN 単位キャッシュを books.json と共有するため、
            # 分類をまたいで重複する ISBN は一度しか問い合わせない。
            if covers and records:
                total_covers += enrich_covers(
                    records, cover_cache, batch=cover_batch,
                    interval=cover_interval)
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
        "withCover": total_covers,
        "dataSource": {
            "name": "CiNii Books",
            "url": "https://ci.nii.ac.jp/books/",
            "license": "CC BY 4.0",
        },
        # 表紙画像の出典（books.json と同じく OpenBD）。
        "coverSource": {
            "name": "openBD",
            "url": "https://openbd.jp/",
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


def enrich_ndc_covers_inplace(ndc_out, cache=".cache/openbd/",
                              batch=1000, interval=1.0):
    """既存の `site/data/ndc/<記号>.json` に OpenBD 表紙を追記する（その場更新）。

    NDC の生レスポンス（`.cache/ndc/`）はリポジトリ管理外のため再取得なしに
    棚データを作り直せない。一方 `site/data/ndc/*.json` はコミット済みの成果物
    なので、そこへ表紙だけを後付けできるようにする。棚を 1 ファイルずつ読み込み、
    `enrich_covers` で表紙を引いて（`.cache/openbd/` に ISBN 単位で永続化。分類を
    またぐ重複 ISBN・books.json との重複は最初の 1 回だけ問い合わせ、以降は
    キャッシュから解決）書き戻す。冪等（棚の並び・スキーマは変えず coverUrl のみ更新）。
    """
    ndc_out = Path(ndc_out)
    code_files = sorted(p for p in ndc_out.glob("*.json") if p.name != "index.json")

    # 棚を 1 ファイルずつ処理する（全棚を同時に展開しない＝メモリ安全）。
    # 表紙は ISBN 単位で `cache` に永続化されるため、分類をまたいで重複する
    # ISBN は最初の 1 回だけ問い合わせ、以降はキャッシュから解決する
    # （＝横断的な重複排除は共有キャッシュが担う）。
    filled = 0
    for idx, p in enumerate(code_files, 1):
        records = json.loads(p.read_text(encoding="utf-8"))
        filled += enrich_covers(records, cache, batch=batch,
                                interval=interval)
        p.write_text(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        if idx % 50 == 0 or idx == len(code_files):
            print(f"  NDC 表紙付与: {idx}/{len(code_files)} ファイル"
                  f"（累計 {filled} 件）", file=sys.stderr)

    # index.json の withCover を実測値へ更新（あれば）。
    index_path = ndc_out / "index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["withCover"] = filled
        index.setdefault("coverSource", {"name": "openBD",
                                         "url": "https://openbd.jp/"})
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return len(code_files), filled


# ---------------------------------------------------------------------------
# 検索コーパスの生成（--search-index） … docs/site-search.md 実装手順 2
# ---------------------------------------------------------------------------

def build_search_index(ndc_dir, out_dir, max_results=1000, fetch_limit=30):
    """`site/data/ndc/` から類（1 桁 0〜9）ごとの検索コーパスを冪等生成する。

    コーパス 1 行 = 類内ユニーク書誌（ncid で重複排除）。同一書誌が類目/綱目/細目の
    複数ファイルに現れる階層重複は、**最も浅い（記号が短い）分類ファイル**の出現を
    採用する（`all_codes()` は 0〜9 → 00〜99 → 000〜999 の浅い順のため、初出＝最浅）。

    出力（列指向 JSON。docs/site-search-poc.md の確定形式）:
      corpus-<類>.json = {"s": [正規化済み検索文字列…],
                          "f": [収録分類記号…], "i": [その分類ファイル内の行位置…]}
      並びは類内の所蔵館数降順・同値 ncid 昇順（＝ランキング順。site/data/ndc と同一規則）。
    書誌本体は site/data/ndc/<f>.json の i 行目から解決する（二重保存しない）。

    冪等: 同じ ndc データからは corpus-*.json がバイト一致で再生成される
    （manifest.json の generatedAt のみ実行時刻で変わる）。
    """
    ndc_dir = Path(ndc_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    classes_meta = []
    for d in range(10):
        digit = str(d)
        # 類 digit に属する分類記号（0→00..09→000..099 の浅い順）。
        codes = [c for c in all_codes() if c.startswith(digit)]
        seen = set()
        rows = []          # (ownerCount, ncid, s, f, i)
        raw_rows = 0
        for code in codes:
            p = ndc_dir / f"{code}.json"
            if not p.is_file():
                continue
            records = json.loads(p.read_text(encoding="utf-8"))
            for i, b in enumerate(records):
                raw_rows += 1
                ncid = b.get("ncid")
                if not ncid or ncid in seen:
                    continue  # 初出＝最も浅いファイルの参照になる
                seen.add(ncid)
                s = normalize_search(book_haystack(b))
                rows.append((b.get("ownerCount") or 0, ncid, s, code, i))
        # ランキング（所蔵館数降順・同値 ncid 昇順）。site/data/ndc と同一規則。
        rows.sort(key=lambda r: (-r[0], r[1]))
        corpus = {
            "s": [r[2] for r in rows],
            "f": [r[3] for r in rows],
            "i": [r[4] for r in rows],
        }
        data = json.dumps(corpus, ensure_ascii=False, separators=(",", ":"))
        (out_dir / f"corpus-{digit}.json").write_text(data, encoding="utf-8")
        classes_meta.append({
            "class": digit,
            "file": f"corpus-{digit}.json",
            "unique": len(rows),
            "rawRows": raw_rows,
            "bytes": len(data.encode("utf-8")),
        })

    manifest = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "normalizeVersion": NORMALIZE_VERSION,
        "format": "columnar",  # {"s":[],"f":[],"i":[]}（docs/site-search-poc.md）
        "sort": "ownerCount desc, ncid asc",
        "maxResults": max_results,   # 検索結果の表示上限（所蔵館数上位）
        "fetchLimit": fetch_limit,   # 書誌本体を解決する分類ファイルの取得上限 K
        "bodySource": "site/data/ndc/<f>.json の i 行目",
        "classes": classes_meta,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


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
    p.add_argument("--ndc-covers", action="store_true",
                   help="NDC 棚データ生成時に OpenBD 表紙も付与（--ndc と併用）")
    p.add_argument("--ndc-covers-inplace", action="store_true",
                   help="既存 site/data/ndc/*.json に OpenBD 表紙を後付けする"
                        "（NDC 生キャッシュ不要。--ndc-out を対象に更新）")
    p.add_argument("--search-index", action="store_true",
                   help="検索コーパスを生成する（site/data/ndc/ → site/data/search/。"
                        "docs/site-search.md 手順2。Git 管理外・デプロイ時生成）")
    p.add_argument("--search-out", default="site/data/search/",
                   help="検索コーパスの出力ディレクトリ（既定 site/data/search/）")
    p.add_argument("--search-fetch-limit", type=int, default=30,
                   help="書誌本体を解決する分類ファイルの取得上限 K"
                        "（既定 30 ≒ 生 12MB。docs/site-search-poc.md で確定）")
    p.add_argument("--cover-batch", type=int, default=1000,
                   help="OpenBD 1 リクエストの ISBN 数（NDC 一括取得の既定 1000）")
    p.add_argument("--cover-interval", type=float, default=1.0,
                   help="OpenBD リクエスト間隔・秒（既定 1.0。API 提供元へのマナー）")
    args = p.parse_args(argv)

    if args.search_index:
        manifest = build_search_index(
            args.ndc_out, args.search_out,
            max_results=1000,  # 検索結果の表示上限（要件定義）
            fetch_limit=args.search_fetch_limit)
        total_uniq = sum(c["unique"] for c in manifest["classes"])
        total_bytes = sum(c["bytes"] for c in manifest["classes"])
        print(f"検索コーパス生成: 全 {len(manifest['classes'])} 類"
              f" / ユニーク {total_uniq:,} 件 -> {Path(args.search_out)}")
        print(f"  正規化バージョン {manifest['normalizeVersion']}"
              f" / 生 {total_bytes/1e6:.1f}MB / 取得上限 K={args.search_fetch_limit}")
        return 0

    if args.ndc_covers_inplace:
        files, filled = enrich_ndc_covers_inplace(
            args.ndc_out, cache=args.cache, batch=args.cover_batch,
            interval=args.cover_interval)
        print(f"NDC 棚 表紙付与（その場更新）: {files} ファイル"
              f" / 表紙 {filled} 件 -> {Path(args.ndc_out)}")
        return 0

    if args.ndc is not None:
        built, skipped, index = build_ndc(
            args.ndc, args.ndc_out, max_records=args.ndc_max,
            covers=args.ndc_covers, cover_cache=args.cache,
            cover_batch=args.cover_batch, cover_interval=args.cover_interval)
        print(f"NDC 棚データ生成: {built} 分類 / データなし {skipped} 分類"
              f" -> {Path(args.ndc_out)}")
        print(f"  index.json: 全 {len(index['classes'])} 分類"
              f"（件数上限 {args.ndc_max} 件・表紙 {index.get('withCover', 0)} 件）")
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
