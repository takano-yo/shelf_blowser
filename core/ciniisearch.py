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


def build_opensearch_url(query=None, count=10000, sortorder=5, clas=None):
    """CiNii OpenSearch のリクエスト URL を組み立てる。

    sortorder=5 は所蔵館数（ownerCount）降順。source/*.json と同じ条件に揃える。
    count も source/*.json の生成時と同じ 10000（CiNii が実際に持つ件数までしか
    返らないため、実質「その検索語の全件」を一度の取得で狙う値）に揃える。

    clas は NDC 分類検索（例: "913*"）。上位桁の前方一致は末尾 `*` で指定する
    （source/0.json ＝ clas=0* の実レスポンスで動作確認済み）。query と clas は
    どちらか一方だけでもよい。
    """
    params = {}
    if query:
        params["q"] = query
    if clas:
        params["clas"] = clas
    params.update({
        "format": "json",
        "count": count,
        "sortorder": sortorder,
        "type": 1,
        "gmd": "_",
    })
    # safe="*": 前方一致の * を実証済みの URL 形（clas=0*）のまま送る
    return OPENSEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params, safe="*")


def items_from_response(data):
    """OpenSearch レスポンス（dict）から items 配列を取り出す。欠損時は []。"""
    try:
        graph = data["@graph"][0]
    except (KeyError, IndexError, TypeError):
        return []
    return graph.get("items") or []


def total_results(data):
    """OpenSearch レスポンス（dict）から総件数を int で返す。欠損時は None。

    レスポンスの opensearch:totalResults は文字列（例 "230095"）で入る。
    """
    try:
        return int(data["@graph"][0]["opensearch:totalResults"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def fetched_at(data):
    """OpenSearch レスポンス（dict）から生成日時（dc:date）を返す。欠損時は None。"""
    try:
        value = data["@graph"][0].get("dc:date")
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) else None


def fetch_response(query=None, count=10000, sortorder=5, clas=None,
                   timeout=30, retries=4):
    """CiNii OpenSearch を取得して生レスポンス全体（dict）を返す。

    NDC バッチ（fetch/ndc_fetch.py）のように totalResults・dc:date 等のメタが
    必要な経路はこちらを使う。マナー: 明示的 UA を付け、一時エラーは指数
    バックオフで再試行する。
    """
    url = build_opensearch_url(query, count=count, sortorder=sortorder, clas=clas)
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 — リトライ対象を広く取る
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return {}


def fetch_live(query, count=10000, sortorder=5, timeout=30, retries=4, clas=None):
    """CiNii OpenSearch を実際に取得して items 配列を返す（本番経路）。

    clas は NDC 分類検索（例: "913*"）。query と併用すると「その分類 かつ その語」の
    複合クエリになる（server の分類内検索が使う）。
    """
    data = fetch_response(query, count=count, sortorder=sortorder, clas=clas,
                          timeout=timeout, retries=retries)
    return items_from_response(data)


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


def search_local(source_path, query, count=10000):
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
