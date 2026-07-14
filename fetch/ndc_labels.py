#!/usr/bin/env python3
"""ndc_labels.py — NDC 分類名を JLA 公式の NDC9 版 Linked Data から抽出する。

日本図書館協会（JLA）は分類委員会のページで NDC 新訂 8 版・9 版のデータ
（NDC-LD。国立国会図書館との共同研究成果）を CC-BY で公開している:
  https://www.jla.or.jp/committees/bunrui/ndc-data/

本スクリプトは ndc9.zip（Turtle 形式）を取得し、スタートページの NDC 分類ナビが
使う 1,110 分類（1〜3 桁）の分類名（skos:prefLabel@ja）を抽出して
labels.json へ保存する。NDC9 に存在しない記号（欠番）は収録しない
（build.py --ndc が index.json で label: null にする）。

NDC Navi（大阪公立大学・NDC10 版）は第三者向けの利用規約が公開されておらず
転載には別途許諾が必要なため、分類名の一次ソースには JLA 公式 CC-BY データ
（NDC9 版)を用いる。NDC10 版の分類名は許諾が得られた場合に差し替える
（docs/site-structure.md「問題点と対処」#2）。

実行イメージ:
    python fetch/ndc_labels.py --out .cache/ndc/labels.json
    python build/build.py --ndc   # labels.json があれば index.json に分類名を収録
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

# リポジトリ直下を import パスへ追加し、core を共有モジュールとして読む。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.ciniisearch import USER_AGENT  # noqa: E402

NDC9_ZIP_URL = "https://www.jla.or.jp/wp/wp-content/uploads/2025/06/ndc9.zip"
SOURCE_INFO = {
    "name": "日本十進分類法 新訂9版データ（NDC-LD）",
    "edition": "NDC9",
    "publisher": "日本図書館協会",
    "url": "https://www.jla.or.jp/committees/bunrui/ndc-data/",
    "license": "CC BY",
}


def download_zip(url: str, dest: Path, timeout: int = 60) -> None:
    """ndc9.zip を取得してキャッシュする（取得済みならスキップ）。"""
    if dest.is_file():
        print(f"取得済みスキップ: {dest}", file=sys.stderr)
        return
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"取得: {url} -> {dest}（{len(data)/1e6:.1f} MB）", file=sys.stderr)


def extract_labels(ttl_text: str) -> dict:
    """Turtle テキストから 1〜3 桁分類の {記号: 分類名(@ja)} を抽出する。

    リソースブロック（`\n.\n` 区切り）ごとに skos:notation が 1〜3 桁の数字で
    あるものを対象とし、skos:prefLabel の @ja 値を分類名として採用する。
    """
    labels = {}
    for block in ttl_text.split("\n.\n"):
        mn = re.search(r'skos:notation\s+"(\d{1,3})"', block)
        if not mn:
            continue
        code = mn.group(1)
        mj = re.search(r'skos:prefLabel\s+"([^"]+)"@ja', block)
        if not mj:
            # まれに @ja が prefLabel の 2 番目以降に来る形（"…"@ja を広めに探す）
            mj = re.search(r'skos:prefLabel\s+[^;]*?"([^"]+)"@ja', block, re.S)
        if mj and code not in labels:
            labels[code] = mj.group(1)
    return labels


def main(argv=None):
    p = argparse.ArgumentParser(
        description="JLA 公式 NDC9 データ（CC-BY）から分類名 labels.json を生成")
    p.add_argument("--out", default=".cache/ndc/labels.json",
                   help="出力先（build.py --ndc が読む）")
    p.add_argument("--url", default=NDC9_ZIP_URL,
                   help="ndc9.zip の取得元 URL（提供場所変更時に上書き）")
    p.add_argument("--zip", default=None,
                   help="ダウンロード済み ndc9.zip のパス（オフライン実行用）")
    p.add_argument("--refresh", action="store_true",
                   help="labels.json が存在しても再生成する")
    args = p.parse_args(argv)

    out_path = Path(args.out)
    if out_path.is_file() and not args.refresh:
        print(f"生成済みスキップ: {out_path}（--refresh で再生成）", file=sys.stderr)
        return 0

    zip_path = Path(args.zip) if args.zip else out_path.parent / "ndc9.zip"
    if not args.zip:
        download_zip(args.url, zip_path)

    with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
        ttl_name = next(n for n in zf.namelist() if n.endswith(".ttl"))
        ttl_text = zf.read(ttl_name).decode("utf-8")

    labels = extract_labels(ttl_text)
    total = 10 + 100 + 1000
    payload = {
        "source": dict(SOURCE_INFO,
                       retrievedAt=_dt.datetime.now(_dt.timezone.utc)
                       .replace(microsecond=0).isoformat().replace("+00:00", "Z")),
        "labels": dict(sorted(labels.items(), key=lambda kv: (len(kv[0]), kv[0]))),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"分類名 {len(labels)}/{total} 件（欠番 {total - len(labels)}）"
          f" -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
