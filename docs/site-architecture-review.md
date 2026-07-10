# サイト構成の改善提案（2026-07 レビュー）

現在の構成（HTML / CSS / バニラ JS ＋ Python 標準ライブラリのみ）を点検し、
**標準ライブラリ以外の Python ライブラリ・フレームワークの導入も含めて**
高度化・効率化・高速化できる点を整理する。

前提とする現状:

- **静的表示**: `site/` は GitHub Pages で公開済み（ビルド工程なし・相対パスのみ）。
- **動的検索**: `server/app.py`（`http.server` ベース・検証用）は**ローカル実行のみ**。
- **契約**: `books.json` のスキーマが唯一の契約で、静的・動的どちらの経路も
  これを `site/` へ渡す（[ルート README](../README.md#アーキテクチャ静的既定--動的検索のハイブリッド)）。
- **ページ構成**: スタートページ＋NDC 分類ナビへの刷新（P2）が最新方針
  （[site-structure.md](site-structure.md)）。NDC 棚データも「棚単位の静的 JSON」
  として配信するため、本レビューの結論（表示層は現状維持・改善は Python 側）とは
  矛盾しない。

結論を先に言うと、**表示層（HTML/CSS/JS）は現状維持が最適**で、
**改善の投資対効果が大きいのは Python 側（特に `server/`）**である。

---

## 現状評価 — 維持すべき点

以下の設計は外部調査・実装点検の双方から見て妥当であり、**変えるべきでない**。

| 維持する点 | 理由 |
|---|---|
| `site/` のバニラ JS・ビルド工程なし | GitHub Pages 直配信と完全に整合。JS 52KB / CSS 24KB の規模に SPA フレームワーク（React/Vue 等）やバンドラ（Vite 等）は過剰で、ビルド環境という新たな保守対象を増やすだけ |
| 静的既定＋動的検索のハイブリッド | API が無くても本棚が見えるグレースフルデグレードは公開形態の自由度を最大化する |
| `books.json` スキーマを唯一の契約とする設計 | 表示層無改修でバックエンドを差し替えられる。以下の提案もすべてこの契約を**不変**とする |
| `core/` の純粋関数構成 | ネットワーク・I/O を持たない正規化ロジックに外部依存を入れる理由がない。**標準ライブラリのみを維持** |
| `build/` の標準ライブラリのみ動作 | 「クローンすれば pip install なしで books.json を再生成できる」性質は既定データの再現性として価値が高い。**維持** |

つまり「標準ライブラリ縛りを解く」対象は **`server/`（と将来の取得層）に限定**
するのが、現構成・配信方法との整合性が最も高い。

---

## 改善提案（優先度順）

### 提案 1 — `server/` の FastAPI + uvicorn 化【推奨・効果大】

現行 `server/app.py` は `http.server` ベースの検証用実装で、
[server/README](../server/README.md) 自身が公開運用の穴（圧縮なし・入力検証・
レート制限・キャッシュ上限・WSGI/ASGI 化の必要）を列挙している。
これらを標準ライブラリで一つずつ手当てするより、**HTTP 層を FastAPI に
差し替える方が少ないコードで多くを解決する**。

| 現状の課題（server/README の P3 項目） | FastAPI での解決 |
|---|---|
| 応答圧縮が無い（books 配列は MB 級） | `GZipMiddleware` 1 行。**実測: 既定データ 2.0MB → gzip 約 356KB（82% 削減）** |
| `count` の下限なし・`q` の長さ制限なし | `Query(ge=1, le=10000)` / `max_length=200` の宣言的バリデーション（不正値は自動で 422） |
| `Access-Control-Expose-Headers` 未対応 | `CORSMiddleware(expose_headers=[...])` で解決 |
| 静的配信に `ETag` / `Cache-Control` が無い | `StaticFiles` が ETag・Range・Content-Type を標準処理（自前の `_serve_static` ＋ MIME 表 約 50 行を削除できる） |
| レート制限が無い | `slowapi` 等のミドルウェアを後付け可能 |
| 公開には WSGI/ASGI 化が必要（README #4） | ASGI ネイティブ。**ローカルも本番も同じ `uvicorn` で動く**ため、ローカル専用→公開の移行が「デプロイ先を選ぶだけ」になる |
| テスト手段が乏しい | `TestClient` で `/api/search` の HTTP レベルのテストが書ける（P3 のテスト整備と相乗） |

移行の要点:

- `search_books()`（キャッシュ→取得→正規化）と `core/` は**そのまま流用**。
  差し替えるのは `Handler` クラス（約 80 行）の HTTP 層だけ。
- `/api/search?q=&count=` の入出力（配列ボディ＋ `X-Result-*` ヘッダ）は不変
  → `site/js/app.js` は**無改修**。
- 起動コマンドが `python server/app.py` → `uvicorn server.app:app` 系に変わる
  （互換のため `python server/app.py` で uvicorn を起動する `main()` を残せる）。
- 副産物として `/docs`（OpenAPI）が自動生成され、API 仕様の文書化が不要になる。

代替案との比較:

- **Flask**: 同程度の記述量だが WSGI（同期）のみ。提案 2 の並列取得の恩恵を
  受けられないため、選ぶ理由が弱い。
- **標準ライブラリ継続**: 依存ゼロの価値はあるが、gzip・検証・ETag・レート制限を
  すべて自前実装・自前保守することになる。「検証用の最小実装」の範囲を超えた機能を
  標準ライブラリで積み増すのは工数対効果が悪い。

### 提案 2 — 取得層の httpx 化と並列ページング【P1 移行と連動】

[core/README の P1](../core/README.md)（CiNii Research API 移行）で
**count 上限が 10000 → 200 に下がる**ため、現行の「1 検索＝1 コール」が
「1 検索＝最大数十コール」になる。既定データ規模（5,212 件）なら **27 コール**で、
`urllib` の直列取得ではコール間隔込みで数十秒級になり、動的検索の体感を壊す。

- `httpx.AsyncClient` ＋ `asyncio.gather` で**ページを並列取得**する
  （マナーとして同時実行数はセマフォで 2〜4 に制限）。直列数十秒 → 数秒に短縮できる。
  これは FastAPI（async ネイティブ）を選ぶ提案 1 の最大の実利でもある。
- 接続の再利用（keep-alive）・タイムアウト・HTTP/2 も `urllib` より扱いやすい。
- **導入場所は取得層（`core/ciniisearch.py` の新経路 `fetch_live_cir`）に限定**し、
  現行 CiNii Books 経路と `build/` は標準ライブラリのまま残す
  （並行運用フラグは core/README #1-5 の方針どおり）。

### 提案 3 — 検索キャッシュの diskcache 化【小工数】

server/README P3 の「キャッシュが無限に貯まる」への対処として、自前のファイル
キャッシュを [diskcache](https://grantjenks.com/docs/diskcache/)（SQLite ベース）に
置き換える。TTL・**容量上限つき LRU 追い出し**・プロセス/スレッド安全が
設定だけで手に入り、`_cache_path` / `_load_cache` / `_save_cache` の自前実装と
「掃除処理を書く」作業自体が不要になる。キャッシュの置き場所・冪等・再生成可能と
いう現行思想はそのまま。

### 提案 4 — pydantic による契約（スキーマ）の形式化【中効果】

`books.json` スキーマは「唯一の契約」なのに、その定義は README の記述のみで
機械可読でない。pydantic で `BookRecord` モデルを 1 つ定義すると:

- FastAPI の `response_model` として API 応答が契約に沿うことを**実行時に保証**。
- `build` の出力・`server` の応答・テストのスナップショットを**同じモデルで検証**でき、
  P1 移行（新 API のフィールドマッピング）時の回帰検知が強くなる。
- JSON Schema を自動生成でき、契約の文書化が README の手書きから解放される。

導入は `server`/テスト側に限定し、`core/normalize.py` は dict を返す純粋関数の
まま変えない（モデル化は境界＝出入口でのみ行う）。

### 提案 5 — pytest ＋ ruff ＋ CI【ロードマップ P3 #9 の具体化】

[core/README の #2（P3）](../core/README.md) で要件定義済みのテスト整備を、
依存の観点から確定させる:

- **pytest**: `tests/test_normalize.py`（役割語の最長一致・「ほか/他」判定・
  年代解析・ISBN/ISSN 弁別）＋ source 全件のスナップショット集計。
- **ruff**: リンタ＋フォーマッタを 1 ツールで。設定は `pyproject.toml` に集約。
- **GitHub Actions**: push/PR ごとに pytest ＋ `build.py --limit` スモーク。
  （ワークフロー追加は CI/CD 変更のためユーザーレビュー必須 — 既存方針どおり）

これらは**開発時依存のみ**で、実行時のゼロ依存性（core/build）に影響しない。

### 提案 6 — pyproject.toml による依存の宣言【基盤】

現在リポジトリに Python パッケージング定義が無い。依存を導入する以上、
`pyproject.toml` を追加し **optional-dependencies でモジュール別に分離**する:

```toml
[project]
name = "shelf-blowser"
requires-python = ">=3.11"
dependencies = []                # core/build は今後もゼロ依存

[project.optional-dependencies]
server = ["fastapi", "uvicorn[standard]", "diskcache", "httpx", "pydantic"]
dev    = ["pytest", "ruff"]
```

- `pip install -e ".[server]"`（または `uv sync --extra server`）した人だけが
  動的検索サーバを動かせる。**`build/build.py` はインストール不要のまま**動く。
- 仮想環境・ロックには [uv](https://docs.astral.sh/uv/) を推奨（単体バイナリで
  高速。`uv.lock` で再現性を確保）。

---

## 表示層（HTML/CSS/JS）— 技術構成は現状維持を推奨

- **フレームワーク・バンドラ導入は非推奨**。ビルド工程なしが GitHub Pages 直配信・
  「クローンすれば動く」性質と整合しており、現規模で失うものの方が大きい。
  スタートページの追加（P2）もバニラ JS・静的 HTML の範囲で行う。
- **データ転送量は当面問題ない**: `books.json` は 2.0MB だが、GitHub Pages・
  提案 1 の gzip どちらでも実転送は約 360KB。**1 つの棚＝1 ファイルを丸ごと読む**
  方式を維持する（「すべて」タブの混在ロジックが棚単位の全件読み込みを前提とする
  ため、棚内の分割はしない）。NDC 棚（P2）も 1 分類 1 ファイルで同じ方式であり、
  最大 10,000 件（gzip 転送で約 700KB 想定）は許容範囲。体感が悪ければ分割ロードへ
  切り替える（[site/README](../site/README.md) #7）。
- 表示層の改善は最新方針の P2（スタートページ・本棚ページの初期条件対応
  ＝ [site-structure.md](site-structure.md)）と既存ロードマップ P4（AbortController・
  ピボットブラウジング等、[site/README](../site/README.md)）で必要十分。
  本レビューで追加すべき新項目は無い。

## その他の非推奨事項（過剰と判断したもの）

| 案 | 見送る理由 |
|---|---|
| データベース（SQLite/PostgreSQL）導入 | データは棚単位の静的 JSON（既定 5,212 件・NDC 棚は 1 分類最大 1 万件程度）＋外部 API 中継で完結しており、検索は CiNii 側またはクライアント絞り込みで行う。永続化すべき自前状態が無い |
| Node.js バックエンド併設 | Python（core 共有）と二重実装になり、「build と server が同一ロジック」という設計の要を壊す |
| Docker 必須化 | uv ＋ pyproject で環境再現は足りる。デプロイ先が要求する場合のみ用意すれば良い |

---

## 整合性の確認（現構成・配信方法との突き合わせ）

| 観点 | 影響 |
|---|---|
| `books.json` スキーマ（唯一の契約） | **不変**。提案 4 はむしろ契約を強制する側 |
| `site/`（GitHub Pages 公開） | **無改修**。公開時に `SEARCH_URL` を絶対 URL へ変える既存手順のまま |
| グレースフルデグレード | 不変（初期表示は静的 `books.json`、API 不在でも本棚は見える） |
| `core/` の純粋性・ゼロ依存 | 不変（httpx は新取得経路のみ。正規化は無改修） |
| `build/` のゼロ依存・オフライン動作 | 不変（`python build/build.py` はインストール不要のまま） |
| ローカル実行手順 | `server` の起動コマンドのみ変更（`python server/app.py` 互換の `main()` を残すことで最小化可能）。README の該当箇所を更新する |
| 既存ロードマップ | P1（CiNii Research 移行）＝提案 2、P3 #9（テスト）＝提案 5、P3 #10（server 堅牢化）＝提案 1・3、公開ホスティング（server/README #4）＝提案 1 が前提整備に相当。P2（NDC 棚データの取得バッチ）も提案 2 の並列取得の恩恵を受ける。**矛盾なし** |

## 段階的な導入プラン

依存追加・CI 変更を含むため、各フェーズは auto-ship 対象外（通常レビュー）とする。

1. **Phase 1 — 基盤**: `pyproject.toml` 追加（提案 6）＋ pytest でテスト整備（提案 5）。
   CI ワークフローは別 PR でユーザーレビューを経て追加。
2. **Phase 2 — server 刷新**: FastAPI + uvicorn 化（提案 1）＋ diskcache（提案 3）＋
   pydantic モデル（提案 4）。`/api/search` の契約不変をテストで担保して差し替え。
3. **Phase 3 — P1 移行と同時**: `fetch_live_cir` を httpx 並列ページングで実装（提案 2）。
4. **Phase 4 — 公開**: uvicorn がそのまま動く PaaS（Render / Fly.io / Cloud Run 等）へ
   `server` をデプロイし、GitHub Pages 側の `SEARCH_URL` を差し替え
   （選定はユーザー判断 — ルート README「公開（到達性）」の方針どおり）。
