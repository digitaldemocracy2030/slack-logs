#!/usr/bin/env python3
"""
Import historical public Slack messages from nishio/oss_weekly_reporter data.

This is a one-shot repair tool for the initial slack-logs backfill where many
monthly canonical files were committed as metadata-only files. The importer
reads oss_weekly_reporter weekly raw Slack JSON files and merges their messages
into raw/slack/<channel_id>/<YYYY-MM>.jsonl.gz.
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


def default_archive_root() -> Path:
    candidates = [
        Path("../oss_weekly_reporter/data"),
        Path("/tmp/oss_weekly_reporter/data"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def month_from_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=JST).strftime("%Y-%m")


def iter_summary_files(archive_root: Path):
    seen: set[Path] = set()
    for path in archive_root.rglob("summary.json"):
        if path in seen:
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            print(f"skip unreadable summary {path}: {exc}", file=sys.stderr)
            continue
        channels = data.get("channels") if isinstance(data, dict) else None
        if isinstance(channels, list):
            yield path, channels


def iter_archive_messages(archive_root: Path):
    seen_files: set[Path] = set()
    for summary_path, channels in iter_summary_files(archive_root):
        for channel in channels:
            cid = channel.get("id")
            cname = channel.get("name", "")
            file_name = channel.get("file")
            if not cid or not file_name:
                continue
            data_path = archive_root / file_name
            if data_path in seen_files:
                continue
            seen_files.add(data_path)
            if not data_path.exists():
                print(f"missing archived channel file listed in {summary_path}: {data_path}", file=sys.stderr)
                continue
            try:
                records = json.loads(data_path.read_text())
            except Exception as exc:
                print(f"skip unreadable channel file {data_path}: {exc}", file=sys.stderr)
                continue
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                ts = record.get("ts")
                if not ts:
                    continue
                yield cid, cname, record, data_path


def read_existing_month(path: Path):
    messages: dict[str, dict] = {}
    channel_name = ""
    if not path.exists():
        return channel_name, messages
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            ts = obj.get("ts")
            if ts:
                messages[ts] = obj
            else:
                channel_name = obj.get("channel_name", channel_name)
    return channel_name, messages


def write_month(path: Path, channel_id: str, channel_name: str, messages: dict[str, dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as f:
                f.write(json_dumps({"channel_id": channel_id, "channel_name": channel_name}) + "\n")
                for ts in sorted(messages, key=lambda value: float(value)):
                    f.write(json_dumps(messages[ts]) + "\n")


def nonempty_text(record: dict) -> bool:
    return bool(str(record.get("text", "")).strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, default=default_archive_root())
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--max-month", help="Do not import messages after this YYYY-MM month.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    archive_root = args.archive_root.resolve()
    repo_root = args.repo_root.resolve()
    raw_root = repo_root / "raw" / "slack"

    if not archive_root.exists():
        print(f"archive root does not exist: {archive_root}", file=sys.stderr)
        return 2

    buckets: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    channel_names: dict[str, str] = {}
    archive_records = 0
    imported_records = 0

    for cid, cname, record, _source_path in iter_archive_messages(archive_root):
        month = month_from_ts(record["ts"])
        if args.max_month and month > args.max_month:
            continue
        archive_records += 1
        key = (cid, month)
        channel_names.setdefault(cid, cname)
        existing = buckets[key].get(record["ts"])
        if existing is None or (not nonempty_text(existing) and nonempty_text(record)):
            buckets[key][record["ts"]] = record
            imported_records += 1

    written = 0
    total_messages = 0
    for (cid, month), archive_messages in sorted(buckets.items()):
        path = raw_root / cid / f"{month}.jsonl.gz"
        existing_name, existing_messages = read_existing_month(path)
        channel_name = channel_names.get(cid) or existing_name
        merged = dict(existing_messages)
        for ts, record in archive_messages.items():
            existing = merged.get(ts)
            if existing is None or (not nonempty_text(existing) and nonempty_text(record)):
                merged[ts] = record
        if not merged:
            continue
        total_messages += len(merged)
        if not args.dry_run:
            write_month(path, cid, channel_name, merged)
        written += 1

    print(f"archive_root={archive_root}")
    print(f"archive_records_seen={archive_records}")
    print(f"archive_records_selected={imported_records}")
    print(f"month_channel_files={'would_write' if args.dry_run else 'written'}={written}")
    print(f"total_messages_in_written_files={total_messages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
