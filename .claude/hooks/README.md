# .claude/hooks — フック

Claude Code が `.claude/settings.json` に基づいて実行するシェルフック。
（フックは**シェルを実行する仕組み**で、Claude の skill は呼べない。new-branch と
同等の挙動を git コマンドで再現している。）

## post-merge-rebranch.sh（Stop フック）

**一つのチャットで開発している間、PR が main にマージされるたびに、自動で
新しいブランチへ移行し、マージ済みの旧ブランチを片付ける。**

Claude が応答を終えるたび（Stop）に発火し、次の条件を**すべて**満たすときだけ動く。

1. 既定ブランチ（`main` / `master`）上でも detached HEAD でもない。
2. **active（main 以外のローカル）ブランチが 1 つだけ**である。
   - 複数ある場合は誤削除を避けるため**何もしない**（ユーザーに委ねる）。
3. 現在のブランチが `origin/main` に**マージ済み**である。

満たしたときの動作:

1. `git fetch origin main` で最新を取得。
2. 最新 `main` から新しいブランチ `claude/work-<日時>` を作成して移行。
3. マージ済みの旧ブランチを `git branch -d` で削除。
4. `systemMessage` で「削除した旧ブランチ名 / 新ブランチ名」を通知。

### 設計方針

- **fail-open**: git 操作やネットワークが失敗しても、フックは安全に終了し
  セッションを妨げない（ブロックしない）。
- **安全側に倒す**: active ブランチが 1 つのときだけ自動化する。判断がつかない
  状況（複数ブランチ・未マージ）では何もしない。
- 新ブランチ名は日時ベースの自動生成。意味のある名前にしたいときは作成後に
  `git branch -m <新名>` でリネームする。

### 制約

- マージ済み判定は `git merge-base --is-ancestor`（**マージコミット方式**）を前提。
  GitHub の **squash / rebase マージ**では旧ブランチの tip が main の祖先に
  ならないため、この方法では検知できない。本リポジトリの PR は「Merge pull
  request」（マージコミット）で運用しているため問題ない。
- Stop フックは応答ごとに発火するため、毎ターン `git fetch origin main` が走る
  （条件を満たさない大半のターンは fetch のみで終了する）。

### 無効化

一時的に止めたいときは `.claude/settings.json` の `Stop` フック項目を削除するか、
スクリプトを実行権限から外す（`chmod -x`）。
