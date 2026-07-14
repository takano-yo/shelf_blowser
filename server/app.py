#!/usr/bin/env python3
"""server/app.py — 動的検索の最小 API サーバ（検証用）。

役割は「検索語 → OpenSearch 取得 → core.normalize で正規化・整列 →
core.openbd で書影を一括付与 → books 配列を JSON で返す」だけ。返す JSON は
build が出力する site/data/books.json と同一スキーマ（レコード配列）で、
フロント（site/js/app.js）はこれをそのまま buildShelfItems() に渡せる
── 表示層を一切変えずに動的化できる、という設計の要。

提供するもの:
  GET /api/search?q=<キーワード>&count=<N>   … 動的検索結果（books 配列。coverUrl 付き）
  GET /api/search?q=<語>&ndc=<分類記号>      … NDC 分類内の検索（1〜3 桁。q 無しなら
                                               分類全体＝静的 site/data/ndc/<記号>.json
                                               と同等の結果を返す）
  それ以外のパス                              … site/ 配下の静的ファイル配信
                                               （同一オリジンなので CORS 不要）

取得元は 2 モード:
  - 既定（--source 指定 or ライブ失敗時）… ローカル OpenSearch JSON を絞り込む。
    ndc 指定時は静的な site/data/ndc/<記号>.json（正規化済み）を絞り込む
  - --live                                … CiNii OpenSearch を実際に叩く。
    ndc 指定時は分類検索（clas=<記号>*・前方一致）と語の複合クエリで取得する

検索結果は ISBN を代表に OpenBD API（core.openbd）へ一括問い合わせし coverUrl
を埋める（--no-covers で無効化可）。取得結果は build と同じ .cache/openbd/ に
ISBN 単位でキャッシュするため、build 済みの書影は再取得しない。

検索語ごとの結果（coverUrl 付与後）は cache/ に保存し、同じ語の再取得を避ける
（CiNii・OpenBD へのマナー・性能）。標準ライブラリのみ。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# リポジトリ直下を import パスへ追加し、core を共有モジュールとして読む。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import ciniisearch  # noqa: E402
from core.normalize import normalize_items  # noqa: E402
from core.openbd import enrich_covers  # noqa: E402

SITE_DIR = ROOT / "site"
NDC_DATA_DIR = SITE_DIR / "data" / "ndc"  # build --ndc の出力（分類ごとの棚データ）
CACHE_DIR = Path(__file__).resolve().parent / "cache"
OPENBD_CACHE_DIR = ROOT / ".cache" / "openbd"  # build と共有（ISBN 単位キャッシュ）
DEFAULT_SOURCE = ROOT / "source" / "日本近代文学.json"

# 起動オプション（main で確定）
CONFIG = {
    "live": False,          # True: CiNii を叩く / False: ローカル source を絞り込む
    "source": DEFAULT_SOURCE,
    "count": 10000,         # build（source/*.json 生成）と同じ既定値。検索語の全件取得を狙う
    "cache_ttl": 3600,      # 秒。キャッシュの有効期限（0 で無期限）
    "covers": True,         # 検索結果に OpenBD で書影 URL を一括付与するか
}


def _cache_path(query, count, ndc=None):
    key = hashlib.sha1(
        f"{CONFIG['live']}|{CONFIG['covers']}|{count}|{ndc or ''}|{query}".encode("utf-8")
    ).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _load_cache(path):
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    ttl = CONFIG["cache_ttl"]
    if ttl and (time.time() - payload.get("_ts", 0)) > ttl:
        return None
    return payload.get("books")


def _save_cache(path, books):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"_ts": time.time(), "books": books}, ensure_ascii=False),
        encoding="utf-8",
    )


def _book_haystack(book):
    """正規化済みレコードの検索対象文字列（タイトル・著者・出版社・シリーズ名）。

    サーバ未稼働時に site 側が行うクライアント側絞り込み
    （site/js/app.js の bookHaystack）と同じ対象・同じ思想。
    """
    parts = [book.get("title") or "", book.get("creatorRaw") or ""]
    parts.extend(book.get("creators") or [])
    parts.extend(book.get("publishers") or [])
    for s in book.get("series") or []:
        if s and s.get("title"):
            parts.append(s["title"])
    return " ".join(parts)


def _filter_books(books, query):
    """books 配列（正規化済み）を検索語（空白区切り AND・部分一致）で絞り込む。"""
    terms = [t for t in (query or "").split() if t]
    if not terms:
        return list(books)
    return [b for b in books if all(t in _book_haystack(b) for t in terms)]


def search_ndc_static(ndc, query):
    """静的な NDC 棚データ（site/data/ndc/<記号>.json）を読み、query で絞り込む。

    データは build --ndc の出力＝正規化・整列済み（books.json と同一スキーマ）
    なので、そのまま絞り込むだけでよい。書影も静的データの値のまま返す
    （＝静的ファイル配信と同等の結果。CiNii へ到達できない環境の代役）。
    """
    path = NDC_DATA_DIR / f"{ndc}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"NDC 分類データがありません: site/data/ndc/{ndc}.json"
        )
    books = json.loads(path.read_text(encoding="utf-8"))
    return _filter_books(books, query)


def search_books(query, count, ndc=None):
    """検索語（と NDC 分類）から books 配列を返す。キャッシュ→取得→正規化→整列の順。

    ndc あり（分類内検索）:
      - ライブ時は CiNii の分類検索（clas=<記号>*・上位桁の前方一致）と語の複合
        クエリで取得する。q 無しなら分類全体（所蔵館数降順の上位 count 件）。
      - ローカル時・ライブ失敗時は静的な site/data/ndc/<記号>.json を絞り込む。
    """
    cache_path = _cache_path(query, count, ndc)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached, "cache"

    items = None
    live_failed = False
    if CONFIG["live"]:
        try:
            items = ciniisearch.fetch_live(query or None, count=count,
                                           clas=f"{ndc}*" if ndc else None)
            source = "cinii"
        except Exception as e:  # noqa: BLE001 — ライブ失敗はローカルへフォールバック
            print(f"  [warn] CiNii 取得失敗、ローカルへフォールバック: {e}",
                  file=sys.stderr)
            live_failed = True

    if items is None:
        if ndc:
            # 静的 NDC データは正規化・整列済みなので、絞り込むだけで返せる。
            books = search_ndc_static(ndc, query)[:count]
            source = "ndc-local-fallback" if live_failed else "ndc-local"
            _save_cache(cache_path, books)
            return books, source
        items = ciniisearch.search_local(CONFIG["source"], query, count=count)
        source = "local-fallback" if live_failed else "local"

    books = normalize_items(items)  # ← build と同一の正規化・整列（core 共有）
    if CONFIG["covers"] and books:
        try:
            enrich_covers(books, OPENBD_CACHE_DIR)
        except Exception as e:  # noqa: BLE001 — 書影取得失敗は無視して検索結果は返す
            print(f"  [warn] OpenBD 書影取得失敗: {e}", file=sys.stderr)
    _save_cache(cache_path, books)
    return books, source


class Handler(BaseHTTPRequestHandler):
    server_version = "shelf_blowser-dev/0.1"

    def log_message(self, fmt, *args):  # 簡潔なアクセスログ
        sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 別オリジン配信（例: GitHub Pages のフロント）からも叩けるよう許可。
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            self._handle_search(parsed)
        else:
            self._serve_static(parsed.path)

    def _handle_search(self, parsed):
        qs = parse_qs(parsed.query)
        query = (qs.get("q", [""])[0]).strip()
        ndc = (qs.get("ndc", [""])[0]).strip()
        if ndc and not re.fullmatch(r"\d{1,3}", ndc):
            self._send_json(
                {"error": "ndc は 1〜3 桁の NDC 分類記号で指定してください"},
                status=400)
            return
        try:
            count = min(int(qs.get("count", [CONFIG["count"]])[0]), 10000)
        except ValueError:
            count = CONFIG["count"]
        try:
            books, source = search_books(query, count, ndc=ndc or None)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, status=404)
            return
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": str(e)}, status=502)
            return
        # フロントは配列をそのまま books として扱うため、本体は配列で返す。
        # 付随情報はヘッダで渡す（表示層の契約＝books.json 形状を崩さない）。
        body = json.dumps(books, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Result-Source", source)
        self.send_header("X-Result-Count", str(len(books)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        rel = path.lstrip("/") or "index.html"
        target = (SITE_DIR / rel).resolve()
        # ディレクトリトラバーサル防止（site/ の外は配信しない）。
        if SITE_DIR not in target.parents and target != SITE_DIR:
            self.send_error(403)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(404)
            return
        ctype = _content_type(target.suffix)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


def _content_type(suffix):
    return _CTYPES.get(suffix.lower(), "application/octet-stream")


def main(argv=None):
    p = argparse.ArgumentParser(description="動的検索の最小 API サーバ（検証用）")
    p.add_argument("--port", type=int, default=8000, help="待受ポート")
    p.add_argument("--host", default="127.0.0.1", help="待受ホスト")
    p.add_argument("--live", action="store_true",
                   help="CiNii OpenSearch を実際に叩く（既定はローカル source 絞り込み）")
    p.add_argument("--source", default=str(DEFAULT_SOURCE),
                   help="ローカル取得に使う OpenSearch JSON")
    p.add_argument("--count", type=int, default=10000, help="1 検索あたりの既定取得件数")
    p.add_argument("--cache-ttl", type=int, default=3600,
                   help="結果キャッシュの有効期限（秒・0 で無期限）")
    p.add_argument("--no-covers", action="store_true",
                   help="検索結果への OpenBD 書影取得を無効化する")
    args = p.parse_args(argv)

    CONFIG["live"] = args.live
    CONFIG["source"] = args.source
    CONFIG["count"] = args.count
    CONFIG["cache_ttl"] = args.cache_ttl
    CONFIG["covers"] = not args.no_covers

    mode = "CiNii ライブ" if args.live else f"ローカル source（{args.source}）"
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"shelf_blowser dev server: http://{args.host}:{args.port}/")
    print(f"  取得モード: {mode}")
    print(f"  API:   http://{args.host}:{args.port}/api/search?q=キーワード")
    print(f"  静的:  {SITE_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
