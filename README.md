# shelf_blowser

CiNii Books API で取得した学術書の書誌データを **本棚のように表示** し、
図書館の書架で行うようなブラウジング（請求記号順に棚を眺め、隣接する関連書に
偶然出会う体験）を Web で再現する学術検索支援アプリ。

試作版として **日本近代文学** 分野を既定データとする。

> **データソース**: 本アプリは [CiNii Books](https://ci.nii.ac.jp/books/) が提供するデータを
> [クリエイティブ・コモンズ 表示 4.0 国際ライセンス（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/deed.ja)
> のもとで利用しています。表紙画像は [openBD](https://openbd.jp/) を利用しています。
>
> **外部サービスの動向（2026-07 時点）**: CiNii Books は
> [CiNii Research への統合](https://support.nii.ac.jp/ja/cir/cib_integration)が進行中
> （機能統合は 2026 年 1 月・3 月にリリース済み。並行運用は 2027 年 3 月頃までの見込みで、
> 終了後は CiNii Books へのアクセスが CiNii Research へリダイレクトされる）。
> また openBD は 2023 年 7 月に旧 API（v1）の提供終了が発表され、後継 API は収録範囲が
> 縮小している。どちらも本アプリの生命線であり、対応を
> [今後必要な作業（ロードマップ）](#今後必要な作業ロードマップ)の最優先項目とする。

---

## コンセプト

CiNii の通常検索では失われる「棚を眺めて歩く」ブラウジング体験を Web で再現する。

- **棚の並び順**: `cinii:ownerCount`（所蔵館数）の降順。所蔵が多い＝広く読まれている
  主要書ほど棚の手前（先頭）に並ぶ。
- **検索 = 棚の入れ替え**: 検索窓にキーワードを入れると、その語で CiNii を取得し直し、
  本棚を丸ごと作り直す。
- **入口はスタートページ**（計画・P2）: 検索語または NDC 分類（類目→綱目→細目の
  階層ボタン）で初期条件を指定してから本棚ページへ遷移する 2 ページ構成にする。
  分類名は [NDC Navi](https://ndcnavi.i.omu.ac.jp/#/list3)（大阪公立大学）を出典表示
  つきで引用する（利用条件の確認が前提）。
  要件定義は [docs/site-structure.md](docs/site-structure.md)。

---

## アーキテクチャ（静的既定 ＋ 動的検索のハイブリッド）

本アプリは **表示用データ（`books.json`）のスキーマを唯一の契約**とし、その
`books.json` を **複数の経路**で用意する。**表示層（`site/`）はどの経路から
来た `books.json` でもそのまま描画できる**（= 表示層を一切変えずに経路を足せる）。

### ページ構成（計画・P2）

サイトは **スタートページ（入口）→ トップページ（本棚ページ）** の 2 ページ構成へ
変更する。スタートページ（`site/index.html`・新規）で検索語または NDC 分類を指定し、
本棚ページ（`site/shelf.html`・現行 index.html を移設）が
`?q=<語>` / `?ndc=<分類記号>` を受け取って棚を作る。クエリなしは既定データの棚
（後方互換）。詳細は [docs/site-structure.md](docs/site-structure.md)。

### データ経路

```
【A. 静的な既定表示】オフラインの事前ビルド。サーバ無しでも本棚が見える。

  source/日本近代文学.json         ← CiNii OpenSearch の実レスポンス（保存済み）
        │
        ▼
  build/build.py (core を使用)     ← 正規化・ownerCount 降順・(任意)表紙付与
        │
        ▼
  site/data/books.json             ← 既定データ（動的検索が無い時のフォールバック）


【B. 動的な検索表示】検索語のたびに取得＆再処理する。

  検索窓に入力 → site/js/app.js が /api/search?q=語 を fetch
        │
        ▼
  server/app.py                    ← ① キャッシュ確認
        │  ② core.ciniisearch で CiNii OpenSearch を取得
        │     （CiNii へ到達できない環境では source をローカル絞り込み）
        │  ③ core.normalize で正規化・整列（build と同一ロジック）
        ▼
  books 配列（books.json と同一スキーマ）を JSON で返す
        │
        ▼
  site/js/app.js が buildShelfItems() に渡して本棚を再描画（表示層は無改修）


【C. NDC 分類の静的棚（計画・P2）】分類ごとの事前取得。サーバ無しでも分類棚が見える。

  fetch/ndc_fetch.py（バッチ）     ← CiNii の分類検索で最大 1,110 分類を取得・キャッシュ
        │
        ▼
  build/build.py --ndc (core を使用) ← 正規化・整列（A と同一ロジック）
        │
        ▼
  site/data/ndc/<記号>.json ＋ index.json ← 本棚ページが ?ndc=<記号> で読み込む。
                                       リポジトリに保存し定期更新（詳細は docs/site-structure.md）
```

**設計の要点**: `build`（バッチ）と `server`（オンデマンド）は、正規化・整列の
中核ロジックを `core/` として**共有**する。同じ OpenSearch レスポンスからは
どちらの経路でも**同一の `books.json`** が得られるため、二重実装が無く、
表示結果も一致する。

### モジュール構成

| モジュール | 役割 | 使うデータ |
|---|---|---|
| [`core/`](core/README.md) | 正規化・整列・CiNii 取得の中核ロジック（`build`/`server` 共有） | OpenSearch レスポンス |
| [`build/`](build/README.md) | 既定データの事前ビルド（バッチ）。`site/data/*.json` を生成 | `source/*.json` ＋（任意）OpenBD |
| [`server/`](server/README.md) | 動的検索の API。`/api/search` を提供し `site/` も配信 | CiNii OpenSearch（ライブ）／`source`（代役） |
| [`site/`](site/README.md) | スタートページ（計画）＋本棚 UI・検索窓・詳細オーバーレイ（バニラ JS・ビルド工程なし） | `books.json`（静的 or API）・`ndc/*.json`（計画） |
| [`fetch/`](fetch/README.md) | （バッチ）NDC 分類ごとの一覧取得（計画・P2）／詳細書誌の取得（将来） | 分類検索 OpenSearch・一件ごとの詳細 API |

### ディレクトリ

```
shelf_blowser/
├── core/                     # 共有ロジック（標準ライブラリのみ）
│   ├── normalize.py          #   OpenSearch item → books レコード（正規化・整列）
│   └── ciniisearch.py        #   CiNii OpenSearch 取得（ライブ／ローカル代役）
├── build/
│   └── build.py              # 既定データの事前ビルド（core を使用）。--ndc は計画
├── server/
│   └── app.py                # 動的検索 API ＋ 静的配信（core を使用）
├── source/日本近代文学.json    # 既定キーワードの保存済み OpenSearch レスポンス
├── site/                     # 表示層（静的サイト）
│   ├── index.html            #   現状は本棚。P2 でスタートページ化（本棚は shelf.html へ移設）
│   ├── css/styles.css
│   ├── js/app.js             #   初期＝静的 books.json / 検索＝API。どちらも同一処理
│   └── data/
│       ├── books.json        #   build の出力（既定表示・フォールバック）
│       └── ndc/              #   （計画・P2）NDC 分類ごとの棚データ＋マスタ index.json
├── fetch/                    # バッチ取得（計画: NDC 分類ごとの一覧／将来: 詳細書誌）
├── docs/                     # 横断的な要件・レビュー（site-structure.md が最新のページ構成方針）
└── .github/workflows/pages.yml  # site/ を GitHub Pages へ配信
```

---

## 表示用データのスキーマ（契約）

`books.json` は **レコードの配列**（`ownerCount` 降順）。静的経路・動的経路の
どちらもこの形で `site` へ渡す。1 レコードの例:

```jsonc
{
  "ncid": "BN01797559",
  "title": "日本の思想",
  "creators": ["丸山真男"],          // dc:creator を正規化（暫定精度）
  "creatorRaw": "丸山真男著",         // 原文も保持
  "contribKind": "personal",          // "personal"=単著/共著 / "editorial"=編集書
  "publishers": ["岩波書店"],
  "year": 1961,                       // 完全 4 桁のみ。不明は null
  "decade": 1960,                     // year//10*10。導出不能は null
  "ownerCount": 560,                  // 整列キー（int）
  "series": [{ "id": "BN00076234", "title": "岩波新書, 青版-434, C39" }],
  "isbn": ["400412039X"],             // urn 除去・ISSN 除外。複数可
  "ciniiUrl": "https://ci.nii.ac.jp/ncid/BN01797559",
  "coverUrl": null                    // OpenBD 取得。無ければ null
}
```

各フィールドの抽出・正規化規則の詳細は [`build/README.md`](build/README.md) を参照。

---

## ローカルでの実行

### 1. 既定データの静的表示のみ（サーバ不要）

`site/` を任意の静的サーバで開くだけ。`build` を再実行してデータを作り直すには:

```bash
# 表紙取得なし（オフラインで完結・全件 coverUrl:null）
python build/build.py --source source/日本近代文学.json --out site/data/

# OpenBD で表紙も取得
python build/build.py --source source/日本近代文学.json --out site/data/ --covers
```

### 2. 動的検索を含めて動かす（API サーバ）

```bash
# 既定: ローカル source を検索語で絞り込む（CiNii に到達できない環境でも動く）
python server/app.py --port 8000
#   → http://127.0.0.1:8000/ をブラウザで開き、検索窓に語を入れる

# 本番: CiNii OpenSearch を実際に叩く
python server/app.py --port 8000 --live
```

`server/app.py` は `site/` を同一オリジンで配信しつつ `/api/search?q=語` を提供する。
検索結果は検索語ごとに `server/cache/` へキャッシュし、同じ語の再取得を避ける
（Git 管理外・冪等・再生成可能）。

> **注**: 初期ロードは常に静的な `site/data/books.json` を表示するため、API サーバが
> 無くても（または落ちても）既定の本棚は見える（グレースフルデグレード）。検索した
> ときだけ API を叩く。

---

## バックエンドのハード的構成と技術的制約

動的検索で新たに必要になるのは「検索語が決まった時点で CiNii 取得＋正規化を実行する
場所」だけで、その処理自体は極めて軽量。

### 計算負荷（ノートPCで十分）

| 処理 | コスト |
|---|---|
| CiNii OpenSearch 取得（1 検索＝1 コール） | 数百 ms〜数秒（**大半は外部 API の応答待ち**） |
| 正規化＋整列（数千件規模） | 1 秒未満（`build` の 2 回目実行が約 0.5 秒の実績） |
| メモリ | 数十 MB 程度 |

→ **ボトルネックは自マシンの性能ではなく、外部 API の応答時間とレート制限**。
一般的なノートPC 1 台で個人〜小規模なら問題ない。

### 技術的制約と対処

- **CORS**: ブラウザから CiNii を直接叩くのは CORS で不可の可能性が高いため、
  中継（`server/`）を挟む。自 API 側は `Access-Control-Allow-Origin` を返すので
  ブラウザ→自 API は通る。
- **CiNii へのマナー / レート制限**: 検索のたびに叩くため、①送信時のみ発火
  （1 打鍵ごとに叩かない）②検索語ごとのキャッシュ ③明示的 User-Agent ④指数
  バックオフ、を守る。
- **CiNii Research 移行後の取得件数**: 新 API（CiNii Research OpenSearch）は
  **appid（アプリケーション ID）必須・1 リクエスト 200 件が上限**のため、現行の
  「count=10000 の 1 コールで全件」という前提が崩れる。`start` ページングによる
  複数コール化と取得上限の見直しが必要（→ [core/README.md](core/README.md)）。
  なお新 API はレスポンスに CORS ヘッダを付けるため、中継サーバの必要性自体も
  再検討の余地がある（ただし appid のクライアント露出に注意）。
- **ペイロード**: `count` に上限を設け、キャッシュ併用でモバイルでも軽く保つ。
- **詳細検索（件名・著者・分類）は動的化しない**: 一件ごとの詳細 API は「1 件＝1 コール ×
  多数」で検索のたびには重すぎる。**動的化するのは OpenSearch（一覧＝本棚）だけ**とし、
  件名・典拠著者・分類は引き続き `fetch`（バッチ）で作る（将来）。

### 公開（到達性）

計算力ではなく「インターネットからの到達性」が運用上の論点。

| 方式 | 常時起動 | 難点 |
|---|---|---|
| ノートPC ＋ トンネル（cloudflared/ngrok） | 必要 | 回線・動的 IP・PC 常時起動・自宅 NW の露出 |
| サーバレス/PaaS（Python 対応の無料枠等） | 不要 | コールドスタート。Python 実行環境の確認 |
| 個人利用に割り切る | — | `localhost` で自分だけ動的検索、公開は静的版 |

**推奨**: まずローカルで検証 → 公開は Python 対応のサーバレス/PaaS の無料枠に置く。
フロント（`site/`）は従来どおり GitHub Pages（静的）に置き、検索時だけ別ホストの
API を叩く構成にもできる（`site/js/app.js` の `SEARCH_URL` を差し替えるだけ）。

---

## 公開（GitHub Pages）

`site/` 配下を GitHub Pages の公開ルートにする（[`.github/workflows/pages.yml`](.github/workflows/pages.yml)）。
すべて相対パスで参照し、サブパス公開（`/<repo>/`）でも動く。Pages は静的配信のため、
**動的検索を公開する場合は API（`server/`）を別途どこかにホストする**必要がある。

---

## 今後必要な作業（ロードマップ）

外部リソースの調査（NII のサービス統合告知・openBD の提供終了告知・仮想書架の先行例
＝ Harvard [StackLife](https://lil.law.harvard.edu/our-work/stack-life/) /
Stanford [SearchWorks](https://searchworks.stanford.edu/) の virtual browse /
東京都立図書館 Digital BookShelf など）と現状コードの点検にもとづき、
今後の作業を優先度順に整理する。各項目の**要件・手順は各モジュールの README と
[docs/](docs/site-structure.md)** に定義した。

### P1 — サービス存続に関わる（最優先）

| # | 作業 | 要件定義の場所 |
|---|---|---|
| 1 | **CiNii Research API への移行**（取得層の差し替え・appid・ページング） | [core/README.md](core/README.md) |
| 2 | **書影取得のフォールバック整備**（openBD 後継 API ＋ NDL サムネイル API） | [build/README.md](build/README.md) |

背景: CiNii Books の並行運用終了（2027 年 3 月頃見込み）後、現行の
CiNii Books OpenSearch は使えなくなる前提で移行する。書影は openBD の収録範囲縮小
（現状の書影取得率 4.2% の一因）を国立国会図書館サーチのサムネイル API で補う。

### P2 — サイト構成の刷新（スタートページ＋NDC 分類ナビ）

**全体の要件定義は [docs/site-structure.md](docs/site-structure.md)**（最新のページ構成方針）。
実装順・問題点と対処も同書に定義する。

| # | 作業 | 要件定義の場所 |
|---|---|---|
| 3 | 事前調査（分類検索 API の確認・NDC 分類名の利用許諾・全分類の件数実測） | [docs/site-structure.md](docs/site-structure.md)・[core/README.md](core/README.md) |
| 4 | NDC マスタ整備（最大 1,110 分類の記号・分類名〈NDC Navi 引用〉・出典 meta） | [docs/site-structure.md](docs/site-structure.md)・[build/README.md](build/README.md) |
| 5 | NDC ごとの棚データ取得バッチと定期更新（既定データの定期更新もここへ統合） | [fetch/README.md](fetch/README.md)・[build/README.md](build/README.md) |
| 6 | スタートページ（検索窓＋NDC 階層ボタン・出典表示） | [site/README.md](site/README.md) |
| 7 | 本棚ページの初期条件対応（`?q=`/`?ndc=`）と URL 状態同期 | [site/README.md](site/README.md) |
| 8 | NDC 分類内の動的検索（`/api/search` の `ndc` パラメータ・静的フォールバック） | [server/README.md](server/README.md) |

### P3 — 品質基盤

| # | 作業 | 要件定義の場所 |
|---|---|---|
| 9 | 正規化ロジックのユニットテストと CI（回帰防止） | [core/README.md](core/README.md) |
| 10 | server の運用堅牢化（gzip・キャッシュ上限・レート制限・入力検証） | [server/README.md](server/README.md) |
| 11 | build の整列処理の重複解消（`core.normalize_items` へ一本化） | [build/README.md](build/README.md) |
| 12 | LICENSE の追加（下記） | 本書 |

### P4 — UX 改善（仮想書架の先行例に学ぶ）

| # | 作業 | 要件定義の場所 |
|---|---|---|
| 13 | 検索リクエストの競合対策（AbortController） | [site/README.md](site/README.md) |
| 14 | ピボットブラウジング（著者・出版社・シリーズのクリックで棚を再生成） | [site/README.md](site/README.md) |
| 15 | 詳細オーバーレイに「棚の近傍」表示（virtual browse） | [site/README.md](site/README.md) |
| 16 | アクセシビリティ強化（タブの矢印キー移動・フォーカストラップ） | [site/README.md](site/README.md) |

> 旧項目「URL 状態同期」は P2 #7（本棚ページの初期条件対応）へ統合した。

### P5 — 保留中の将来機能

| # | 作業 | 要件定義の場所 |
|---|---|---|
| 17 | fetch（一件ごとの詳細書誌のバッチ取得）の実装 | [fetch/README.md](fetch/README.md) |
| 18 | キーワード検索棚の分類順ソート・分類見出し（fetch 整備後） | [site/README.md](site/README.md)・[fetch/README.md](fetch/README.md) |
| 19 | 件名・典拠著者・分類のファセット検索 | [build/README.md](build/README.md)・[site/README.md](site/README.md) |

> 旧項目「NDC 分類順の棚」は、分類ごとの棚そのものを P2（分類検索による事前取得）で
> 実現する方針に変更した。キーワード検索棚の中を NDC 順に並べる機能（#18）のみ、
> 一件ごとの詳細取得（#17）を前提とする将来機能として残す。

### LICENSE の追加（#12 の要件）

- コード部分のライセンス（MIT 等、選定はユーザー判断）を `LICENSE` としてリポジトリ直下に置く。
- データ・画像は各提供元の条件に従う旨を README に明記する:
  CiNii 由来データ＝ CC BY 4.0／openBD 後継 API＝「本の販促・紹介目的」限定／
  NDL サムネイル API＝非営利は申請不要（継続利用は申請を推奨）。
