#!/usr/bin/env python3
"""ndc_fetch.py — NDC 分類ごとの一覧を CiNii Books OpenSearch から取得するバッチ。

fetch/README.md「A. NDC 分類ごとの一覧取得」の実装。対象は NDC の
類 10（0〜9）・綱 100（00〜99）・目 1,000（000〜999）＝最大 1,110 分類で、
分類記号ごとに `clas=<記号>*`（前方一致）で取得する
（分類検索パラメータは source/0.json ＝ clas=0* の実レスポンスで確認済み）。

2 つのモードを持つ:

  1) 件数実測モード（--counts）
       count=1 の軽量コールで opensearch:totalResults だけを集め、
       <out>/counts.json に保存する。docs/site-structure.md 手順 0-③
       （データ総量の見積り → 1 分類あたりの件数上限の確定）に使う。
  2) 一覧取得モード（既定）
       生レスポンスを <out>/<分類記号>.json へそのまま保存する（Git 管理外）。
       正規化と site/data/ndc/ への出力は build/build.py --ndc が担う。

マナー（fetch の既存方針）: 直列取得・リクエスト間隔（既定 1 秒）・連絡先入り
User-Agent・指数バックオフ（core.ciniisearch）・取得済みスキップ（冪等・中断
再開可能）・失敗分類の記録（<out>/failed.txt）。標準ライブラリのみで動く。

実行イメージ:
    python fetch/ndc_fetch.py --counts --out .cache/ndc/   # 件数実測（約 40 分）
    python fetch/ndc_fetch.py --out .cache/ndc/            # 全分類の一覧取得
    python fetch/ndc_fetch.py --codes 910,911 --out .cache/ndc/
    python fetch/ndc_fetch.py --level 3 --limit 10 --out .cache/ndc/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# リポジトリ直下を import パスへ追加し、core を共有モジュールとして読む。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.ciniisearch import fetch_response, total_results  # noqa: E402


def all_codes(levels=(1, 2, 3)):
    """NDC 分類記号の一覧を階層順（0〜9, 00〜99, 000〜999）で返す。"""
    codes = []
    if 1 in levels:
        codes += [f"{i:01d}" for i in range(10)]
    if 2 in levels:
        codes += [f"{i:02d}" for i in range(100)]
    if 3 in levels:
        codes += [f"{i:03d}" for i in range(1000)]
    return codes


def _write_json_atomic(path: Path, data) -> None:
    """クラッシュ時に壊れたファイルを残さないよう、一時ファイル経由で書き込む。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def _is_valid_cache(path: Path) -> bool:
    """取得済みキャッシュとして有効か（存在し JSON として読めるか）を返す。"""
    if not path.is_file():
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False


def fetch_counts(codes, out_dir: Path, interval: float, retries: int) -> dict:
    """件数実測モード: 各分類の totalResults を counts.json へ収集する（再開可能）。"""
    counts_path = out_dir / "counts.json"
    store = {"counts": {}}
    if counts_path.is_file():
        store = json.loads(counts_path.read_text(encoding="utf-8"))
        store.setdefault("counts", {})
    counts = store["counts"]

    todo = [c for c in codes if c not in counts]
    done = fetched = failed = 0
    failures = []
    for code in todo:
        try:
            data = fetch_response(clas=code + "*", count=1, retries=retries)
            total = total_results(data)
            if total is None:
                raise ValueError(f"totalResults が読めない: {code}")
            counts[code] = total
            fetched += 1
        except Exception as exc:  # noqa: BLE001 — 失敗を記録して続行
            failures.append(code)
            failed += 1
            print(f"失敗: {code} ({exc})", file=sys.stderr)
        done += 1
        # 中断してもここまでの実測を失わないよう、10 件ごとに書き出す
        if done % 10 == 0 or done == len(todo):
            store["counts"] = dict(sorted(counts.items(), key=lambda kv: (len(kv[0]), kv[0])))
            store["measuredAt"] = _now_utc()
            _write_json_atomic(counts_path, store)
            print(f"進捗 {done}/{len(todo)}（取得 {fetched} / 失敗 {failed}）",
                  file=sys.stderr)
        if done < len(todo):
            time.sleep(interval)

    skipped = len(codes) - len(todo)
    print(f"件数実測 完了: 取得 {fetched} / スキップ {skipped} / 失敗 {failed}"
          f" -> {counts_path}", file=sys.stderr)
    return {"fetched": fetched, "skipped": skipped, "failed": failures}


