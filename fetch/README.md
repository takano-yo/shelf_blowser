# ① fetch — 外部 API からのバッチ取得

> **役割: 表示用データの元になる外部 API のバッチ取得を担う。** 2 系統がある。
>
> - **A. NDC 分類ごとの一覧取得（計画・P2 #5）** … スタートページの NDC 分類ナビ
>   （[docs/site-structure.md](../docs/site-structure.md)）が使う分類ごとの棚データを、
>   CiNii の分類検索でまとめて取得する。→ 本書「A. NDC 分類ごとの一覧取得」
> - **B. 一件ごとの詳細書誌の取得（将来・P5 #17）** … 件名・典拠著者・分類を使う
>   詳細検索を有効化するときに、図書一件ごとの詳細 API を取得する。
>   **キーワード検索棚の表示には不要**（一覧 = OpenSearch のデータで足りる）。

---

## A. NDC 分類ごとの一覧取得（実装済み・P2 #5）

全体要件は [docs/site-structure.md](../docs/site-structure.md) を正とする。
バッチ本体 `ndc_fetch.py` は **実装済み（2026-07-13）**。

- **対象**: NDC の類 10・綱 100・目 1,000 ＝ **最大 1,110 分類**。
  1 分類あたり最大 10,000 件程度を想定（上限は件数実測後に確定）。
- **取得**: CiNii の分類検索（OpenSearch）で分類記号ごとに `clas=<記号>*`
  （前方一致）で一覧を取得する。**分類検索パラメータ・前方一致・所蔵館数降順
  （sortorder=5）は実レスポンス `source/0.json`（clas=0*）で確認済み**
  （docs/site-structure.md「問題点と対処」#1）。
  CiNii Research 移行（P1）後は新 API（appid・200 件/コールのページング）へ
  引き継ぐため、取得は `core.ciniisearch.fetch_response()` に集約してある
  （[core/README.md](../core/README.md) の方針どおり差し替え可能）。
- **CLI**:
  ```bash
  python fetch/ndc_fetch.py --counts --out .cache/ndc/   # 件数実測（totalResults のみ収集）
  python fetch/ndc_fetch.py --out .cache/ndc/            # 全 1,110 分類の一覧取得
  python fetch/ndc_fetch.py --codes 910,911              # 分類記号を指定
  python fetch/ndc_fetch.py --level 3 --limit 10         # 階層・件数を絞った動作テスト
  ```
  主なオプション: `--interval`（リクエスト間隔・既定 1 秒）・`--retries`（既定 4）・
  `--count`（一覧取得の 1 分類あたり件数・既定 10000）。
- **出力**: `.cache/ndc/<分類記号>.json`（生レスポンス・Git 管理外）。
  件数実測モードは `.cache/ndc/counts.json`（分類記号 → totalResults）。
  正規化と `site/data/ndc/` への出力は `build.py --ndc` が担う
  （[build/README.md](../build/README.md)）。
- **マナー（実装済み）**: 直列取得・リクエスト間隔（既定 1 秒）・連絡先入り
  User-Agent・指数バックオフ・取得済みスキップ（**冪等・中断再開可能**）・
  失敗分類の記録（`failed.txt`。再実行で失敗分のみ再試行）— 下記 B の設計方針と
  同一。`--codes`/`--level` により分割実行（例: 週ごとに 1/4）できる。
- **定期更新**: 当面は手動実行→コミット。再取得はキャッシュ（`.cache/ndc/`）を
  削除（または対象分類のみ削除）してから再実行する。GitHub Actions 化は CI/CD
  変更のためユーザーレビューを経て別途行う。

### 全分類の初期整備手順（ランブック）

CiNii（`ci.nii.ac.jp`）へ到達できる環境で実行する（実行環境によっては
egress ポリシーで CiNii がブロックされるため注意）。

1. **件数実測**: `python fetch/ndc_fetch.py --counts`
   （1,110 コール × 間隔 1 秒 ≈ 40 分。中断可・再実行で続きから）
2. **件数上限の確定**: `counts.json` から `Σ min(件数, 上限) × 419 バイト`で
   総量を試算し、リポジトリ 1GB 未満に収まる `--ndc-max` を決める
   （docs/site-structure.md「問題点と対処」#3。10,000 件のままでは最悪 4.6GB）。
3. **一覧取得**: `python fetch/ndc_fetch.py`
   （1,110 コール・レスポンス約 5MB/分類 ≈ 数時間。中断可・再実行で続きから。
   失敗分は `failed.txt` を確認して再実行）
