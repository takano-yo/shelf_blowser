"""core/openbd.py — OpenBD API から書影 URL を取得する共有ロジック。

もとは build/build.py に内蔵していた表紙取得処理を、CLI バッチ（build）と
API サーバ（server）の双方から使えるよう共通化したもの。ISBN 単位でファイル
キャッシュし、再取得を避ける（build と server で同じキャッシュディレクトリを
指定すれば取得結果を共有できる）。標準ライブラリのみで動作する。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OPENBD_API = "https://api.openbd.jp/v1/get"


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


def enrich_covers(records, cache_dir, batch=100, retries=4):
    """先頭 ISBN を代表に OpenBD で表紙 URL を引き、coverUrl を埋める。

    取得結果は cache_dir に ISBN 単位でキャッシュし、再実行時は再取得しない。
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
