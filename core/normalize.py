"""core/normalize.py — OpenSearch item を books レコードへ正規化する純粋ロジック。

もとは build/build.py に内蔵していた正規化処理を、CLI バッチ（build）と
API サーバ（server）の双方から使えるよう共通化したもの。ネットワーク・ファイル
I/O を一切持たない純粋関数だけを置く（同じ入力から常に同じ出力＝冪等）。

入力の 1 item は CiNii OpenSearch のレスポンス `@graph[0].items` の 1 要素で、
保存済み JSON でもライブ取得でも構造は同一。したがってバッチでもオンデマンドでも
まったく同じ正規化結果になる。
"""

from __future__ import annotations

import re

# --- 著者正規化に使う役割語（末尾から除去する責任表示） ---
#
# 並び順ではなく `ROLE_WORDS`（下で長さ降順に確定させる）が優先順位を決める。
# 「短い役割語を含む長い役割語」（`翻訳` ⊃ `訳`、`責任編集` ⊃ `編集`）は必ず
# 長い方を先に判定しないと、役割語の一部だけが切り取られて残りが著者名に
# 混入する（`◯◯翻`・`◯◯責任`）。
#
# **単漢字は原則として足さない。** 実データでは人名・学校名・作品集名の末尾と
# 衝突するため（`祖田修`・`桐光学園中学校`・`森公任`・`小林責`・`崔章集`・
# `中平解`・`張説`・`爆笑問題`・`星野共`）。これらは既存の役割語で
# 「人名＋役割語」として正しく処理されるので、複合語の形で列挙する。
_ROLE_WORDS = [
    # 責任表示。`責任◯`（責任編集・責任翻訳・責任監修 …）は ROLE_PREFIXES が
    # 処理するので、ここには `◯責任` の語順と、単独の `責任` を置く。
    "編集責任", "校閲責任", "翻訳責任", "責任",
    # 監修・監訳系
    "解説監訳", "編集監訳", "編監訳", "監訳著", "監訳",
    "監修", "監著", "監閲", "監集", "監",
    # 共同（共＋役割）系。`共◯` の組み合わせは ROLE_PREFIXES が処理するので、
    # ここには単独で現れる `共著`（従来からの語）だけを置く。
    "共著",
    # 編集系
    "副主編", "主編", "編纂", "編訳", "編著", "編集", "編修", "編校",
    "編訂", "編解", "編輯", "編述", "編刊",
    # 校訂系
    "校訂", "校注", "校閲", "校釈", "校補", "校勘", "校点",
    "改訂", "補訂", "増訂", "修訂", "改編", "増補", "譯補", "刪補",
    # 注釈系
    "注釈", "註釈", "注解", "註解", "訳注", "訳註", "譯註", "補註",
    "増註", "補注", "評註", "評注", "増評", "訳解", "注校", "著校",
    "釈解", "解注", "訓点", "標点", "标点", "點校", "点校", "輯校",
    "校註", "標注", "評釈", "評解", "評校", "評訓", "註疏", "義疏",
    "疏", "註",
    # 翻訳系
    "翻訳", "訳編", "訳述", "訳補", "補訳", "抄訳", "選訳", "対訳",
    "分担", "翻案", "翻刻", "解読",
    "譯編", "譯解", "校譯", "注譯", "訳詩", "譯",
    # 撰・纂・集・輯系（漢籍・中国語資料に多い）
    "纂著", "纂述", "纂訂", "纂修", "纂集", "纂訳", "訳纂", "纂",
    "集解", "集注", "集校", "集説", "集述", "集撰",
    "撰集", "撰述", "収集", "彙輯", "補輯", "批選",
    "纂輯", "輯録", "集録", "彙編", "訂補", "輯", "選", "補", "評",
    "抄", "鈔",
    # 原著・原作系
    "原著", "原編著", "原編集", "原編", "原作", "原案",
    # 口述・講述・演説系
    "講述", "口述", "演述", "演説", "講説", "序説", "概説", "評説",
    # 制作・図版系
    "執筆", "写真", "撮影", "作画", "構成", "企画", "製作",
    # 解題・図解ほか
    "解題", "解説", "開題", "図解", "圖解", "閲",
    "校刊", "覆刊", "刊行", "刊",
    # 単独の役割語（従来どおり）
    "述", "著", "編", "訳", "注", "画", "撰",
]
# 長い役割語を必ず先に判定させる（重複は排除。同長は決定的順で安定させる）。
ROLE_WORDS = sorted(set(_ROLE_WORDS), key=lambda r: (-len(r), r))

