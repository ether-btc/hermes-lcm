#!/usr/bin/env python3
"""
LCM DAG repair script: backfill lcm_lifecycle_state entries for sessions that
have summary_nodes but no lifecycle entry, and rebuild the DAG frontier for
orphaned sessions so condensation works correctly.

Usage:
    python3 ~/.hermes/scripts/repair_lcm_dag.py [--dry-run]

Root causes fixed:
  1. Sessions with summary_nodes but NO lcm_lifecycle_state entry
     → lcm_lifecycle_state.bind_session() was never called for these sessions
  2. Sessions with messages but no frontier advance after compression
     → _persist_frontier_marker() not called for sessions not via lifecycle
  3. cache_friendly_condensation suppressing condensation when debt is 1 group
     → With fanin=4, min_debt_groups=2, needed 8 uncondensed before follow-on
     → Fixed via LCM_CONDENSATION_FANIN=2 in hermes-gateway.service

Env vars (from hermes-gateway.service):
    LCM_CONTEXT_THRESHOLD=0.35
    LCM_INCREMENTAL_MAX_DEPTH=3
    LCM_FRESH_TAIL_COUNT=24
    LCM_CONDENSATION_FANIN=2
    LCM_DYNAMIC_LEAF_CHUNK_ENABLED=true
    LCM_CACHE_FRIENDLY_CONDENSATION_ENABLED=false
"""

import os
import sys
import time
import sqlite3
import argparse

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
DB_PATH = os.path.join(HERMES_HOME, "lcm.db")


def get_session_frontier(conn, session_id: str) -> int:
    """Get the last store_id for a session (last message store_id)."""
    result = conn.execute(
        "SELECT MAX(store_id) FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    return result or 0


def backfill_lifecycle_state(conn, dry_run: bool = False) -> int:
    """Add lcm_lifecycle_state entries for sessions that have summary_nodes but no entry."""
    rows = conn.execute("""
        SELECT DISTINCT sn.session_id
        FROM summary_nodes sn
        LEFT JOIN lcm_lifecycle_state lcs ON lcs.conversation_id = sn.session_id
        WHERE lcs.conversation_id IS NULL
    """).fetchall()

    fixed = 0
    for (session_id,) in rows:
        frontier = get_session_frontier(conn, session_id)
        label = session_id[:30]
        if dry_run:
            print(f"  [DRY RUN] Would insert lifecycle for session={label}, frontier={frontier}")
        else:
            now = time.time()
            conn.execute("""
                INSERT OR IGNORE INTO lcm_lifecycle_state(
                    conversation_id, current_session_id, last_finalized_session_id,
                    current_frontier_store_id, last_finalized_frontier_store_id,
                    debt_kind, debt_size_estimate, current_bound_at, last_finalized_at,
                    debt_updated_at, last_maintenance_attempt_at, last_rollover_at,
                    last_reset_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, session_id, session_id,
                frontier, frontier,
                None, 0, now, now, None, None, now, None, now,
            ))
            print(f"  [FIXED] Inserted lifecycle for session={label}, frontier={frontier}")
        fixed += 1

    return fixed


def fix_frontier_gaps(conn, dry_run: bool = False) -> int:
    """Update current_frontier_store_id for sessions that have summary_nodes."""
    rows = conn.execute("SELECT DISTINCT session_id FROM summary_nodes").fetchall()

    fixed = 0
    for (session_id,) in rows:
        # Get max store_id across all source_ids in this session's nodes
        max_src_id = conn.execute("""
            SELECT MAX(CAST(value AS INTEGER))
            FROM summary_nodes, json_each(source_ids)
            WHERE session_id = ?
        """, (session_id,)).fetchone()[0]

        if max_src_id is None:
            continue

        label = session_id[:30]
        if dry_run:
            print(f"  [DRY RUN] Would update frontier for session={label}: -> {max_src_id}")
        else:
            conn.execute("""
                UPDATE lcm_lifecycle_state
                SET current_frontier_store_id = MAX(current_frontier_store_id, ?),
                    last_finalized_frontier_store_id = MAX(last_finalized_frontier_store_id, ?),
                    updated_at = ?
                WHERE conversation_id = ?
            """, (max_src_id, max_src_id, time.time(), session_id))
            print(f"  [FIXED] Updated frontier for session={label}: -> {max_src_id}")
        fixed += 1

    return fixed


def report_state(conn) -> None:
    """Print current DAG and lifecycle state."""
    total_nodes = conn.execute("SELECT COUNT(*) FROM summary_nodes").fetchone()[0]
    depth_dist = conn.execute("""
        SELECT depth, COUNT(*), SUM(token_count), SUM(source_token_count)
        FROM summary_nodes GROUP BY depth ORDER BY depth
    """).fetchall()

    print(f"\n  Total summary nodes: {total_nodes}")
    for d, cnt, tok, src_tok in depth_dist:
        ratio = src_tok / tok if tok else 0
        print(f"    d{d}: {cnt} nodes, {tok:,} tok, {ratio:.1f}x src ratio")

    sessions_with_nodes = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM summary_nodes"
    ).fetchone()[0]
    total_sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM messages"
    ).fetchone()[0]
    print(f"\n  Sessions with nodes: {sessions_with_nodes}/{total_sessions}")

    # Check for truly orphaned sessions (no lifecycle reference in any field)
    orphan = conn.execute("""
        SELECT COUNT(DISTINCT sn.session_id)
        FROM summary_nodes sn
        LEFT JOIN lcm_lifecycle_state lcs 
            ON lcs.conversation_id = sn.session_id
            OR lcs.current_session_id = sn.session_id
            OR lcs.last_finalized_session_id = sn.session_id
        WHERE lcs.rowid IS NULL
    """).fetchone()[0]
    print(f"  Sessions with nodes but NO lifecycle reference: {orphan}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair LCM DAG lifecycle state")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    print("=" * 60)
    print("LCM DAG REPAIR")
    print("=" * 60)

    print("\n[1] Backfilling lcm_lifecycle_state for sessions with nodes but no entry...")
    fixed = backfill_lifecycle_state(conn, dry_run=args.dry_run)
    print(f"  → {fixed} session(s) processed")

    print("\n[2] Fixing frontier gaps for sessions with summary_nodes...")
    fixed2 = fix_frontier_gaps(conn, dry_run=args.dry_run)
    print(f"  → {fixed2} session(s) processed")

    print("\n[3] Current DAG state:")
    report_state(conn)

    conn.close()

    if args.dry_run:
        print("\n[DRY RUN] No changes made. Remove --dry-run to apply fixes.")
    else:
        print("\n[DONE] LCM DAG repair complete.")
        print("  Note: New compression cycles will build depth>0 nodes.")
        print("  Existing depth-0 nodes are preserved.")


if __name__ == "__main__":
    main()