#!/usr/bin/env python3
"""Rewrite raw/slack files so every message is stored in its own JST month."""

from __future__ import annotations

import argparse
import gzip
import io
import json
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


def keep_better(messages: dict[str, dict], record: dict) -> None:
    ts = record.get("ts")
    if not ts:
        return
    existing = messages.get(ts)
    if existing is None or (not nonempty_text(existing) and nonempty_text(record)):
        messages[ts] = record


def read_file(path: Path):
    channel_id = path.parent.name
    channel_name = ""
    messages: dict[str, dict] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            ts = obj.get("ts")
            if ts:
                keep_better(messages, obj)
            else:
                channel_id = obj.get("channel_id", channel_id)
                channel_name = obj.get("channel_name", channel_name)
    return channel_id, channel_name, messages


def write_file(path: Path, channel_id: str, channel_name: str, messages: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as f:
                f.write(json_dumps({"channel_id": channel_id, "channel_name": channel_name}) + "\n")
                for ts in sorted(messages, key=lambda value: float(value)):
                    f.write(json_dumps(messages[ts]) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--month", action="append", help="Only scan this source YYYY-MM month. Repeatable.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw_root = args.repo_root.resolve() / "raw" / "slack"
    source_months = set(args.month or [])
    paths = sorted(raw_root.glob("*/*.jsonl.gz"))
    if source_months:
        paths = [p for p in paths if p.name.removesuffix(".jsonl.gz") in source_months]

    channel_names: dict[str, str] = {}
    buckets: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    existing_target_paths: set[Path] = set()
    scanned = 0
    moved = 0

    for path in paths:
        source_month = path.name.removesuffix(".jsonl.gz")
        cid, cname, messages = read_file(path)
        scanned += len(messages)
        if cname:
            channel_names[cid] = cname
        for record in messages.values():
            actual_month = month_from_ts(record["ts"])
            if actual_month != source_month:
                moved += 1
            keep_better(buckets[(cid, actual_month)], record)
        existing_target_paths.add(path)

    for (cid, month) in buckets:
        existing_target_paths.add(raw_root / cid / f"{month}.jsonl.gz")

    rewritten = 0
    for path in sorted(existing_target_paths):
        cid = path.parent.name
        month = path.name.removesuffix(".jsonl.gz")
        incoming = buckets.get((cid, month), {})
        if path.exists() and path not in paths:
            existing_cid, existing_name, existing = read_file(path)
            cid = existing_cid or cid
            for record in existing.values():
                keep_better(incoming, record)
            if existing_name:
                channel_names.setdefault(cid, existing_name)
        if not incoming and not path.exists():
            continue
        if not args.dry_run:
            write_file(path, cid, channel_names.get(cid, ""), incoming)
        rewritten += 1

    print(f"source_files={len(paths)}")
    print(f"messages_scanned={scanned}")
    print(f"messages_moved_to_different_month={moved}")
    print(f"month_channel_files={'would_rewrite' if args.dry_run else 'rewritten'}={rewritten}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
