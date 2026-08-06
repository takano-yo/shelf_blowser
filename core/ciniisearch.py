"""core/ciniisearch.py — CiNii OpenSearch の取得。

動的検索は「検索語 → OpenSearch 取得 → normalize → 整列 → books 配列」で、
その取得段だけをここに閉じ込める。

  - fetch_response() … 生レスポンス全体（totalResults・dc:date 等のメタ込み）。
  - fetch_live()     … items 配列だけを返す（`@graph[0].items`）。

CiNii へ到達できない環境の代役は、事前取得済みの静的な棚データ
（`site/data/ndc/<記号>.json`）を読む server 側が担う。

標準ライブラリのみ。ネットワーク I/O はこのモジュールに集約する。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OPENSEARCH_ENDPOINT = "https://ci.nii.ac.jp/books/opensearch/search"

# appid（CiNii Books OpenSearch の必須パラメータ）と User-Agent は
# 実行環境の環境変数から読む。
APPID_ENV = "CINII_APPID"
USER_AGENT_ENV = "SHELF_BLOWSER_UA"
DEFAULT_USER_AGENT = "shelf_blowser/1.0"

# 1 コールあたりの取得件数の既定。公式仕様の count は「デフォルト 20」とだけ
# 定められていて上限の記載が無いため、1 コールで数千〜1 万件返る挙動は仕様上
# 保証されていない。棚データの保存上限（build --ndc-max = 1000）に合わせた値を
# 既定とし、大きな値は呼び出し側が明示する。
# CiNii Research 移行時は 200 件/コール上限になるため、`p`（ページ番号）による
# ページングへ切り替える（core/README.md の移行手順）。
DEFAULT_COUNT = 1000


def user_agent():
    """リクエストに付ける User-Agent（環境変数で上書きできる）。"""
    return (os.environ.get(USER_AGENT_ENV) or "").strip() or DEFAULT_USER_AGENT


def appid():
    """CiNii OpenSearch の appid。未設定なら空文字を返す。"""
    return (os.environ.get(APPID_ENV) or "").strip()


_warned_missing_appid = False


def _warn_missing_appid():
    """appid 未設定の警告を 1 プロセスにつき 1 回だけ出す。"""
    global _warned_missing_appid
    if not _warned_missing_appid:
        _warned_missing_appid = True
        print(f"  [warn] {APPID_ENV} が未設定です。appid は CiNii Books "
              "OpenSearch の必須パラメータで、未指定のアクセスは遮断されることが"
              "あります。", file=sys.stderr)


def build_opensearch_url(query=None, count=DEFAULT_COUNT, sortorder=5, clas=None):
    """CiNii OpenSearch のリクエスト URL を組み立てる。

    sortorder=5 は所蔵館数（ownerCount）降順。棚データの整列キーと揃える。
    count は 1 コールで取る件数（→ DEFAULT_COUNT の注記）。

    clas は NDC 分類検索（例: "913*"）。上位桁の前方一致は末尾 `*` で指定する
    （source/0.json ＝ clas=0* の実レスポンスで動作確認済み）。query と clas は
    どちらか一方だけでもよい。

    appid は仕様上の必須パラメータなので、設定されていれば必ず付ける
    （未設定のときは警告を出したうえで、従来どおり appid 無しで組み立てる）。
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
    aid = appid()
    if aid:
        params["appid"] = aid
    else:
        _warn_missing_appid()
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


def retry_after_seconds(err):
    """HTTP 応答の Retry-After（秒数 or HTTP-date）を秒で返す。読めなければ None。"""
    headers = getattr(err, "headers", None)
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return max(0, int((when - _dt.datetime.now(_dt.timezone.utc)).total_seconds()))


def fetch_response(query=None, count=DEFAULT_COUNT, sortorder=5, clas=None,
                   timeout=30, retries=4):
    """CiNii OpenSearch を取得して生レスポンス全体（dict）を返す。

    NDC バッチ（fetch/ndc_fetch.py）のように totalResults・dc:date 等のメタが
    必要な経路はこちらを使う。マナー: 明示的 UA と appid を付け、一時エラーは
    指数バックオフで再試行する。

    再試行は「叩き直せば結果が変わりうるエラー」に限る。4xx は叩き直しても
    同じ結果になるため即座に送出する。とくに 403（Access Limit Over）は
    遮断されている状態なので、そこへ叩き足さないことがマナー上重要
    （バッチ側は失敗として記録し、再実行時に失敗分だけ再試行する）。
    429 のみ Retry-After に従って待ってから再試行する。
    """
    url = build_opensearch_url(query, count=count, sortorder=sortorder, clas=clas)
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                raise  # 再試行しても変わらない（403 の遮断中は特に叩かない）
            if attempt == retries - 1:
                raise
            wait = retry_after_seconds(e) or delay
            time.sleep(wait)
            delay = max(delay * 2, wait)
        except Exception:  # noqa: BLE001 — 5xx・ネットワーク・JSON 破損は再試行
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    return {}


def fetch_live(query, count=DEFAULT_COUNT, sortorder=5, timeout=30, retries=4,
               clas=None):
    """CiNii OpenSearch を実際に取得して items 配列を返す（本番経路）。

    clas は NDC 分類検索（例: "913*"）。query と併用すると「その分類 かつ その語」の
    複合クエリになる（server の分類内検索が使う）。
    """
    data = fetch_response(query, count=count, sortorder=sortorder, clas=clas,
                          timeout=timeout, retries=retries)
    return items_from_response(data)