# --- 役割語の直前に付いて役割を修飾する接頭辞 ---
#
# `共訳`・`共編`・`共譯`・`共訳註`・`共編訳著` … のように、これらは任意の役割語と
# 自由に結合するため、複合語を列挙すると組み合わせが際限なく増える。役割語を
# 除去した**直後に限って**接頭辞も取り除くことで、列挙せずにまとめて解決する。
#
# ここに置けるのは「役割語の直前という位置以外ではまず人名末尾に現れない」語だけ。
# `修`・`校`・`原` などは `田中修著`・`◯◯学校編`・`陳原著` のように人名・学校名の
# 一部になるため決して入れない（→ ROLE_WORDS 冒頭の注記）。
# なお `共` には人名の実例が 2 件だけある（`星野共`・`山岸共`）。役割語が続かない
# 単独の `星野共` は除去対象にならないので保たれるが、`山岸共共著` のように
# 役割語が続く場合は `山岸` になる（既知の割り切り）。
ROLE_PREFIXES = sorted({"責任", "分担", "副", "共"}, key=lambda r: (-len(r), r))
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
        # パート全体が役割語だけのもの（`◯◯編集/校閲` を "/" で分けた "校閲"、
        # `◯◯著/翻刻` の "翻刻" など）は人名ではないので、除去ループへ入れずに
        # 捨てる。ループに入れると複合役割語がその一部（"校閲"→"閲"）で削られ、
        # 残りかす（"校"）が人名として出てしまう。
        if name.rstrip(TRAIL_PUNCT) in ROLE_WORDS:
            continue
        # 末尾の役割語と連結記号を繰り返し除去（"校注・訳" 等にも対応）
        while True:
            new = name.rstrip(TRAIL_PUNCT)
            for role in ROLE_WORDS:
                if new.endswith(role) and len(new) > len(role):
                    new = new[: -len(role)]
                    # 役割語を除いた直後だけ、その役割を修飾する接頭辞も除く
                    # （"共訳註" の "共"、"責任編集" の "責任"）。役割語が
                    # 続かない単独の接頭辞（人名 "星野共"）には作用しない。
                    # "共責任編集" のように接頭辞が重なるので繰り返す。
                    while True:
                        for pre in ROLE_PREFIXES:
                            if new.endswith(pre) and len(new) > len(pre):
                                new = new[: -len(pre)]
                                break
                        else:
                            break
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
# 第 1 寄与者の役割が「著・共著・執筆・著者・原著」なら単著/共著、それ以外（編・訳・
# 校注・編著・編集委員 …）および著者表記なし（creatorRaw が空）は編集書とする。
# 「編著」を「著」と誤認しないよう、役割語は長いものから最長一致で取り出す。
# 「原著」は翻訳書の原著者（例: `ジュル・ヴェルヌ原著 ; 川島忠之助訳`）で、
# 役割としては著者そのものなので単著/共著に含める。一方「監著」「纂著」
# 「責任著」など監修・編纂に軸のある `◯著` は編集書のままとする。
PERSONAL_ROLES = {"著", "共著", "執筆", "著者", "原著"}
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


