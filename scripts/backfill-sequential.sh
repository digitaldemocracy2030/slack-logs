#!/bin/bash
# Sequentially dispatch slack-backup.yml for each YYYY-MM in args.
#
# GitHub Actions の concurrency 仕様（cancel-in-progress: false でも新しい pending
# が古い pending を cancel する）を回避するため、外部で完了待ちしながら 1件ずつ投げる。
#
# 使い方:
#   scripts/backfill-sequential.sh 2025-01 2025-02 2025-03
#
# 必要: gh CLI に digitaldemocracy2030/slack-logs への write 権限

set -euo pipefail

REPO=digitaldemocracy2030/slack-logs
WORKFLOW=slack-backup.yml

if [ $# -eq 0 ]; then
  echo "Usage: $0 YYYY-MM [YYYY-MM ...]" >&2
  exit 1
fi

RESULTS=()
for ym in "$@"; do
  y=${ym%-*}; m=${ym#*-}; m=$((10#$m))
  echo "=== dispatching $ym ==="
  gh workflow run "$WORKFLOW" -R "$REPO" -f year="$y" -f month="$m"
  sleep 8
  rid=$(gh run list -R "$REPO" --workflow="$WORKFLOW" --limit 1 --json databaseId --jq '.[0].databaseId')
  echo "run id: $rid"
  while true; do
    j=$(gh run view "$rid" -R "$REPO" --json status,conclusion 2>/dev/null) || { sleep 15; continue; }
    st=$(jq -r '.status' <<<"$j")
    cc=$(jq -r '.conclusion' <<<"$j")
    if [ "$st" = "completed" ]; then
      echo "=== $ym -> $cc ==="
      RESULTS+=("$ym=$cc")
      break
    fi
    sleep 25
  done
done

echo ""
echo "=== ALL DONE ==="
printf '%s\n' "${RESULTS[@]}"