4. **ビルド**: `python build/build.py --ndc --ndc-max <確定値>`
   → `site/data/ndc/<記号>.json` ＋ `index.json`
5. **検証**: もう一度ビルドして棚データがバイト一致（冪等）すること、
   `index.json` から各分類の件数（count）・取得日（fetchedAt）を確認できること。
6. **コミット**: `site/data/ndc/` をコミット（生キャッシュ `.cache/ndc/` は
   Git 管理外のまま）。分類名（label）は NDC Navi の利用許諾確認
   （docs/site-structure.md #2）が取れるまで null のままとする。

---

## B. 一件ごとの詳細書誌の取得（将来・P5 #17）

### 目的

`source/日本近代文学.json` の各 item が持つ `rdfs:seeAlso`（詳細レコード `.json` への
リンク）をたどり、CiNii Books の詳細書誌を **図書一件ごとに** 取得する。
一覧レベルには無い **件名・典拠著者・分類** はここで初めて取得でき、
これらは **件名・著者名を含めた詳細検索でのみ必要** になる。

### いつ使うか（本棚表示 vs 詳細検索）

- **本棚ページの表示**（キーワード検索棚・NDC 棚とも）… 一覧情報（OpenSearch）だけで
  完結するため、本系統（一件ごとの詳細取得）の出力は**不要**。
- **件名・著者名を含めた詳細検索** … 件名 `foaf:topic`・典拠著者 `foaf:maker`
  （著者 ID + 正規形）・分類 `dc:subject` は詳細レコードにしか無いため、
  本系統で取得した一件ごとの詳細 API 情報を利用する。

### 一覧レベルにあるもの / 詳細レコードにしかないもの

| 項目 | source（一覧） | 詳細レコード | 備考 |
|---|:---:|:---:|---|
| タイトル | ✓ | ✓ | |
| 著者（文字列）`dc:creator` | ✓ | ✓ | 「丸山真男著」のような表記 |
| **著者（典拠 ID + 正規形）** `foaf:maker` | ✗ | ✓ | 名寄せに必須。`著者ID` で同一人物を束ねられる |
| 出版者 `dc:publisher` | ✓ | ✓ | |
| 出版年 `dc:date` | ✓ | ✓ | |
| 所蔵館数 `cinii:ownerCount` | ✓ | ✓ | |
| シリーズ `dcterms:isPartOf` | ✓ | ✓ | |
| ISBN `dcterms:hasPart` | ✓ | ✓ | |
| **件名** `foaf:topic` | ✗ | ✓ | 例: 「日本文学 -- 歴史 -- 近代」 |
| **分類** `dc:subject` | ✗ | ✓ | 例: `NDC9:910.26`, `NDLC:KG381` |
| 所蔵館一覧 `bibo:owner` | ✗ | ✓ | 図書館名・OPAC リンク |

→ **本棚表示は ✓（一覧）の項目だけで足りる。** 件名・典拠著者・分類による
詳細検索を提供する場合に、本系統（一件ごとの詳細 API）が必須となる。

### 取得対象 URL の形

各 item の `rdfs:seeAlso["@id"]`（例: `https://ci.nii.ac.jp/ncid/BN01797559.json`）。
`@id`（`.../ncid/BN01797559`）に `.json` を付けた形でも同じ。

> **注（2026-07 時点）**: CiNii Books は CiNii Research への統合が進行中で、
> 並行運用終了（2027 年 3 月頃見込み）後は `ci.nii.ac.jp/ncid/...` へのアクセスが
> CiNii Research 側へリダイレクトされる。**実装前に、統合後の詳細レコード取得手段
> （CiNii Research の RDF/JSON-LD エンドポイント、統合で追加された所蔵検索
> OpenSearch など）を確認し、取得対象 URL を確定させること**（下記「実装の要件・手順」
> の手順 0）。件名・典拠著者・分類・所蔵館一覧が統合後も同粒度で取れるかの検証が
> 本モジュール実装の前提になる。

### 設計方針（実装時に確定する論点）

実装時は CiNii への**マナーを最優先**に、以下を満たす設計とする。

- **レート制限**: リクエスト間隔を空け、同時接続数を抑える（サーバ負荷をかけない）。
- **リトライ**: ネットワーク／一時エラーは指数バックオフで再試行。
- **キャッシュ・再開可能**: 取得済みレコードはローカルに保存し、再実行時はスキップ。
  途中中断しても続きから取得できるようにする。生データ（キャッシュ）は Git に含めない。
