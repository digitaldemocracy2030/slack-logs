#!/usr/bin/env python3
"""Validate raw/slack monthly canonical JSONL gzip files."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def month_from_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=JST).strftime("%Y-%m")


def iter_month_files(raw_root: Path, month: str | None):
    pattern = f"*/{month}.jsonl.gz" if month else "*/*.jsonl.gz"
    yield from sorted(raw_root.glob(pattern))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--month", help="Validate only this YYYY-MM file set.")
    parser.add_argument("--require-messages", action="store_true")
    args = parser.parse_args()

    raw_root = args.repo_root.resolve() / "raw" / "slack"
    files = list(iter_month_files(raw_root, args.month))
    if not files:
        print(f"no raw files found for {args.month or 'all months'}", file=sys.stderr)
        return 1

    total_messages = 0
    out_of_month = 0
    bad_json = 0
    duplicate_ts = 0
    month_counts: dict[str, int] = defaultdict(int)

    for path in files:
        expected_month = path.name.removesuffix(".jsonl.gz")
        seen_ts: set[str] = set()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        bad_json += 1
                        print(f"{path}:{line_no}: invalid JSON: {exc}", file=sys.stderr)
                        continue
                    ts = obj.get("ts")
                    if not ts:
                        continue
                    total_messages += 1
                    actual_month = month_from_ts(ts)
                    month_counts[actual_month] += 1
                    if actual_month != expected_month:
                        out_of_month += 1
                        print(
                            f"{path}:{line_no}: ts {ts} belongs to {actual_month}, expected {expected_month}",
                            file=sys.stderr,
                        )
                    if ts in seen_ts:
                        duplicate_ts += 1
                        print(f"{path}:{line_no}: duplicate ts within file: {ts}", file=sys.stderr)
                    seen_ts.add(ts)
        except OSError as exc:
            bad_json += 1
            print(f"{path}: cannot read gzip: {exc}", file=sys.stderr)

    print(f"files={len(files)}")
    print(f"messages={total_messages}")
    print(f"out_of_month={out_of_month}")
    print(f"bad_json={bad_json}")
    print(f"duplicate_ts_within_file={duplicate_ts}")
    print("message_months=" + ",".join(f"{k}:{v}" for k, v in sorted(month_counts.items())))

    if args.require_messages and total_messages == 0:
        print("required message count is zero", file=sys.stderr)
        return 1
    if bad_json or out_of_month or duplicate_ts:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
