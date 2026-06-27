#!/usr/bin/env bash
#
# post-merge-rebranch.sh — Stop フックスクリプト
#
# 目的:
#   PR がクラウドの main にマージされた際、active な作業ブランチが 1 つだけなら、
#   その（マージ済みの）ブランチを削除し、最新の main から新しいブランチへ移行する。
#   → 一つのチャットで開発している間、マージのたびに自動で新規ブランチへ切り替え、
#     それまでのブランチを片付ける（new-branch 相当の git 操作を実行する）。
#
# 安全方針（迷ったら何もしない / fail-open）:
#   - main / master 上、detached HEAD では何もしない。
#   - active（main 以外のローカル）ブランチが「1 つだけ」でなければ何もしない。
#     複数ある場合は誤って消さないよう、ユーザーに委ねる。
#   - 現ブランチが origin/main にマージ済み（マージコミット方式）でなければ何もしない。
#   - いずれの git 操作も失敗したら、その場で安全に終了する（ブロックしない）。
#
# 制約:
#   - マージ済み判定は merge-base による「マージコミット方式」を前提とする。
#     squash / rebase マージはこの方法では検知できない（README 参照）。

# fail-open: エラーでフックがセッションを妨げないよう、常に 0 で抜ける。
set -u

# --- リポジトリルートへ ---
root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
  root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[ -n "$root" ] || exit 0
cd "$root" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# --- 現在のブランチ ---
current="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ -n "$current" ] || exit 0                       # detached HEAD は対象外
case "$current" in
  main|master) exit 0 ;;                           # 既定ブランチ上では何もしない
esac

# --- active（main 以外のローカル）ブランチ数 ---
active_count="$(git for-each-ref --format='%(refname:short)' refs/heads \
  | grep -cvxE 'main|master' || true)"
[ "$active_count" = "1" ] || exit 0               # 1 つだけのときのみ自動化する

# --- main の最新を取得（ネットワーク失敗は安全に無視） ---
git fetch origin main --quiet 2>/dev/null || exit 0

# --- 現ブランチが origin/main にマージ済みか（マージコミット方式を前提） ---
git merge-base --is-ancestor HEAD origin/main 2>/dev/null || exit 0
# main と同一（= 新規に切っただけで差分が無い）ブランチは「マージ済み」扱いしない。
# これが無いと、作りたてのブランチで毎ターン誤発火してしまう。
[ "$(git rev-parse HEAD 2>/dev/null)" != "$(git rev-parse origin/main 2>/dev/null)" ] || exit 0

# --- 新しいブランチ名（自動生成。後でユーザーがリネーム可能） ---
new_branch="claude/work-$(date +%Y%m%d-%H%M%S)"
if git show-ref --verify --quiet "refs/heads/$new_branch"; then
  new_branch="${new_branch}-$$"
fi

# --- 最新 main から新規ブランチへ移行し、マージ済みの旧ブランチを削除 ---
git switch -c "$new_branch" origin/main --quiet 2>/dev/null || exit 0
git branch -d "$current" --quiet 2>/dev/null || true

# --- ユーザー/Claude へ通知（systemMessage はそのまま表示される） ---
printf '{"systemMessage":"post-merge-rebranch: マージ済みブランチ %s を削除し、最新の main から %s を作成して移行しました。"}\n' \
  "$current" "$new_branch"

exit 0
