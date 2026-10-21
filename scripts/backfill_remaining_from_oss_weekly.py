#!/usr/bin/env python3
"""
oss_weekly_reporter に残っていて canonical に無い Slack 本文を、すべて埋め戻す一回性バックフィル
(backfill_from_oss_weekly.py の follow-up)。

backfill_from_oss_weekly.py が拾えなかったもの:
  1. 最初期の週ディレクトリ(data/2025-03-*)は raw/slack/ を持たない旧配置で、
     チャンネル別 JSON が週ディレクトリ直下にある → 2025-03 が丸ごと漏れていた
  2. 現行名に無いチャンネル名(改名・アーカイブ・削除) → 名→ID が解決できず対象外だった
  3. 2026-03-01〜03-12 は、ブートストラップ(2026-06-09)時点で 90 日の保持期限を過ぎていて
     live 取得に入っていない

方針:
  - 名→ID は canonical メタ行 + mirror/sync.json + conversations.list(archived 含む)の
    スナップショット(--channels, [{"id","name"}])で作る
  - 改名は ALIAS で現行名へ寄せる。根拠はどれも「週の出現が重ならない」に加えて、
    旧名の最初のメッセージと現行チャンネルの created が一致する、または同じ ts のメッセージが
    両方の名前に現れる、のいずれか(下のコメント)
  - ID が取れない削除済みチャンネルは raw/slack/deleted-<旧名>/ に置く
  - 既存ファイルの行と並びは変えない。既存に無い ts だけを足す。ts 順のファイルには順に差し込み、
    live 取得のファイル(スレッド単位の並び)には先頭に置く
  - 対象月は 2025-03..2026-03。2026-04 以降は live 取得の範囲なので触らない

使い方:
  python3 scripts/backfill_remaining_from_oss_weekly.py --owr /path/owr/data --channels channels.json
"""
import argparse
import glob
import gzip
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from backfill_from_oss_weekly import ALIAS as ALIAS_V1, build_name2id  # noqa: E402

JST = timezone(timedelta(hours=9))
MONTH_MIN, MONTH_MAX = "2025-03", "2026-03"

ALIAS = dict(ALIAS_V1)
ALIAS.update({
    # 2025-04 末の prefix 整理(週の出現が旧名→新名で重複0で入れ替わる)
    "2_開発_広聴ai_github": "8_開発_広聴ai_github",
    "2_開発_いどばた_github": "8_開発_いどばた_github",
    "学生のためのデジタル民主主義": "2_学生のためのデジタル民主主義",
    "3_事例紹介_全体": "1_事例紹介_全体",
    "2_デザイン_general": "3_デザイン_general",
    "1_freetalk-in-english": "7_freetalk-in-english",
    "9_ai_coding知見共有部屋": "7_ai_coding知見共有部屋",
    "若者の雑談チャンネル": "7_若者の雑談チャンネル",
    # 2_開発_政治資金ボード は 2_開発_polimoney と同じ ts のメッセージを6件共有。
    # 2_開発_政治資金透明化ツール(03-05..03-29)→ボード(04-02..04-03)→polimoney(04-07..)と
    # 期間が連続し重ならない。polimoney は 2025-03-01 作成なのに 4 月まで現行名の出現が無い
    "2_開発_政治資金透明化ツール": "2_開発_polimoney",
    "2_開発_政治資金ボード": "2_開発_polimoney",
    # 0412_1day_hackathon の最初の channel_join(1743254920) = 0412_1day_meetup_運営 の created。
    # 0412_1day_meetup も同じ ts のメッセージを7件共有
    "0412_1day_hackathon": "0412_1day_meetup_運営",
    "0412_1day_meetup": "0412_1day_meetup_運営",
    # 最初の channel_join(1744530688) が 2_コミュニティ運営 の created(1744530687)と1秒差
    "2_コミュニティ運営を考える": "2_コミュニティ運営",
})


def iter_week_files(owr):
    for w in sorted(os.listdir(owr)):
        wd = os.path.join(owr, w)
        if not os.path.isdir(wd):
            continue
        for f in sorted(glob.glob(os.path.join(wd, "raw", "slack", "*.json"))
                        + glob.glob(os.path.join(wd, "*.json"))):
            base = os.path.basename(f)[:-5]
            if base != "summary":
                yield base, f


def read_canon(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return lines[0], lines[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owr", required=True, help="oss_weekly_reporter の data ディレクトリ")
    ap.add_argument("--channels", required=True, help="conversations.list(archived 含む)の id/name スナップショット")
    ap.add_argument("--slack-root", default=".")
    args = ap.parse_args()

    id2name, name2id = build_name2id(args.slack_root)
    for c in json.load(open(args.channels)):
        id2name.setdefault(c["id"], c["name"])
        name2id.setdefault(c["name"], c["id"])

    def resolve(base):
        cid = name2id.get(ALIAS.get(base, base))
        return cid if cid else "deleted-" + base

    buckets = {}  # (dir, ym) -> {ts: msg}
    for base, f in iter_week_files(args.owr):
        try:
            arr = json.load(open(f))
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        d = resolve(base)
        if d.startswith("deleted-"):
            id2name.setdefault(d, base)
        for m in arr:
            ts = isinstance(m, dict) and m.get("ts")
            if not ts:
                continue
            ym = datetime.fromtimestamp(float(ts), JST).strftime("%Y-%m")
            if MONTH_MIN <= ym <= MONTH_MAX:
                buckets.setdefault((d, ym), {}).setdefault(ts, m)

    stats = {}
    for (d, ym), mm in sorted(buckets.items()):
        path = os.path.join(args.slack_root, "raw", "slack", d, ym + ".jsonl.gz")
        if os.path.exists(path):
            meta, lines = read_canon(path)
        else:
            meta, lines = json.dumps({"channel_name": id2name[d]}, ensure_ascii=False), []
        have = {json.loads(l)["ts"] for l in lines}
        add = [m for ts, m in mm.items() if ts not in have]
        if not add:
            continue
        old = [(float(json.loads(l)["ts"]), l) for l in lines]
        new = sorted(((float(m["ts"]), json.dumps(m, ensure_ascii=False)) for m in add),
                     key=lambda r: r[0])
        if all(old[i][0] <= old[i + 1][0] for i in range(len(old) - 1)):
            rows = sorted(old + new, key=lambda r: r[0])  # 安定ソートなので既存行の順は保たれる
        else:
            # live 取得のファイルはスレッド単位の並び(親の後に返信)で ts 順ではない。
            # 既存行の並びには手を付けず、それより前の時期の復旧分を先頭に置く
            rows = new + old
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(meta + "\n")
            for _, l in rows:
                fh.write(l + "\n")
        stats[(d, ym)] = len(add)

    print(f"updated {len(stats)} channel-month files, added {sum(stats.values())} messages")
    by_dir = {}
    for (d, _), n in stats.items():
        by_dir[d] = by_dir.get(d, 0) + n
    for d, n in sorted(by_dir.items(), key=lambda x: -x[1]):
        print(f"  {n:5d} {d} {id2name.get(d, '')}")


if __name__ == "__main__":
    main()