def fetch_lists(codes, out_dir: Path, count: int, interval: float,
                retries: int) -> dict:
    """一覧取得モード: 生レスポンスを <out>/<記号>.json へ保存する（再開可能）。"""
    todo = [c for c in codes if not _is_valid_cache(out_dir / f"{c}.json")]
    done = fetched = failed = 0
    failures = []
    for code in todo:
        try:
            data = fetch_response(clas=code + "*", count=count, retries=retries)
            if not data.get("@graph"):
                raise ValueError(f"@graph が無いレスポンス: {code}")
            _write_json_atomic(out_dir / f"{code}.json", data)
            fetched += 1
        except Exception as exc:  # noqa: BLE001 — 失敗を記録して続行
            failures.append(code)
            failed += 1
            print(f"失敗: {code} ({exc})", file=sys.stderr)
        done += 1
        if done % 10 == 0 or done == len(todo):
            print(f"進捗 {done}/{len(todo)}（取得 {fetched} / 失敗 {failed}）",
                  file=sys.stderr)
        if done < len(todo):
            time.sleep(interval)

    skipped = len(codes) - len(todo)
    print(f"一覧取得 完了: 取得 {fetched} / スキップ {skipped} / 失敗 {failed}"
          f" -> {out_dir}", file=sys.stderr)
    return {"fetched": fetched, "skipped": skipped, "failed": failures}


def _now_utc() -> str:
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="NDC 分類ごとの CiNii 一覧取得（生レスポンス → .cache/ndc/）")
    p.add_argument("--out", default=".cache/ndc/",
                   help="出力ディレクトリ（生レスポンス・Git 管理外）")
    p.add_argument("--counts", action="store_true",
                   help="件数実測モード（count=1 で totalResults のみ収集）")
    p.add_argument("--codes", default=None,
                   help="対象分類記号をカンマ区切りで指定（例: 910,911）")
    p.add_argument("--level", type=int, choices=(1, 2, 3), default=None,
                   help="対象階層のみ取得（1=類 / 2=綱 / 3=目。既定: 全階層）")
    p.add_argument("--count", type=int, default=10000,
                   help="一覧取得時の 1 分類あたり取得件数（既定 10000）")
    p.add_argument("--interval", type=float, default=1.0,
                   help="リクエスト間隔・秒（既定 1.0。CiNii へのマナー）")
    p.add_argument("--retries", type=int, default=4,
                   help="一時エラーの再試行回数（既定 4・指数バックオフ）")
    p.add_argument("--limit", type=int, default=None,
                   help="先頭 N 分類のみ処理（動作テスト用）")
    args = p.parse_args(argv)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        bad = [c for c in codes if not (c.isdigit() and 1 <= len(c) <= 3)]
        if bad:
            p.error(f"不正な分類記号: {','.join(bad)}（1〜3 桁の数字のみ）")
    else:
        levels = (args.level,) if args.level else (1, 2, 3)
        codes = all_codes(levels)
    if args.limit is not None:
        codes = codes[:args.limit]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.counts:
        result = fetch_counts(codes, out_dir, args.interval, args.retries)
    else:
        result = fetch_lists(codes, out_dir, args.count, args.interval,
                             args.retries)

    # 失敗分類を記録（再実行すれば取得済みスキップにより失敗分だけ再試行される）
    failed_path = out_dir / "failed.txt"
    if result["failed"]:
        failed_path.write_text("\n".join(result["failed"]) + "\n",
                               encoding="utf-8")
        print(f"失敗 {len(result['failed'])} 分類を {failed_path} に記録。"
              "再実行で失敗分のみ再試行される。", file=sys.stderr)
        return 1
    if failed_path.is_file():
        failed_path.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