- **User-Agent**: 連絡先を含む明示的な UA を付ける。
- **利用規約の確認**: CiNii Books の API 利用条件・出典表示の要件を取得前に確認する。
- **入力**: `source/*.json` の item 一覧。
- **出力**: 詳細レコードのローカルキャッシュ（`build` モジュールが読み取る中間形式）。

### 想定インターフェース（暫定）

```
fetch/
├── README.md                 # 本書
├── (計画・P2) ndc_fetch.py    # A: NDC 分類ごとの一覧 → .cache/ndc/ へ取得
└── (将来・P5) fetch.py 等     # B: source の NCID 一覧 → 詳細 .json をキャッシュ取得
```

実行イメージ（将来）:

```
python fetch/fetch.py --source source/日本近代文学.json --out .cache/details/
```

### build との関係

`build` は次の入力系統を扱う。

- **既定データ（キーワード棚）用** … `source/日本近代文学.json`（一覧）のみで生成。
- **NDC 棚用（計画・P2）** … 本モジュール A が取得した `.cache/ndc/` を
  `build.py --ndc` が正規化して `site/data/ndc/` を生成する。
- **詳細検索用（将来・P5）** … 本系統 B が取得した一件ごとの詳細レコードを合流させ、
  件名・典拠著者・分類のファセット／索引を生成する。

したがって本系統 B の出力は、件名・著者名を含めた詳細検索の索引を作る段階で
`build` に入力される。本棚表示だけを動かす場合は B を実行しなくてよい。

---

### 実装の要件・手順（今後必要な作業）

優先度 P5（[ルート README のロードマップ](../README.md#今後必要な作業ロードマップ) #17）。
本系統 B の出力は**キーワード検索棚の分類順ソート・分類見出し**（site の #8）と
**件名・典拠著者・分類のファセット検索**（#19）の前提になる。

#### 手順 0 — 統合後のエンドポイント確認（実装前の必須調査）

- CiNii Research 統合後に詳細レコード（件名 `foaf:topic`・典拠著者 `foaf:maker`・
  分類 `dc:subject`・所蔵館一覧 `bibo:owner`）をどの API で取得できるかを確認し、
  URL の形・レスポンス構造・必要な認証（appid）を本書に記録する。
- 並行運用中に現行 `ci.nii.ac.jp/ncid/<NCID>.json` で取得したキャッシュが、
  統合後の形式と互換かどうかも判断する（非互換なら変換層を設ける）。

#### 手順 1 — fetch.py の実装

- **CLI**: `python fetch/fetch.py --source source/日本近代文学.json --out .cache/details/`
  - `--limit N`（動作テスト）・`--interval 秒`（既定 1.0）・`--retries N`（既定 4）。
- **入力**: `source/*.json` の item から NCID 一覧を作る。
- **出力**: `.cache/details/<NCID>.json`（1 件 1 ファイル・Git 管理外）。
- **マナー**（設計方針の具体化）:
  - 直列取得・リクエスト間隔は既定 1 秒（5,212 件 ≈ 90 分。並列化はしない）。
  - 連絡先を含む User-Agent、指数バックオフ（2s→4s→8s→16s）。
  - 取得済み NCID はスキップ（**冪等・中断後の再開可能**）。
  - 失敗 NCID は `failed.txt` 等に記録し、再実行で失敗分だけ再試行できるようにする。
- **進捗表示**: `n/total（取得/スキップ/失敗）` を定期的に標準エラーへ出す。

#### 手順 2 — build への合流

- `build.py --details .cache/details/` で詳細レコードを読み、`subjects`（件名）・
  `ndc`・`ndlc`（分類）・`authors`（典拠 ID + 正規形）を各レコードへ付与、
  またはファセット索引 `site/data/facets.json` を生成する
  （[build/README.md](../build/README.md) の将来要件どおり）。

#### 受け入れ条件（実装完了の定義）

1. 中断 → 再実行で取得済み分を再取得しない（キャッシュヒットのログで確認）。
2. 全件取得後、詳細レコード数が source の NCID 数と一致する（失敗分はリスト化）。
3. 件名・典拠著者・分類が `build` の入力として読める形式で保存されている。
4. リクエスト間隔・UA・リトライがコード上で確認できる（CiNii へのマナー）。