# 「ほか」「他」省略表記（＝著者多数の含意）の検出。これを含む寄与者表記は、
# 筆頭の役割が「著」系でも編集書(editorial)として扱う（多数著者のまとめ＝編集書）。
# 人名内の「他」（例「岡野他家夫」）を誤検出しないよう、"省略マーカー" として
# 機能する位置——括弧の内容が「ほか/他」で始まる・役割語や括弧の直前にある——
# のみを検出する。役割語は ROLE_DETECT を再利用する。
_OTHERS_ROLE = "|".join(ROLE_DETECT)
OTHERS_MARKER = re.compile(
    # 括弧（[]〔〕()）の内容が「ほか」で始まる: [ほか] 〔ほか〕 (ほか) [ほか著] [ほか講演]
    r"[〔\[(]\s*ほか[^〕\])]*[〕\])]"
    # 括弧内が「他」単独 or 「他＋役割語」のみ: [他] [他著]（人名内「他」は対象外）
    r"|[〔\[(]\s*他\s*(?:" + _OTHERS_ROLE + r")?\s*[〕\])]"
    # ベタ書きの「ほか」が役割語・括弧の直前: 名ほか著 / 名ほか[著]（[…ほか執筆] も）
    r"|ほか(?=\s*(?:" + _OTHERS_ROLE + r")|\s*[〔\[(])"
    # ベタ書きの「他」が役割語の直前のみ: 名他著 / 名他編（「他家夫」等は役割語でないため除外）
    r"|他(?=\s*(?:" + _OTHERS_ROLE + r"))"
)


def has_others_marker(group):
    """寄与者グループに『ほか/他（＝著者多数の省略表記）』が含まれるか。

    人名に偶々「他」「ほか」が含まれていても（例「岡野他家夫」）誤検出しない。
    """
    return bool(OTHERS_MARKER.search(group))


def _first_authorship_segment(group):
    """第 1 役割グループから『第 1 寄与者の著系役割が完結するまで』を切り出す。

    著系役割語（著・共著・執筆・著者）が最初に現れる位置までを第 1 寄与者の
    著者表記とみなす。これにより、第 1 寄与者が著で完結した後ろに続く別寄与者
    （`◯◯著, ◯◯ほか編` のように同一グループ内でも）の「ほか/他」を、第 1 寄与者
    の省略表記と取り違えない。著系役割が無ければグループ全体を返す。
    """
    roles = sorted(PERSONAL_ROLES, key=len, reverse=True)
    for i in range(len(group)):
        for role in roles:
            if group.startswith(role, i):
                return group[: i + len(role)]
    return group


def contrib_kind(raw):
    """第 1 寄与者の役割から本の種別を返す（site の棚分割に使う）。
    - "personal" : 単著/共著（第 1 寄与者が 著・共著・執筆・著者）
    - "editorial": 編集書（それ以外。編・訳・校注・編著・編集委員 … と著者表記なし。
                   および第 1 寄与者『自身』が「ほか/他」省略表記を含むもの）
    """
    if not raw or not raw.strip():
        return "editorial"
    first_group = ROLE_GROUP_SEP.split(raw.strip())[0]
    # 第 1 寄与者の役割が著系でなければ編集書。
    if _first_contributor_role(raw) not in PERSONAL_ROLES:
        return "editorial"
    # 第 1 寄与者が著系 → 原則 personal。ただし第 1 寄与者『自身』が「ほか/他」
    # （著者多数の省略）を含むなら editorial（例: `◯◯ほか著`）。著系役割が完結した
    # 後ろに続く別寄与者の「ほか/他」では editorial にしない
    # （例: `◯◯著 ; ◯◯ほか編` は personal）。
    seg = _first_authorship_segment(first_group)
    return "editorial" if has_others_marker(seg) else "personal"


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
    """OpenSearch の 1 item を books レコードへ変換する。"""
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


def normalize_items(items):
    """items 配列を正規化し、ownerCount 降順・同値 ncid 昇順で整列した配列を返す。

    build（バッチ）・server（オンデマンド）の双方でこの並び順を共通に使う。
    """
    records = [normalize_item(it) for it in items]
    records.sort(key=lambda r: (-r["ownerCount"], r["ncid"]))
    return records
