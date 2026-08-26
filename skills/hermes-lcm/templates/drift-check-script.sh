#!/usr/bin/env bash
# drift-check.sh — observation-window drift log for LCM silent-truncation fixes
#
# Use after applying a fix for the parent-batch-compression timeout failure
# mode (Mode A in references/silent-truncation.md). Records L3 count and
# source-id-recoverable split into an append-only log so future sessions can
# quantify drift relative to the post-fix baseline.
#
# Run once per session. Idempotent. Never modifies the DB, the env, or the YAML.
#
# Usage: bash templates/drift-check-script.sh [baseline_l3_count] [log_path]
#   baseline_l3_count: pre-fix L3 count (default 0 = any growth is DRIFT)
#   log_path: append-only drift log (default ./drift-log.txt)

set -u

DB="${LCM_DB:-${HOME}/.hermes/lcm.db}"
BASELINE="${1:-0}"
LOG="${2:-./drift-log.txt}"

if [[ ! -r "$DB" ]]; then
    echo "FATAL: cannot read $DB" >&2
    exit 2
fi

total="$(sqlite3 -readonly "$DB" 'SELECT count(*) FROM summary_nodes;')"
l3="$(sqlite3 -readonly "$DB" "SELECT count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%';")"
unrecoverable="$(sqlite3 -readonly "$DB" "SELECT count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%' AND (source_ids='[]' OR source_ids IS NULL OR source_ids='');")"
recoverable="$(sqlite3 -readonly "$DB" "SELECT count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%' AND source_ids<>'[]';")"

delta=$(( l3 - BASELINE ))
if (( BASELINE == 0 )); then
    status="BASELINE"
elif (( delta > 0 )); then
    status="DRIFT"
elif (( delta < 0 )); then
    status="RECOVERED"
else
    status="OK"
fi

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf "%s\tstatus=%s\ttotal=%s\tl3=%s\tdelta_vs_baseline=%+d\tunrecoverable=%s\trecoverable=%s\n" \
    "$ts" "$status" "$total" "$l3" "$delta" "$unrecoverable" "$recoverable" \
    >> "$LOG"

echo "$ts $status l3=$l3 (baseline=$BASELINE, delta=$delta) unrecoverable=$unrecoverable recoverable=$recoverable"
echo "appended to $LOG"