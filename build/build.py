#!/usr/bin/env python3
"""build.py — source/日本近代文学.json から site/data/books.json を生成する。

build/README.md の要件定義に対応した実装。
処理フロー: load → normalize → (enrich: 表紙) → sort → write

標準ライブラリのみで動作する（表紙取得 --covers 時のみ urllib でネットワークを使う）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path

# --- 著者正規化に使う役割語（長いものから順に末尾除去する） ---
ROLE_WORDS = [
    "注釈", "訳注", "編訳", "解題", "編著", "共著", "編纂", "校訂",
    "監修", "校注", "校閲", "解説", "編集",
    "述", "著", "編", "訳", "注", "画", "撰",
]
# 著者の区切り（半角空白は姓名間にも現れるため区切りに使わない）。
# " . "（前後空白付きのピリオド）は CiNii の合冊（複数著作）区切り。
CREATOR_SEP = re.compile(r"[;；,，、/]|\s+\.\s+")
# 役割語の間に現れる連結記号・末尾の句読点（"校注・訳" の "・" など）
TRAIL_PUNCT = " 　・･·／/.．,，、;；"
# 角括弧注記（[ほか] / [著] など）を除去
BRACKET = re.compile(r"\[[^\]]*\]")
# 先頭の連続数字
LEADING_DIGITS = re.compile(r"^(\d+)")


def ncid_from_uri(uri: str) -> str:
    """URI の末尾セグメントを NCID として返す。"""
    return uri.rstrip("/").rsplit("/", 1)[-1]


def parse_year_decade(date_str):
    """dc:date から (year, decade) を求める。

    - 完全 4 桁  -> (year, decade)
    - 3 桁確定   -> (None, decade)   例 "197-" → 1970 年代
    - それ未満   -> (None, None)     例 "19--", "1---", 欠損
    """
    if not date_str:
        return None, None
    m = LEADING_DIGITS.match(date_str.strip())
    if not m:
        return None, None
    digits = m.group(1)
    if len(digits) >= 4:
        year = int(digits[:4])
        return year, year // 10 * 10
    if len(digits) == 3:
        return None, int(digits) * 10
    return None, None


def normalize_creators(raw):
    """dc:creator 文字列を人名配列へ軽く正規化する（暫定精度）。

    役割語・角括弧注記を除き、区切りで分割する。完璧な分割は目指さない。
    """
    if not raw:
        return []
    out = []
    for part in CREATOR_SEP.split(raw):
        name = BRACKET.sub("", part).strip()
        if not name:
            continue
        # 末尾の役割語と連結記号を繰り返し除去（"校注・訳" 等にも対応）
        while True:
            new = name.rstrip(TRAIL_PUNCT)
            for role in ROLE_WORDS:
                if new.endswith(role) and len(new) > len(role):
                    new = new[: -len(role)]
                    break
            if new == name:
                break
            name = new
        # 「ほか」だけ、あるいは省略表記は人名として残さない
        name = name.rstrip()
        if name in ("", "ほか"):
            continue
        if name.endswith("ほか"):
            name = name[:-2].strip()
        if name and name != "ほか" and name not in ROLE_WORDS:
            out.append(name)
    return out


# --- 寄与者の種別判定（単著/共著 ⇔ 編集書）に使う ---
# 第 1 寄与者の役割が「著・共著・執筆・著者」なら単著/共著、それ以外（編・訳・校注・
# 編著・編集委員 …）および著者表記なし（creatorRaw が空）は編集書とする。
# 「編著」を「著」と誤認しないよう、役割語は長いものから最長一致で取り出す。
PERSONAL_ROLES = {"著", "共著", "執筆", "著者"}
# 役割語の検出辞書（最長一致・決定的順）。著で終わるが単著でない「編著」等を優先一致。
ROLE_DETECT = sorted(
    set(ROLE_WORDS) | {"執筆", "著者", "編集委員", "責任編集", "編集協力"},
    key=lambda r: (-len(r), r),
)
# 役割グループ（別の役割の寄与者）の区切り。" ; " と " . "。
# 同一役割の共著者を並べる "," は区切らない（末尾にまとめて属性が付くため）。
ROLE_GROUP_SEP = re.compile(r"\s*;\s*|\s+\.\s+")
# 先頭に属性が来る表記（"[著者] 宮本正人" など）。
LEADING_ROLE = re.compile(r"^\[?\s*(著者|著|執筆|共著)\s*\]?[\s　]")


def _role_suffix(s):
    """文字列末尾の役割語を最長一致で返す（無ければ ""）。"""
    s = s.strip().rstrip(TRAIL_PUNCT)
    for role in ROLE_DETECT:
        if s.endswith(role):
            return role
    return ""


def _first_contributor_role(raw):
    """creatorRaw の第 1 寄与者の役割語を取り出す。
    複数名が "," で並びまとめて属性が付くケース・角括弧表記（[著]/[ほか編集]/
    [ほか]著）・先頭属性（[著者] 名）に対応する。"""
    group = ROLE_GROUP_SEP.split(raw.strip())[0].strip()
    m = LEADING_ROLE.match(group)
    if m:
        return m.group(1)
    s = group
    # 末尾の角括弧内に役割があれば優先（[著]/[ほか編集]）。
    # 「ほか」等のみの括弧は捨て、その外側の役割（[ほか]著）を探す。
    for _ in range(4):
        s = s.rstrip(TRAIL_PUNCT)
        mb = re.search(r"\[([^\]]*)\]$", s)
        if not mb:
            break
        role = _role_suffix(mb.group(1))
        if role:
            return role
        s = s[: mb.start()]
    return _role_suffix(s)


def contrib_kind(raw):
    """第 1 寄与者の役割から本の種別を返す（site の棚分割に使う）。
    - "personal" : 単著/共著（第 1 寄与者が 著・共著・執筆・著者）
    - "editorial": 編集書（それ以外。編・訳・校注・編著・編集委員 … と著者表記なし）
    """
    if not raw or not raw.strip():
        return "editorial"
    return "personal" if _first_contributor_role(raw) in PERSONAL_ROLES else "editorial"


def extract_isbn(has_part):
    """dcterms:hasPart から urn:isbn: のみを採用し接頭辞を除く（ISSN は除外）。"""
    isbns = []
    for el in has_part or []:
        uri = el.get("@id", "")
        if uri.startswith("urn:isbn:"):
            isbns.append(uri[len("urn:isbn:"):])
    return isbns


def extract_series(is_part_of):
    """dcterms:isPartOf を [{id, title}] へ。親 NCID を保持する。"""
    series = []
    for el in is_part_of or []:
        uri = el.get("@id", "")
        series.append({
            "id": ncid_from_uri(uri) if uri else None,
            "title": el.get("dc:title"),
        })
    return series


def normalize_item(item):
    """source の 1 item を books レコードへ変換する。"""
    uri = item.get("@id", "")
    ncid = ncid_from_uri(uri)
    link = item.get("link") or {}
    cinii_url = link.get("@id") or (
        f"https://ci.nii.ac.jp/ncid/{ncid}" if ncid else None
    )

    try:
        owner_count = int(item.get("cinii:ownerCount", "0"))
    except (TypeError, ValueError):
        owner_count = 0

    year, decade = parse_year_decade(item.get("dc:date"))
    raw_creator = item.get("dc:creator")

    return {
        "ncid": ncid,
        "title": (item.get("title") or "").strip(),
        "creators": normalize_creators(raw_creator),
        "creatorRaw": raw_creator,
        "contribKind": contrib_kind(raw_creator),
        "publishers": list(item.get("dc:publisher") or []),
        "year": year,
        "decade": decade,
        "ownerCount": owner_count,
        "series": extract_series(item.get("dcterms:isPartOf")),
        "isbn": extract_isbn(item.get("dcterms:hasPart")),
        "ciniiUrl": cinii_url,
        "coverUrl": None,
    }


# ---------------------------------------------------------------------------
# 段階 3: 表紙取得（OpenBD）。--covers 指定時のみ実行する。
# ---------------------------------------------------------------------------

OPENBD_API = "https://api.openbd.jp/v1/get"


def enrich_covers(records, cache_dir, batch=100, retries=4):
    """先頭 ISBN を代表に OpenBD で表紙 URL を引き、coverUrl を埋める。

    取得結果は cache_dir に ISBN 単位でキャッシュし、再実行時は再取得しない。
    ネットワークは本関数内に閉じ込める（標準ライブラリ urllib）。
    """
    import urllib.parse
    import urllib.request

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


def _http_get_json(url, retries):
    import urllib.request
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "shelf_blowser-build/0.1"}
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
    args = p.parse_args(argv)

    records, meta = build(
        args.source, args.out, covers=args.covers, cache=args.cache,
        pretty=args.pretty, limit=args.limit,
    )
    print(f"生成: {meta['total']} 件 -> {Path(args.out) / 'books.json'}")
    print(f"  ISBN 保有: {meta['withIsbn']} 件 / 表紙取得: {meta['withCover']} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
