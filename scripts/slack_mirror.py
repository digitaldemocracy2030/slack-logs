#!/usr/bin/env python3
"""
Slack mirror: 過去 N 日分の public channel ログを mirror/ 配下に上書きする。

- raw/slack/ (月次 canonical) とは独立して走る現状クエリ用 rolling snapshot
- 履歴は持たない (raw 側の責務)、毎回上書き
- 出力: mirror/slack/<channel_id>.jsonl.gz, mirror/users.json, mirror/sync.json
"""

import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def env(key: str, default: str | None = None) -> str:
    v = os.environ.get(key, default)
    if v is None:
        print(f"::error::missing env {key}", file=sys.stderr)
        sys.exit(1)
    return v


SLACK_TOKEN = env("SLACK_TOKEN")
WINDOW_DAYS = int(env("WINDOW_DAYS", "75"))
SKIP_CHANNELS = set(filter(None, env("SKIP_CHANNELS", "").split()))
OUT_DIR = Path(env("OUT_DIR", "mirror"))

now = datetime.now(timezone.utc)
oldest_ts = now.timestamp() - WINDOW_DAYS * 86400
latest_ts = now.timestamp()

client = WebClient(token=SLACK_TOKEN)


def rate_limited_call(fn, **kwargs):
    """SlackApi の rate limit (429) を Retry-After に従って待つ。"""
    while True:
        try:
            return fn(**kwargs)
        except SlackApiError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", "1"))
                print(f"  rate limited, sleeping {retry_after}s", file=sys.stderr)
                time.sleep(retry_after + 1)
                continue
            raise


def list_public_channels():
    cursor = None
    while True:
        res = rate_limited_call(
            client.conversations_list,
            cursor=cursor,
            types="public_channel",
            limit=200,
        )
        for c in res["channels"]:
            if c["id"] in SKIP_CHANNELS:
                continue
            if c.get("is_archived"):
                continue
            yield c
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break


def fetch_replies(channel_id: str, ts: str):
    cursor = None
    while True:
        res = rate_limited_call(
            client.conversations_replies,
            channel=channel_id,
            ts=ts,
            cursor=cursor,
            limit=200,
        )
        for m in res["messages"]:
            if m.get("ts") != ts:
                yield m
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break


def fetch_history(channel_id: str):
    """過去 WINDOW_DAYS 日分のメッセージとそのスレッド返信を取得。"""
    cursor = None
    while True:
        res = rate_limited_call(
            client.conversations_history,
            channel=channel_id,
            cursor=cursor,
            oldest=f"{oldest_ts:.6f}",
            latest=f"{latest_ts:.6f}",
            limit=200,
        )
        for msg in res["messages"]:
            if msg.get("reply_count"):
                for r in fetch_replies(channel_id, msg["ts"]):
                    yield r
            yield msg
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break


def main():
    out_slack = OUT_DIR / "slack"
    out_slack.mkdir(parents=True, exist_ok=True)

    # 既存の mirror/slack/*.jsonl.gz を一旦消して、新しい結果だけ残す
    # (削除されたチャンネルや SKIP に追加されたチャンネルが残らないように)
    for old in out_slack.glob("*.jsonl.gz"):
        old.unlink()

    channel_count = 0
    message_count = 0
    channels_meta = []
    for c in list_public_channels():
        cid = c["id"]
        cname = c.get("name", "")
        # bot が参加していないチャンネルはそのままだと取れないので join を試みる
        if not c.get("is_member"):
            try:
                rate_limited_call(client.conversations_join, channel=cid)
            except SlackApiError as e:
                print(f"  skip {cname} ({cid}): join failed: {e.response['error']}", file=sys.stderr)
                continue

        path = out_slack / f"{cid}.jsonl.gz"
        n = 0
        with gzip.open(path, "wt", encoding="utf-8") as f:
            # 先頭に channel meta
            f.write(json.dumps({"channel_id": cid, "channel_name": cname}, ensure_ascii=False) + "\n")
            for msg in fetch_history(cid):
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                n += 1
        message_count += n
        channel_count += 1
        channels_meta.append({"id": cid, "name": cname, "messages": n})
        print(f"  {cname} ({cid}): {n} messages", file=sys.stderr)

    # users.list snapshot
    users_path = OUT_DIR / "users.json"
    users_res = rate_limited_call(client.users_list, limit=1000)
    users_path.write_text(json.dumps(users_res.data, ensure_ascii=False, indent=2))

    # sync metadata
    sync_path = OUT_DIR / "sync.json"
    sync = {
        "synced_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "window_oldest": datetime.fromtimestamp(oldest_ts, tz=timezone.utc).isoformat(),
        "window_latest": datetime.fromtimestamp(latest_ts, tz=timezone.utc).isoformat(),
        "channel_count": channel_count,
        "message_count": message_count,
        "channels": sorted(channels_meta, key=lambda x: x["name"]),
    }
    sync_path.write_text(json.dumps(sync, ensure_ascii=False, indent=2))

    print(f"DONE: {channel_count} channels, {message_count} messages", file=sys.stderr)


if __name__ == "__main__":
    main()
