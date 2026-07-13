# core — 共有ロジック（正規化・整列・CiNii 取得）

> `build`（バッチ）と `server`（オンデマンド）が共有する中核モジュール。
> 同じ OpenSearch レスポンスからはどちらの経路でも**同一の books 配列**が得られる
> （二重実装の排除＝表示結果の一致）。標準ライブラリのみで動く。

## 構成

| ファイル | 役割 |
|---|---|
| `normalize.py` | OpenSearch item → books レコードの正規化・整列。**純粋関数のみ**（ネットワーク・ファイル I/O なし・冪等） |
| `ciniisearch.py` | CiNii OpenSearch の取得。ライブ取得（`fetch_live`）とローカル代役（`search_local`）の 2 系統。ネットワーク I/O はここに集約 |

- 正規化・種別判定（`contribKind`）・「ほか/他」省略表記の検出などの仕様詳細は
  [`build/README.md`](../build/README.md) の「抽出仕様」を参照（本モジュールが実装）。
- 取得は `items_from_response()` が受け取る同一構造（`@graph[0].items`）に揃えて
  返すため、下流はライブ／ローカルを区別しない。

---

## 今後必要な作業（要件・手順）

優先度は [ルート README のロードマップ](../README.md#今後必要な作業ロードマップ) に対応する。

### 1. CiNii Research API への移行（P1・最優先）

- **背景**: CiNii Books は CiNii Research へ統合され（機能統合 2026 年 1 月・3 月
  リリース済み）、並行運用終了（2027 年 3 月頃見込み）後は現行の
  CiNii Books OpenSearch（`ci.nii.ac.jp/books/opensearch/search`）が使えなくなる
  前提で移行する。統合により「図書館検索」「所蔵検索」の OpenSearch も追加された。
- **新 API（CiNii Research OpenSearch）の判明している差分**:
  - エンドポイント: `https://cir.nii.ac.jp/opensearch/books`（検索タイプ `books`）。
  - **appid（アプリケーション ID）必須** — 現行は不要だった。
  - **count 上限 200**（既定 20）— 現行実装の `count=10000` は不可。
  - CORS 対応（`Access-Control-Allow-Origin: *`）。JSON-LD あり。
- **要件**:
  1. **事前調査**: JSON-LD レスポンスに所蔵館数（`cinii:ownerCount` 相当）が
     含まれるか、`sortorder` に所蔵館数降順があるかを実レスポンスで確認する
     （無ければ統合で追加された所蔵検索 OpenSearch との併用を検討）。
     item 構造の差分をフィールドマッピング表として本書へ記録する。
     あわせて **NDC 分類ナビ（P2 #3）が使う分類検索パラメータ**と**上位桁での
     前方一致の可否**も確認する。現行 CiNii Books 側は確認済み（`clas=<記号>*`・
     `fetch_response()` として実装済み）。**新 API 側の同等機能の確認が残作業**
     （→ [docs/site-structure.md](../docs/site-structure.md)「問題点と対処」#1）。
  2. **appid の管理**: 開発者登録で appid を取得し、環境変数（例:
     `CINII_APP_ID`）または起動オプションで注入する。**リポジトリにコミットしない**。
  3. **ページング取得**: `count≦200` のため `start` によるページングで集める
     `fetch_live_cir(query, max_records)` を実装する。1 検索＝1 コールの前提が
     崩れるため、**既定の取得上限を現実的な値（例: 1,000〜2,000 件＝5〜10 コール）に
     見直す**。コール間隔・指数バックオフ・明示的 UA は現行と同じマナーを守る。
  4. **レスポンス差異の吸収**: 新旧の item 構造差は取得層（`ciniisearch.py`）で
     現行構造へマッピングし、`normalize.py` は原則無改修とする
     （吸収しきれない場合のみ normalize に変換を追加）。
  5. **切替と並行運用**: 取得元を `books-cib`（現行）／`books-cir`（新）で選べる
     フラグを設け、並行運用期間中に結果を突き合わせて検証する。並行運用終了後に
     既定を新 API へ切り替え、現行経路を削除する。
  6. **source の再取得手順**: 既定データ（`source/*.json`）を新 API で作り直す
     手順を確立する（ページング結合後に `@graph[0].items` 形式へ整形して保存し、
     ローカル代役・build の入力互換を保つ）。
- **手順**: 事前調査（1）→ appid 取得（2）→ `fetch_live_cir` 実装＋マッピング（3・4）
  → server にフラグ追加（5）→ 検証 → source 再生成（6）→ 既定切替。
- **副次的な論点**: 新 API は CORS 対応のため、**ブラウザから直接叩く構成**
  （server 中継の廃止）も技術的には可能になる。ただし appid がクライアントへ
  露出する・キャッシュとマナー制御を失う欠点があるため、当面は中継を維持し、
  公開形態を決める段階で再検討する。

### 2. 正規化ロジックのユニットテスト（P3）

- **目的**: `contrib_kind()`・`has_others_marker()`・`normalize_creators()`・
  `parse_year_decade()` は精緻な規則（最長一致・「ほか/他」の位置判定・不完全年など）を
  持ち、README に「誤検出 0 件」を確認済みと記録している。この品質を回帰テストで固定する。
- **要件**:
  - `pytest` によるユニットテストを `tests/` へ追加する（役割語の最長一致、
    `[岡野他家夫著]` を editorial にしない、`◯◯著 ; ◯◯ほか編` を personal に保つ、
    `197-`/`19--` の年代解析、ISBN/ISSN の弁別、シリーズ抽出など、README 記載の
    代表ケースを網羅）。
  - `source/日本近代文学.json` 全件に対する集計値（personal 2,875 / editorial 2,337 等）
    を検証するスナップショットテストを加える（データ更新時は期待値も更新）。
  - GitHub Actions で push / PR ごとに実行する CI を追加する（pytest ＋
    `build.py --limit` のスモーク実行と books.json のスキーマ検証）。
    ワークフロー追加は CI/CD 変更のためユーザーレビューを必須とする。
- **手順**: `tests/test_normalize.py` 作成 → ローカルで green を確認 →
  CI ワークフロー追加の PR を別途立てる。

### 3. 整列関数の切り出し（P3）

`build.py` との整列処理の重複解消のため、`normalize_items()` から整列部分を
`sort_records()` として分離する（詳細は [build/README.md](../build/README.md) の
「今後必要な作業 #4」）。
