# server — 動的検索の最小 API サーバ（検証用）

> 「検索語 → CiNii OpenSearch 取得 → `core.normalize` で正規化・整列 →
> `core.openbd` で書影を一括付与 → books 配列を JSON で返す」だけの最小サーバ。
> 返す JSON は `build` が出力する `site/data/books.json` と**同一スキーマ**の
> ため、表示層（`site/`）は無改修で動的検索に対応する。標準ライブラリのみ・
> 単一ファイル（`app.py`）。

## 提供するもの

| パス | 内容 |
|---|---|
| `GET /api/search?q=<語>&count=<N>` | 動的検索の結果（books 配列）。付随情報は `X-Result-Source`（cinii / local / local-fallback / cache）・`X-Result-Count` ヘッダで返す |
| `GET /api/search?q=<語>&ndc=<記号>`（計画・P2） | NDC 分類内の検索（→「今後必要な作業 #1」） |
| その他のパス | `site/` 配下の静的ファイル配信（同一オリジンなので CORS 不要） |

## 実行方法

```bash
# 既定: ローカル source を検索語で絞り込む（CiNii に到達できない環境でも動く）
python server/app.py --port 8000

# 本番相当: CiNii OpenSearch を実際に叩く（失敗時はローカルへフォールバック）
python server/app.py --port 8000 --live
```

主なオプション: `--host` / `--port` / `--live` / `--source PATH` / `--count N` /
`--cache-ttl 秒`（0 で無期限）/ `--no-covers`（書影取得を無効化）。

## キャッシュ

- 検索語ごとの結果（書影付与後）を `server/cache/` に保存し、同じ語の再取得を
  避ける（CiNii・OpenBD へのマナー・応答速度）。キーは
  `live フラグ | count | 検索語` の SHA-1。
- TTL は既定 3600 秒。**Git 管理外・冪等・再生成可能**（`build` の OpenBD
  キャッシュと同じ思想）。
- 書影は `core.openbd.enrich_covers()` で ISBN 単位に `.cache/openbd/` へ
  キャッシュする。`build --covers` が事前に埋めたキャッシュを共有するため、
  同じ ISBN を再取得しない。

---

## 今後必要な作業（要件・手順）

優先度は [ルート README のロードマップ](../README.md#今後必要な作業ロードマップ) に対応する。

### 1. NDC 分類内の動的検索（P2 #8）

- **目的**: NDC 棚（[docs/site-structure.md](../docs/site-structure.md)）の中を
  検索語で絞り込めるようにする。
- **要件**:
  - `/api/search` に `ndc=<分類記号>`（1〜3 桁）パラメータを追加する。
    `q` と併用されたら「その分類 かつ その語」の結果を返し、`q` 無しなら
    分類全体（＝静的 `site/data/ndc/<記号>.json` と同等）を返す。
  - ライブ時は CiNii の分類検索＋語の複合クエリで取得する
    （分類検索パラメータの事前調査〈P2 #3〉が前提）。CiNii へ到達できない環境では
    `site/data/ndc/<記号>.json` を読み込んでローカル絞り込みする
    （現行の source 代役と同じ思想）。
  - 返す JSON は従来どおり books 配列（同一スキーマ）・`X-Result-*` ヘッダ。
    キャッシュキーに `ndc` を含める。
  - **サーバ未稼働時のフォールバックは site 側で完結する**
    （クライアントが読み込み済み NDC データを絞り込む。→ [site/README.md](../site/README.md) #2）。
    server はあくまで「稼働していればより良い検索」を提供する位置づけ。

### 2. 運用堅牢化（P3）

現状は「ローカル検証用の最小実装」であり、公開運用には次の穴がある。

- **応答圧縮が無い**: books 配列は大きい検索語で MB 級になる。
  `Accept-Encoding: gzip` を判定して API 応答・静的配信の双方を gzip 圧縮する
  （標準ライブラリ `gzip` で可）。
- **キャッシュが無限に貯まる**: `server/cache/` に上限・掃除が無い。
  保存時に**期限切れエントリの削除**と**上限件数（例: 500 件）超過分の
  古い順削除**を行う。
- **入力検証が甘い**: `count` は上限（10000）のみで**下限が無く負値を受け付ける**。
  `1 ≦ count ≦ 上限` に正規化する。`q` にも最大長（例: 200 文字）を設ける。
- **簡易レート制限が無い**: `/api/search` は CiNii への中継のため、公開時は
  接続元ごとの最小間隔（例: 1 秒）や同時実行数の制限を設けて CiNii を守る。
- **CORS でカスタムヘッダが読めない**: 別オリジンのフロントから
  `X-Result-Source` / `X-Result-Count` を読むには
  `Access-Control-Expose-Headers` の付与が必要。
- **静的配信にキャッシュ制御が無い**: `Cache-Control` / `ETag` を付ける
  （`books.json` の更新が反映されるよう max-age は短めに）。

**手順**: いずれも `app.py` 内で完結する（標準ライブラリのみ維持）。
圧縮 → キャッシュ掃除 → 入力検証 → Expose-Headers → キャッシュ制御の順で
小さく分けて実装・確認する。

### 3. CiNii Research 移行への追随（P1・core と連動）

- 取得層の移行（appid・ページング・新旧切替フラグ）は
  [core/README.md](../core/README.md) の #1 で行う。server 側の作業は:
  - appid を環境変数（`CINII_APP_ID`）から読んで core へ渡す。
  - `--live` の取得先（新旧 API）を選ぶ起動オプションを追加する。
  - ページング取得により 1 検索のコール数が増えるため、**キャッシュ TTL の既定を
    見直す**（長めにして CiNii への負荷を抑える）。

### 4. 公開ホスティング（P4〜P5）

- **現状の制約**: `http.server` ベースの自前サーバは検証用であり、公開運用には
  WSGI/ASGI 化またはサーバレス関数化が要る。
- **要件**:
  - `search_books()`（キャッシュ→取得→正規化）はそのまま使い、HTTP 層だけを
    差し替えられる構造を保つ。
  - 案 A: WSGI 化して PaaS 無料枠（Python 対応）へ。案 B: サーバレス関数
    （1 関数 = `/api/search`）にし、フロントは GitHub Pages のまま
    `site/js/app.js` の `SEARCH_URL` を絶対 URL に差し替える。
  - どちらもキャッシュの置き場所（ローカルディスクが永続しない環境では
    メモリ or 外部ストア）を決める。コールドスタートの体感は初期表示が
    静的データであるため許容範囲（検索時のみ待たせる）。
- **手順**: 移行先の選定（ユーザー判断）→ HTTP 層の分離 → デプロイ →
  `SEARCH_URL` 切り替え → 動作確認。
