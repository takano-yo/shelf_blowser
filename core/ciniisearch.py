"""core/ciniisearch.py — CiNii OpenSearch の取得（ライブ／ローカル代役）。

動的検索は「検索語 → OpenSearch 取得 → normalize → 整列 → books 配列」で、
その取得段だけをここに閉じ込める。取得元は 2 系統あり、どちらも
`items_from_response()` が受け取る同一構造（`@graph[0].items`）を返す:

  1) fetch_live()   … CiNii OpenSearch API を実際に叩く（本番）。
  2) search_local() … 保存済み OpenSearch JSON をキーワードで絞り込む（オフライン
                       検証・フォールバック用）。source/*.json は CiNii の実
                       レスポンスそのものなので、正規化結果はライブと同一になる。

標準ライブラリのみ。ネットワーク・ファイル I/O はこのモジュールに集約する。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

OPENSEARCH_ENDPOINT = "https://ci.nii.ac.jp/books/opensearch/search"
# 連絡先を含む明示的な UA（CiNii へのマナー）。運用時は連絡先を差し替える。
USER_AGENT = "shelf_blowser/0.1 (+https://github.com/takano-yo/shelf_blowser)"


def build_opensearch_url(query, count=200, sortorder=5):
    """CiNii OpenSearch のリクエスト URL を組み立てる。

    sortorder=5 は所蔵館数（ownerCount）降順。source/*.json と同じ条件に揃える。
    count は動的検索では控えめ（既定 200）にし、ペイロードとサーバ負荷を抑える。
    """
    params = {
        "q": query,
        "format": "json",
        "count": count,
        "sortorder": sortorder,
        "type": 1,
        "gmd": "_",
    }
    return OPENSEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def items_from_response(data):
    """OpenSearch レスポンス（dict）から items 配列を取り出す。欠損時は []。"""
    try:
        graph = data["@graph"][0]
    except (KeyError, IndexError, TypeError):
        return []
    return graph.get("items") or []


def fetch_live(query, count=200, sortorder=5, timeout=30, retries=4):
    """CiNii OpenSearch を実際に取得して items 配列を返す（本番経路）。

    マナー: 明示的 UA を付け、一時エラーは指数バックオフで再試行する。
    """
    url = build_opensearch_url(query, count=count, sortorder=sortorder)
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return items_from_response(data)
        except Exception:  # noqa: BLE001 — リトライ対象を広く取る
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return []


# --- オフライン検証・フォールバック用のローカル絞り込み ---

def _item_haystack(item):
    """絞り込み対象の文字列（タイトル・著者・出版者）を連結して返す。"""
    parts = [item.get("title") or "", item.get("dc:creator") or ""]
    pub = item.get("dc:publisher")
    if isinstance(pub, list):
        parts.extend(pub)
    elif pub:
        parts.append(pub)
    return " ".join(parts)


def search_local(source_path, query, count=200):
    """保存済み OpenSearch JSON を読み、query（空白区切り AND）で items を絞り込む。

    CiNii に到達できない環境での動的パイプライン検証・フォールバック用。
    query が空なら全件（先頭 count 件）を返す。CiNii の一覧は所蔵館数降順で
    保存されているため、この順序をそのまま活かす（normalize 側でも再整列する）。
    """
    data = json.loads(Path(source_path).read_text(encoding="utf-8"))
    items = items_from_response(data)
    terms = [t for t in (query or "").split() if t]
    if terms:
        matched = [
            it for it in items
            if all(t in _item_haystack(it) for t in terms)
        ]
    else:
        matched = items
    return matched[:count]
