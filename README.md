# dd2030 slack-logs

dd2030 の Slack public channel ログを GitHub に蓄積するためのリポジトリ。

## 目的

- Slack 無料プランで過去メッセージが順次非表示化されることへの対策（保全）
- Discord 移行（2026-05 以降検討中）に伴う過去ログの受け皿
- 公式アーカイブとして CC-BY 公開化（nishio 2026-05-13 提案、要決定）

## 設計 — 二層構成

このリポは「保全（canonical）」と「現状ミラー（rolling snapshot）」の二層を持つ。AI からの参照シチュエーション2タイプ（[dd2030-wiki: AI から Slack ログを参照するパターン](https://nishio.github.io/dd2030-wiki/topics/ai-slack-access-patterns)）に対応する。

### `raw/` — 月次 canonical（保全用）

- **collector**: [`kuboon/slack-logger-cli-action`](https://github.com/kuboon/slack-logger-cli-action) を fork なしで `uses:` 導入
- **保存粒度**: `raw/slack/<channel_id>/<YYYY>-<MM>.jsonl.gz` を月単位で commit
- **state**: `state/users-<YYYY>-<MM>.json`（退会者解決のための users.list snapshot）
- **頻度**: 毎月1日 09:11 JST。`workflow_dispatch` で `year`/`month` 指定の過去分埋め戻し可
- **2ヶ月遅延**: スレッド返信が親メッセージの月に紐づく Slack API 仕様への対処として、実行月の2ヶ月前を取得
- workflow: [.github/workflows/slack-backup.yml](.github/workflows/slack-backup.yml)

### `mirror/` — rolling snapshot（現状クエリ用）

- **collector**: Python + `slack_sdk`（`scripts/slack_mirror.py`）
- **保存粒度**: `mirror/slack/<channel_id>.jsonl.gz`（**毎回上書き、履歴なし**）。直近 14 日分のメッセージ＋スレッド
- **メタ**: `mirror/sync.json`（最終同期時刻、window、channel/message 数）、`mirror/users.json`
- **頻度**: 6時間ごと（cron `7 */6 * * *`）。`workflow_dispatch` で `window_days` 上書き可
- 履歴は `raw/` 側の責務。`mirror/` は AI が「直近の状態」を知るための薄いビュー
- workflow: [.github/workflows/slack-mirror.yml](.github/workflows/slack-mirror.yml)

詳細な設計判断・推奨構成・代替案は [dd2030-wiki: アーカイブパイプライン設計](https://nishio.github.io/dd2030-wiki/topics/archive-pipeline-design) を参照。

## 関連

- 週次レポート生成（AI要約）: [`nishio/oss_weekly_reporter`](https://github.com/nishio/oss_weekly_reporter)（保全とは分離して併走）
- 移管経緯: [dd2030-wiki: OSS Weekly Reporter](https://nishio.github.io/dd2030-wiki/entities/oss-weekly-reporter)
- 関連 Issue: digitaldemocracy2030/website [#170](https://github.com/digitaldemocracy2030/website/issues/170) / [#177](https://github.com/digitaldemocracy2030/website/issues/177)

## セットアップ手順（運用開始時に必要）

1. Slack app を作成し、bot token (`xoxb-...`) を発行
   - scope: `channels:history` `channels:read` `users:read` `channels:join`
   - 可能なら **internal customer-built app** として登録（2025-05-29 rate limit 制限の対象外）
2. リポジトリ Secrets に `SLACK_TOKEN` を登録
3. （任意）`SKIP_CHANNELS` Variable に除外したい channel id を空白区切りで登録
4. `workflow_dispatch` でまず1ヶ月分をテスト実行
5. 過去分を `year`/`month` 指定で月単位に埋め戻し

## ライセンス

未定（CC-BY 公開化検討中）。
