#!/usr/bin/env python3
"""
slack-logger-cli-action の出力（チャンネルごとの jsonl）を raw/slack/<channel_id>/<YYYY-MM>.jsonl.gz に置く。

- 同じ ts の行は最初の 1 行だけ残す。「チャンネルにも送信」した返信（thread_broadcast）は、
  チャンネルの履歴とスレッドの返信の両方から返ってくるため、そのままだと 2 行になる
- 既存ファイルにある ts が新しい取得に 1 つでも無ければ、そのチャンネルは上書きしない。
  Slack 無料プランは約 90 日より古い本文を返さないので、古い月を workflow_dispatch で取り直すと
  本文 0 件になり、oss_weekly_reporter から埋め戻した本文（Slack からは再取得できない）を消してしまう。
  raw/ は append-only の保全層なので、行が減る上書きはしない

使い方:
  python3 scripts/place_backup_jsonl.py --jsonl-dir <action の jsonl_dir> --month 2026-08
"""
import argparse
import gzip
import json
from pathlib import Path


def message_ts(line):
    return json.loads(line).get("ts")


def existing_ts(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    return {message_ts(l) for l in lines[1:]} - {None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl-dir", required=True)
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()

    written = skipped = dropped = 0
    for src in sorted(Path(args.jsonl_dir).glob("*.jsonl")):
        lines = [l for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        meta, body = lines[:1], lines[1:]

        seen, out = set(), []
        for line in body:
            ts = message_ts(line)
            if ts is not None and ts in seen:
                dropped += 1
                continue
            seen.add(ts)
            out.append(line)
        seen.discard(None)

        dest = Path(args.repo_root) / "raw" / "slack" / src.stem / f"{args.month}.jsonl.gz"
        if dest.exists():
            lost = existing_ts(dest) - seen
            if lost:
                print(f"::warning::{dest}: 既存の {len(lost)} 件が今回の取得（{len(seen)} 件）に無いので上書きしない")
                skipped += 1
                continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(("\n".join(meta + out) + "\n").encode("utf-8"))
        written += 1

    print(f"written={written} skipped={skipped} duplicate_lines_dropped={dropped}")


if __name__ == "__main__":
    main()
