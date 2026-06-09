# dd2030 slack-logs

dd2030 の Slack public channel ログを GitHub に蓄積するリポジトリ。**月次 canonical**（保全）と **rolling mirror**（現状クエリ）の二層構成。

## いまの状態（2026-06-09 時点）

| 項目 | 値 |
|---|---|
| 保全層 (`raw/`) | 2025-01 〜 2026-04 の16ヶ月分が backfill 済み。毎月1日 09:11 JST cron で更新 |
| ミラー層 (`mirror/`) | 6時間ごと (cron `7 */6 * * *`) に上書き更新。最新状態は [`mirror/sync.json`](mirror/sync.json) を参照 |
| 対象チャンネル | dd2030 Slack workspace の **public channel** すべて（autoJoin 有効）。`vars.SKIP_CHANNELS` で除外可 |
| Slack bot token | nishio が `nishio/oss_weekly_reporter` で使っている bot token を流用（フェーズ1） |
| 移管フェーズ | dd2030 org 自前 token への切り替えは **未着手**（→ [将来の保守者向け](#将来の保守者向け) を参照）|

---

## 誰向けか

- **このログを読みたい人** → [利用者向け](#利用者向け)
- **secret や cron をいじる人** → [運用者向け](#運用者向け)
- **設計を変えたい・引き継ぐ人** → [将来の保守者向け](#将来の保守者向け)

---

## 利用者向け

### ディレクトリ構成

```
slack-logs/
├── raw/                                  # 月次 canonical（保全用、append-only）
│   └── slack/
│       └── <channel_id>/
│           └── <YYYY>-<MM>.jsonl.gz      # 1ヶ月分のメッセージ＋スレッド
│
├── mirror/                               # rolling snapshot（現状用、毎回上書き）
│   ├── slack/
│   │   └── <channel_id>.jsonl.gz         # 直近14日分のメッセージ＋スレッド
│   ├── sync.json                         # 最終同期時刻・window・channel数・message数
│   └── users.json                        # users.list snapshot（直近）
│
├── state/                                # 月次 users.list snapshot（保全層に紐づく）
│   └── users-<YYYY>-<MM>.json
│
├── scripts/
│   └── slack_mirror.py                   # mirror 層の collector（Python）
│
└── .github/workflows/
    ├── slack-backup.yml                  # raw 層の cron（月次）
    └── slack-mirror.yml                  # mirror 層の cron（6時間ごと）
```

### どちらを読むべきか

| シチュエーション | 読むべき層 | 理由 |
|---|---|---|
| 「今週 dd2030 で何が起きてる？」 | `mirror/` | 鮮度が高い（最大6時間遅れ） |
| 「先月の議論の流れを追いたい」 | `raw/` | スレッド完全性が高い（2ヶ月遅延の代償） |
| 「2025年9月の特定議論を遡る」 | `raw/` | 履歴を持つのは raw 側だけ |
| 「特定 Issue が Slack で言及されているか」 | `mirror/` → `raw/` の順 | mirror で直近を当たり、なければ raw を月単位で grep |

詳細な使い分けは [dd2030-wiki: AI から Slack ログを参照するパターン](https://nishio.github.io/dd2030-wiki/topics/ai-slack-access-patterns) を参照。

### 読み方の具体例

#### A. 最新の同期状況を確認

```bash
curl -sL https://raw.githubusercontent.com/digitaldemocracy2030/slack-logs/main/mirror/sync.json | jq '. | del(.channels)'
```

#### B. 特定チャンネルの直近14日メッセージを読む

```bash
# channel_id は mirror/sync.json の .channels[].name から逆引き
curl -sL https://raw.githubusercontent.com/digitaldemocracy2030/slack-logs/main/mirror/slack/C08F7JZPD63.jsonl.gz | gunzip | jq -c
```

#### C. 特定月の canonical を読む

```bash
curl -sL https://raw.githubusercontent.com/digitaldemocracy2030/slack-logs/main/raw/slack/C08F7JZPD63/2026-04.jsonl.gz | gunzip | jq -c
```

#### D. 全部 clone して grep する

```bash
gh repo clone digitaldemocracy2030/slack-logs
cd slack-logs
# 直近14日で 'Discord' に言及している message を全 channel から grep
for f in mirror/slack/*.jsonl.gz; do
  zcat "$f" | jq -c "select(.text | test(\"Discord\"; \"i\"))" | \
    while read -r line; do echo "$(basename "$f" .jsonl.gz): $line"; done
done
```

### JSONL フォーマット

各 `.jsonl.gz` の構造:

```
{ channel meta }                    # 1行目: { "channel_id", "channel_name" }
{ Slack message }                   # 2行目以降: conversations.history の raw response
{ Slack thread reply }              # スレッド返信は親メッセージの前後に並ぶ
...
```

メッセージ本体は **Slack API の `conversations.history` / `conversations.replies` レスポンスをそのまま** 1メッセージ = 1行で保存。よく使うフィールド:

| キー | 意味 |
|---|---|
| `ts` | Slack 内一意 ID（Unix epoch + 連番）|
| `user` | 発言者 user_id（`U...` 形式）。`users.json` で表示名に解決 |
| `text` | 本文（`<@Uxxx>` や `<#Cxxx>` のメンションは生のまま）|
| `thread_ts` | スレッド親の `ts`。これが `ts` と一致するなら親メッセージ |
| `reply_count` | スレッド返信数（親のみ） |
| `subtype` | `bot_message` `channel_join` 等の特殊種別 |
| `files` | 添付ファイル（本文は保存しない、URL のみ） |

ユーザー名解決には同月の `state/users-<YYYY>-<MM>.json`（canonical）または `mirror/users.json`（最新スナップショット）を使う。

---

## 運用者向け

### Secrets / Variables

| 種別 | 名前 | 値 |
|---|---|---|
| Secret | `SLACK_TOKEN` | Slack bot token（`xoxb-...`、scope: `channels:history` `channels:read` `users:read` `channels:join`）|
| Variable | `SKIP_CHANNELS` | 取得対象外にする channel id を空白区切り（任意）|

設定:

```bash
gh secret set SLACK_TOKEN -R digitaldemocracy2030/slack-logs
gh variable set SKIP_CHANNELS -R digitaldemocracy2030/slack-logs --body "C0XXXXX C0YYYYY"
```

### 失敗時の挙動

両 workflow とも失敗時に **自動で Issue を起票**する（labels: `slack-backup`/`slack-mirror` + `failure`）。本文に run URL と target を含む。修正後に手動 dispatch で再試行し、issue を close する運用。

### よくある操作

#### canonical の過去分を埋め戻す

```bash
gh workflow run slack-backup.yml -R digitaldemocracy2030/slack-logs -f year=2025 -f month=6
```

**注意**: 連続 dispatch すると GitHub Actions concurrency 仕様で**中間 pending が cancel** される。複数月を一度に処理するときは [scripts/backfill-sequential.sh](scripts/backfill-sequential.sh) のような外部 wait ループで sequential 化する（過去の埋め戻しで実証済み、コミット [39a299e](../../commit/39a299e) も参照）。

#### mirror を即時更新する

```bash
gh workflow run slack-mirror.yml -R digitaldemocracy2030/slack-logs
# window を変更したいとき
gh workflow run slack-mirror.yml -R digitaldemocracy2030/slack-logs -f window_days=30
```

#### cron を一時停止する

`.github/workflows/<name>.yml` の `on:` から `schedule:` ブロックを削除（or コメントアウト）して push。

### Slack rate limit について

dd2030 workspace 内で動く bot app は **internal customer-built** 扱いになり、2025-05-29 以降の非 Marketplace 制限（`conversations.history`/`replies` が 1分1req・15件）の対象外。Tier 3（~50req/min）が適用される。mirror script (`scripts/slack_mirror.py`) は 429 受信時に `Retry-After` ヘッダに従って待つ実装。

---

## 将来の保守者向け

### なぜ二層か

「保全用途」と「現状クエリ用途」は鮮度・完全性の要件が真逆。

- 保全用途: スレッド完全性が重要 → Slack API の制約上 **2ヶ月遅延**が必要（[`kuboon/slack-logger-cli-action`](https://github.com/kuboon/slack-logger-cli-action) の README 参照）
- 現状用途: 1週間以内の鮮度が必要 → 完全性は割り切る

両者を一本のパイプラインで兼ねると常にどちらかが妥協する。詳細は [dd2030-wiki: AI から Slack ログを参照するパターン](https://nishio.github.io/dd2030-wiki/topics/ai-slack-access-patterns)。

### なぜ canonical を `slack-logger-cli-action` で、mirror を Python 自前か

| 観点 | canonical | mirror |
|---|---|---|
| 入力範囲 | 月単位（year/month）で十分 | 任意の日数 window が必要 |
| 既存実装 | `slack-logger-cli-action` がそのまま使える | 月単位前提なので使えない |
| 結論 | `uses:` で取り込む（fork なし）| Python + slack_sdk で自前実装 |

mirror が将来「分単位の差分取得」「watermark / dedup の本格運用」を求められたら、canonical 側も一緒に置き換えることを検討する（→ [アーカイブパイプライン設計](https://nishio.github.io/dd2030-wiki/topics/archive-pipeline-design) の「推奨構成」）。

### 既知の制約

- **private channel / DM は非対応**: bot scope が `channels:*` のみ。group:* / im:* を追加すれば取れるが、公開化 (CC-BY) との整合が必要なため意図的に外している
- **添付ファイル本文は保存しない**: URL のみ保存。Slack 無料プランで添付が失効しても本文の復元はできない
- **autoJoin の副作用**: bot が新規 public channel に自動 join するので、Slack 上で bot の在席が広範に見える
- **canonical の 2ヶ月遅延**: 直近2ヶ月は raw/ には現れない。mirror/ または `nishio/oss_weekly_reporter` の data ブランチで補完
- **mirror の履歴喪失**: 上書きなので、過去の mirror snapshot に遡る手段はない（履歴は raw/ の責務と割り切る）

### 移行ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| 1. bootstrap | リポ作成 + workflow 整備 + 過去16ヶ月分 backfill | **完了** (2026-06-09) |
| 2. mirror layer 追加 | rolling snapshot pipeline 追加 | **完了** (2026-06-09) |
| 3. 脱-nishio token | dd2030 org の Slack app に切り替え | 未着手 |
| 4. ライセンス確定 | データ CC BY 4.0 / コード MIT のデュアルライセンスを確定。公式サイトからのリンク貼りは未 | **データ・コードのライセンス確定済み (2026-06-10)**。公式サイトからのリンク貼りは未着手 |
| 5. 過去ログ移送 | `nishio/oss_weekly_reporter` data ブランチ 67週分 (~117MB) を CC-BY 再公開 | 未着手 |
| 6. Discord 移行後の扱い | Slack 卒業後、両 workflow を停止 / Discord 用 collector に置き換え | 検討中（dd2030 全体の Discord 移行決定待ち） |

関連 Issue:
- digitaldemocracy2030/website [#170](https://github.com/digitaldemocracy2030/website/issues/170) 毎週のプロジェクト活動状況の更新処理を移管する（**前提が変わった**: 保全は slack-logs に分離・生成は当面 `nishio/oss_weekly_reporter` 継続）
- digitaldemocracy2030/website [#177](https://github.com/digitaldemocracy2030/website/issues/177) 「プロジェクトの歴史」更新の自動化（kuboon の脱-nishio 設計提案。本リポの方針と矛盾しない）

### 関連リポ・参照

- [`kuboon/slack-logger-cli-action`](https://github.com/kuboon/slack-logger-cli-action) — canonical 層の collector
- [`nishio/oss_weekly_reporter`](https://github.com/nishio/oss_weekly_reporter) — 週次 AI レポート生成（このリポと分離して併走）
- [dd2030-wiki: OSS Weekly Reporter](https://nishio.github.io/dd2030-wiki/entities/oss-weekly-reporter) — 移管経緯・運用状況
- [dd2030-wiki: アーカイブパイプライン設計](https://nishio.github.io/dd2030-wiki/topics/archive-pipeline-design) — 設計判断の根拠

---

## ライセンス

このリポジトリは **デュアルライセンス**:

| 対象 | ライセンス | ファイル |
|---|---|---|
| **データ** (`raw/`, `mirror/`, `state/`) — Slack ログ本体 | [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/) | [LICENSE-DATA](LICENSE-DATA) |
| **コード** (`scripts/`, `.github/workflows/`, ドキュメント) | [MIT License](https://opensource.org/licenses/MIT) | [LICENSE](LICENSE) |

データを再利用する際は CC BY 4.0 に従って **「dd2030 / digitaldemocracy2030 slack-logs」へのクレジット表記**をお願いします。スクリプトの改変・再配布は MIT のもとで自由に。

決定経緯: [nishio 2026-05-13 提案](https://nishio.github.io/dd2030-wiki/entities/oss-weekly-reporter#2026-05-のスコープ拡張) → 2026-06-10 確定。
