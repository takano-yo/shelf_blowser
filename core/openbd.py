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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.ciniisearch import retry_after_seconds, user_agent  # UA は CiNii 側と共通

OPENBD_API = "https://api.openbd.jp/v1/get"

# GET のクエリ文字列がこの長さを超えるときは POST に切り替える。openBD は
# 1 リクエストで問い合わせられる ISBN 数の上限を公表しておらず、長い URL は
# 途中の経路で切られうるため、長さに依存しない POST を使う。
MAX_QUERY_BYTES = 2000


def _write_json_atomic(path, obj):
    """一時ファイルへ書いてから rename する（中断時に部分ファイルを残さない）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _fetch_json(isbns, retries):
    """openBD /v1/get へ ISBN 群を問い合わせて配列を返す。失敗時は None。

    CiNii 側と同じく、4xx（429 を除く）は叩き直しても変わらないため再試行しない。
    """
    query = urllib.parse.urlencode({"isbn": ",".join(isbns)})
    delay = 2
    for attempt in range(retries):
        try:
            if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
                req = urllib.request.Request(
                    OPENBD_API, data=query.encode("utf-8"),
                    headers={"User-Agent": user_agent(),
                             "Content-Type": "application/x-www-form-urlencoded"},
                )
            else:
                req = urllib.request.Request(
                    OPENBD_API + "?" + query,
                    headers={"User-Agent": user_agent()},
                )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                print(f"  [warn] OpenBD 取得失敗: {e}", file=sys.stderr)
                return None
            if attempt == retries - 1:
                print(f"  [warn] OpenBD 取得失敗: {e}", file=sys.stderr)
                return None
            wait = retry_after_seconds(e) or delay
            time.sleep(wait)
            delay = max(delay * 2, wait)
        except Exception as e:  # noqa: BLE001 — 5xx・ネットワーク・JSON 破損は再試行
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
    `batch` は 1 リクエストで問い合わせる ISBN 数（多いときは自動で POST に
    切り替わる → MAX_QUERY_BYTES）、`interval` はリクエスト間隔（秒。API 提供元への
    マナー）。大量取得（NDC 棚など）では batch を大きく・interval を長めに取り、
    リクエスト本数と負荷を抑える。`progress=True` で進捗を stderr に出す。

    取得できなかったバッチはキャッシュを作らないので、再実行すれば未取得分だけ
    問い合わせ直される（＝一時的な障害が「書影なし」として固定されない）。
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
    unresolved = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        data = _fetch_json(chunk, retries)
        # 取得できなかったバッチはキャッシュに書かない。書いてしまうと
        # 一時的な障害が「この ISBN に書影は無い」という確定結果として
        # 永続化され、以降そのバッチの ISBN は二度と再取得されなくなる
        # （再実行すれば未取得として拾い直せるようにしておく）。
        if data is None or len(data) != len(chunk):
            if data is not None:
                print(f"  [warn] OpenBD の応答件数が要求と一致しません"
                      f"（要求 {len(chunk)} / 応答 {len(data)}）。"
                      "このバッチはキャッシュしません。", file=sys.stderr)
            unresolved += len(chunk)
            time.sleep(interval)
            continue
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

    if unresolved:
        print(f"  [warn] OpenBD: {unresolved} ISBN を取得できませんでした"
              "（キャッシュ未作成。再実行で再試行されます）", file=sys.stderr)

    filled = 0
    for isbn, recs in targets.items():
        cover = (cache_mem.get(isbn) or {}).get("coverUrl") or None
        for r in recs:
            r["coverUrl"] = cover
            if cover:
                filled += 1
    return filled
