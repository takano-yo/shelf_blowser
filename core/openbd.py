"""core/openbd.py — OpenBD API から書影 URL を取得する共有ロジック。

もとは build/build.py に内蔵していた表紙取得処理を、CLI バッチ（build）と
API サーバ（server）の双方から使えるよう共通化したもの。ISBN 単位でファイル
キャッシュし、再取得を避ける（build と server で同じキャッシュディレクトリを
指定すれば取得結果を共有できる）。標準ライブラリのみで動作する。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OPENBD_API = "https://api.openbd.jp/v1/get"


def _write_json_atomic(path, obj):
    """一時ファイルへ書いてから rename する（中断時に部分ファイルを残さない）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _http_get_json(url, retries):
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "shelf_blowser/0.1"}
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


def enrich_covers(records, cache_dir, batch=100, retries=4, interval=0.2,
                  progress=False):
    """先頭 ISBN を代表に OpenBD で表紙 URL を引き、coverUrl を埋める。

    取得結果は cache_dir に ISBN 単位でキャッシュし、再実行時は再取得しない。
    `batch` は 1 リクエストで問い合わせる ISBN 数（OpenBD は GET でも 1,000 件
    程度まで受け付ける）、`interval` はリクエスト間隔（秒。API 提供元への
    マナー）。大量取得（NDC 棚など）では batch を大きく・interval を長めに取り、
    リクエスト本数と負荷を抑える。`progress=True` で進捗を stderr に出す。
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_path(isbn):
        return cache_dir / f"{isbn}.json"

    # 代表 ISBN を持つレコードを集める
    targets = {}  # isbn -> [record, ...]
    for r in records:
        if r["isbn"]:
            targets.setdefault(r["isbn"][0], []).append(r)

    # キャッシュ済みを先に反映し、未取得分だけ問い合わせる。
    # 破損・空のキャッシュ（中断で生じうる）は未取得扱いにして取り直す。
    pending = []
    cache_mem = {}
    for isbn in targets:
        p = cache_path(isbn)
        if p.exists():
            try:
                cache_mem[isbn] = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pending.append(isbn)
        else:
            pending.append(isbn)

    total_batches = (len(pending) + batch - 1) // batch
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
            _write_json_atomic(cache_path(isbn), rec)
        if progress:
            print(f"  OpenBD: {i // batch + 1}/{total_batches} バッチ完了"
                  f"（未取得 {len(pending)} ISBN・batch={batch}）", file=sys.stderr)
        time.sleep(interval)  # マナー: 間隔を空ける

    filled = 0
    for isbn, recs in targets.items():
        cover = (cache_mem.get(isbn) or {}).get("coverUrl") or None
        for r in recs:
            r["coverUrl"] = cover
            if cover:
                filled += 1
    return filled
