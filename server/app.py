#!/usr/bin/env python3
"""server/app.py — 動的検索の最小 API サーバ（検証用）。

役割は「検索語 → OpenSearch 取得 → core.normalize で正規化・整列 →
core.openbd で書影を一括付与 → books 配列を JSON で返す」だけ。返す JSON は
build が出力する site/data/books.json と同一スキーマ（レコード配列）で、
フロント（site/js/app.js）はこれをそのまま buildShelfItems() に渡せる
── 表示層を一切変えずに動的化できる、という設計の要。

提供するもの:
  GET /api/search?q=<キーワード>&count=<N>   … 動的検索結果（books 配列。coverUrl 付き）
  それ以外のパス                              … site/ 配下の静的ファイル配信
                                               （同一オリジンなので CORS 不要）

取得元は 2 モード:
  - 既定（--source 指定 or ライブ失敗時）… ローカル OpenSearch JSON を絞り込む
  - --live                                … CiNii OpenSearch を実際に叩く

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


def _cache_path(query, count):
    key = hashlib.sha1(
        f"{CONFIG['live']}|{CONFIG['covers']}|{count}|{query}".encode("utf-8")
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


def search_books(query, count):
    """検索語から books 配列を返す。キャッシュ→取得→正規化→整列の順。"""
    cache_path = _cache_path(query, count)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached, "cache"

    if CONFIG["live"]:
        try:
            items = ciniisearch.fetch_live(query, count=count)
            source = "cinii"
        except Exception as e:  # noqa: BLE001 — ライブ失敗はローカルへフォールバック
            print(f"  [warn] CiNii 取得失敗、ローカルへフォールバック: {e}",
                  file=sys.stderr)
            items = ciniisearch.search_local(CONFIG["source"], query, count=count)
            source = "local-fallback"
    else:
        items = ciniisearch.search_local(CONFIG["source"], query, count=count)
        source = "local"

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
        try:
            count = min(int(qs.get("count", [CONFIG["count"]])[0]), 10000)
        except ValueError:
            count = CONFIG["count"]
        try:
            books, source = search_books(query, count)
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
