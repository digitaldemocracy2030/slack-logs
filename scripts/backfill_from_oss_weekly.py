#!/usr/bin/env python3
"""
歴史月の canonical 本文を oss_weekly_reporter 週次アーカイブから復旧する一回性バックフィル。

背景:
  dd2030 Slack workspace は 2025-03 開始。無料プランの保持期限により、
  slack-logs のブートストラップ(2026-06-09)を走らせた時点で 2026-03 より前の
  本文は既に Slack から消えていた。そのため raw/slack/<id>/<2025-03..2026-02>.jsonl.gz は
  各チャンネルのメタ行のみ(本文0)になっている(issue #4)。

  本文は、同時期に取得済みの nishio/oss_weekly_reporter (data ブランチ) の
  週次アーカイブ data/<週>/raw/slack/<チャンネル名>.json に残っている。これを
  canonical の月次 jsonl.gz 形式へ変換して埋め戻す。

このスクリプトは CI ジョブではない(oss_weekly_reporter のローカル checkout が要る)。
Slack ネイティブでは復旧できないので、oss_weekly_reporter が唯一の出所。

使い方:
  # 事前に oss_weekly_reporter data ブランチを checkout しておく
  #   gh repo clone nishio/oss_weekly_reporter /path/owr -- --depth 1 -b data --single-branch
  python3 scripts/backfill_from_oss_weekly.py --owr /path/owr/data --out raw/slack

方針:
  - 週次ファイルはチャンネル "名" 基準、canonical はチャンネル "ID" 基準。現行の
    canonical メタ行と mirror/sync.json から 名→ID を作る。
  - 2025-04 末の workspace 全体 prefix 整理で改名されたチャンネルは、旧名を現行名へ
     ALIAS で寄せる(週の出現が旧名→新名で重複なく入れ替わることを確認済み)。
  - ts(epoch) を Asia/Tokyo で月に振り分け(canonical backup と同じ tz 規約)。
  - 重複 ts は最初取得分を採用(週窓が重なる分の dedup)。
  - スレッド返信も週次ファイルに含まれるのでそのまま保存される。
  - 出力は canonical と同形式: 先頭 {"channel_name": ...} メタ行 + 1メッセージ1行、gzip。
  - 復旧対象は本文0の 2025-03..2026-02 のみ。2026-03 以降は live 取得済みなので触らない。

未対応(follow-up):
  現行 workspace に存在しない(アーカイブ/削除された)チャンネルは名→ID を解決できず
  対象外。ID を得るには conversations.list(archived 含む)が要る。
"""
import argparse
import glob
import gzip
import json
import os
import re
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

# 確認済みの改名(旧 oss_weekly_reporter 名 -> 現行名)。週の出現が重複0で入れ替わる。
ALIAS = {
    "1_雑談": "7_雑談",
    "8_開発_公聴ai_figma": "8_開発_広聴ai_figma",  # 漢字修正 公聴->広聴
    "自治体_meetup_運営": "2_自治体_meetup_運営",
    "collab_ミライ構想カレッジin小布施-いどばた": "2_collab_ミライ構想カレッジin小布施-いどばた",
    "devinと人間たちの部屋": "8_devinと人間たちの部屋",
    "devin部屋": "8_devinと人間たちの部屋",
    "devin_channel": "8_devinと人間たちの部屋",
}

MONTH_MIN, MONTH_MAX = "2025-03", "2026-02"


def build_name2id(slack_root):
    id2name = {}
    for meta in glob.glob(os.path.join(slack_root, "raw/slack/*/*.jsonl.gz")):
        cid = os.path.basename(os.path.dirname(meta))
        if cid in id2name:
            continue
        try:
            with gzip.open(meta, "rt") as fh:
                nm = json.loads(fh.readline()).get("channel_name")
            if nm:
                id2name[cid] = nm
        except Exception:
            pass
    sync = os.path.join(slack_root, "mirror/sync.json")
    if os.path.exists(sync):
        for c in json.load(open(sync)).get("channels", []):
            id2name.setdefault(c["id"], c["name"])
    return id2name, {nm: cid for cid, nm in id2name.items()}


def week_range(name):
    m = re.match(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", name)
    return m.groups() if m else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owr", required=True, help="oss_weekly_reporter の data ディレクトリ")
    ap.add_argument("--out", default="raw/slack", help="出力先(canonical raw/slack)")
    ap.add_argument("--slack-root", default=".", help="slack-logs リポのルート(名→ID 解決用)")
    args = ap.parse_args()

    id2name, name2id = build_name2id(args.slack_root)

    def resolve(base):
        return name2id.get(ALIAS.get(base, base))

    # (cid, ym) -> {ts: msg}
    buckets = {}
    unmatched = set()
    for w in sorted(os.listdir(args.owr)):
        a, b = week_range(w)
        if not a or not (b >= "2025-03-01" and a <= "2026-02-28"):
            continue
        d = os.path.join(args.owr, w, "raw", "slack")
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "*.json")):
            base = os.path.basename(f)[:-5]
            if base == "summary":
                continue
            cid = resolve(base)
            if not cid:
                unmatched.add(base)
                continue
            try:
                arr = json.load(open(f))
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            for m in arr:
                ts = m.get("ts")
                if not ts:
                    continue
                ym = datetime.fromtimestamp(float(ts), JST).strftime("%Y-%m")
                if ym < MONTH_MIN or ym > MONTH_MAX:
                    continue
                buckets.setdefault((cid, ym), {}).setdefault(ts, m)

    files = msgs = 0
    for (cid, ym), mm in sorted(buckets.items()):
        od = os.path.join(args.out, cid)
        os.makedirs(od, exist_ok=True)
        with gzip.open(os.path.join(od, ym + ".jsonl.gz"), "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"channel_name": id2name[cid]}, ensure_ascii=False) + "\n")
            for m in sorted(mm.values(), key=lambda x: float(x["ts"])):
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
                msgs += 1
        files += 1

    print(f"wrote {files} channel-month files, {msgs} messages")
    if unmatched:
        print(f"skipped {len(unmatched)} channels with no current id (archived/deleted): "
              + ", ".join(sorted(unmatched)))


if __name__ == "__main__":
    main()
