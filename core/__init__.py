"""core — CLI バッチ（build/）と API サーバ（server/）で共有する純粋ロジック。

CiNii OpenSearch のレスポンス（ライブ取得でも保存済み JSON でも構造は同一）を
site が読む books レコードへ正規化する処理を集約する。標準ライブラリのみで動く。
"""
