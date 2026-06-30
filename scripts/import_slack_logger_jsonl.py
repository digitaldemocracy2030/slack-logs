#!/usr/bin/env python3
"""
Import kuboon/slack-logger-cli-action JSONL output into raw monthly canonical logs.

The action writes one JSONL file per channel, with channel metadata on the first
line followed by Slack message objects. Thread replies may have timestamps outside
the requested month, so this importer routes every message by its own JST month
and merges it into raw/slack/<channel_id>/<YYYY-MM>.jsonl.gz.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def month_from_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=JST).strftime("%Y-%m")


def nonempty_text(record: dict) -> bool:
    return bool(str(record.get("text", "")).strip())


def keep_better(messages: dict[str, dict], record: dict) -> bool:
    ts = record.get("ts")
    if not ts:
        return False
    existing = messages.get(ts)
    if existing is None or (not nonempty_text(existing) and nonempty_text(record)):
        messages[ts] = record
        return True
    return False


def read_month(path: Path):
    channel_id = path.parent.name
    channel_name = ""
    messages: dict[str, dict] = {}
    if not path.exists():
        return channel_id, channel_name, messages

    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            ts = obj.get("ts")
            if ts:
                messages[ts] = obj
            else:
                channel_id = obj.get("channel_id", channel_id)
                channel_name = obj.get("channel_name", channel_name)
    return channel_id, channel_name, messages


def write_month(path: Path, channel_id: str, channel_name: str, messages: dict[str, dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as f:
                f.write(json_dumps({"channel_id": channel_id, "channel_name": channel_name}) + "\n")
                for ts in sorted(messages, key=lambda value: float(value)):
                    f.write(json_dumps(messages[ts]) + "\n")


def read_jsonl(path: Path):
    channel_id = path.stem
    channel_name = ""
    messages: list[dict] = []
    with path.open("rt", encoding="utf-8") as f:
        for index, line in enumerate(f, start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            ts = obj.get("ts")
            if ts:
                messages.append(obj)
            elif index == 1:
                channel_id = obj.get("channel_id", channel_id)
                channel_name = obj.get("channel_name", obj.get("name", channel_name))
    return channel_id, channel_name, messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--target-month", required=True, help="Requested month in YYYY-MM format.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    jsonl_dir = args.jsonl_dir.resolve()
    repo_root = args.repo_root.resolve()
    raw_root = repo_root / "raw" / "slack"

    if not jsonl_dir.is_dir():
        print(f"jsonl dir does not exist: {jsonl_dir}", file=sys.stderr)
        return 2

    buckets: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    channel_names: dict[str, str] = {}
    target_channels: set[str] = set()
    input_messages = 0
    target_month_messages = 0
    routed_out_of_target = 0

    for path in sorted(jsonl_dir.glob("*.jsonl")):
        cid, cname, records = read_jsonl(path)
        target_channels.add(cid)
        if cname:
            channel_names[cid] = cname
        for record in records:
            month = month_from_ts(record["ts"])
            input_messages += 1
            if month == args.target_month:
                target_month_messages += 1
            else:
                routed_out_of_target += 1
            keep_better(buckets[(cid, month)], record)

    written = 0
    total_messages_written = 0
    touched_months: set[str] = set()

    for (cid, month), incoming in sorted(buckets.items()):
        path = raw_root / cid / f"{month}.jsonl.gz"
        existing_cid, existing_name, messages = read_month(path)
        for record in incoming.values():
            keep_better(messages, record)
        if not messages:
            continue
        channel_name = channel_names.get(cid) or existing_name
        if not args.dry_run:
            write_month(path, existing_cid or cid, channel_name, messages)
        written += 1
        total_messages_written += len(messages)
        touched_months.add(month)

    # Preserve channel metadata for the requested month even when a channel had
    # no messages. This keeps the existing canonical shape without treating an
    # all-empty backup as successful.
    for cid in sorted(target_channels):
        if (cid, args.target_month) in buckets:
            continue
        path = raw_root / cid / f"{args.target_month}.jsonl.gz"
        existing_cid, existing_name, messages = read_month(path)
        if messages:
            continue
        channel_name = channel_names.get(cid) or existing_name
        if not args.dry_run:
            write_month(path, existing_cid or cid, channel_name, {})
        written += 1
        touched_months.add(args.target_month)

    print(f"jsonl_dir={jsonl_dir}")
    print(f"target_month={args.target_month}")
    print(f"input_messages={input_messages}")
    print(f"target_month_messages={target_month_messages}")
    print(f"routed_out_of_target={routed_out_of_target}")
    print(f"month_channel_files={'would_write' if args.dry_run else 'written'}={written}")
    print(f"touched_months={','.join(sorted(touched_months))}")
    print(f"total_messages_in_written_files={total_messages_written}")
    if target_month_messages == 0:
        print(f"no messages found for target month {args.target_month}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
