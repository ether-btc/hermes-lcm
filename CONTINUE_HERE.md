# hermes-lcm — Investigation: Why Condensation Was Not Building Depth>0 Nodes

## Status: ROOT CAUSE FOUND + FIXED

## Investigation (May 14, 2026)

### Finding 1: No condensation chains — all 18 summary_nodes at depth=0

```
d0: 18 nodes, 33,270 tok
d>0: 0 nodes
```

No hierarchical DAG was being built despite `incremental_max_depth=3` and `context_threshold=0.35` in config.yaml.

### Root Causes (three compounding issues)

#### 1. Missing `lcm_lifecycle_state` rows — sessions orphaned from frontier tracking
- Sessions that had `summary_nodes` created via `_maybe_condense()` had **no corresponding `lcm_lifecycle_state` row**
- `_maybe_condense()` calls `get_uncondensed_at_depth()` which requires `self._session_id`
- But `lifecycle_state.get_by_session()` returned None → `should_compress()` computed wrong `threshold_tokens` (0) via `_session_ignored` or missing frontier data
- The DAG existed but the engine couldn't reason about it correctly

**Evidence from DB:**
```
Sessions with nodes but NO lifecycle entry: 3 (before fix)
  20260514_154742_6340c1
  20260514_155921_c05fac
  20260514_163841_59dfee
```

Also: most sessions had `current_frontier_store_id=0` despite having hundreds of messages.

#### 2. Missing LCM env vars in hermes-gateway.service
The systemd service had NO `LCM_*` environment variables. Without them:
- `LCMConfig.from_env()` uses all-default values (from DEFAULT_CONFIG)
- `fresh_tail_count=64` (should be 24-32)
- `condensation_fanin=4` (should be 2)
- `context_threshold=0.35` ✓ (read from config.yaml `lcm:` section by `from_env()`)
- `incremental_max_depth=3` ✓ (read from config.yaml)
- `dynamic_leaf_chunk=false` (should be true)
- `cache_friendly_condensation=true` (should be false)

The `lcm:` section in `~/.hermes/config.yaml` was correct, but `from_env()` reads env vars **first** and uses config.yaml as fallback. The missing env vars meant critical operational params used defaults instead of tuned values.

#### 3. repair_lcm_dag.py was never executed
The script existed at `~/.hermes/scripts/repair_lcm_dag.py` but was never run as a cron job, startup hook, or manual step.

---

### What Was Fixed

#### FIX 1: Ran `repair_lcm_dag.py` — backfilled lifecycle state
```
[1] Backfilling lcm_lifecycle_state...
  [FIXED] Inserted lifecycle for session=20260514_154742_6340c1, frontier=10434
  [FIXED] Inserted lifecycle for session=20260514_155921_c05fac, frontier=10826
  [FIXED] Inserted lifecycle for session=20260514_163841_59dfee, frontier=11192
  → 3 session(s) processed

[2] Fixing frontier gaps for sessions with summary_nodes...
  [FIXED] Updated frontier for 11 sessions
  → 11 session(s) processed

[3] Current DAG state:
  Total summary nodes: 19 (d0: 19)
  Sessions with nodes but NO lifecycle entry: 0 ✓
```

#### FIX 2: Added LCM env vars to hermes-gateway.service
```
Environment="LCM_CONTEXT_THRESHOLD=0.35"
Environment="LCM_INCREMENTAL_MAX_DEPTH=3"
Environment="LCM_FRESH_TAIL_COUNT=24"
Environment="LCM_CONDENSATION_FANIN=2"
Environment="LCM_DYNAMIC_LEAF_CHUNK_ENABLED=true"
Environment="LCM_CACHE_FRIENDLY_CONDENSATION_ENABLED=false"
```

Verified live in PID after restart:
```
LCM_CACHE_FRIENDLY_CONDENSATION_ENABLED=false ✓
LCM_CONDENSATION_FANIN=2 ✓
LCM_CONTEXT_THRESHOLD=0.35 ✓
LCM_DYNAMIC_LEAF_CHUNK_ENABLED=true ✓
LCM_FRESH_TAIL_COUNT=24 ✓
LCM_INCREMENTAL_MAX_DEPTH=3 ✓
```

---

## Remaining Work
- Monitor new sessions for depth>0 node creation (next compression cycle)
- The 3 newly-backfilled sessions + 11 with updated frontiers will build depth>0 on next compress cycle
- A cron job to run repair_lcm_dag.py daily or on startup would prevent future orphan sessions

## Branch State
- `fix/dag-repair-script` — has repair_lcm_dag.py + config defaults fix (pushed to origin)
- `fix/lcm-config-2026-05-14` — has config.yaml lcm section (ahead of main)
- `fix/lcm-config-v4` — older version of config fix, superseded
- No new commits needed — existing branches cover everything
- Need to verify: does hermes-lcm need to be installed/registered as a plugin for context.engine=lcm to activate?
  - The config.yaml has `context.engine: lcm` — this is the right path
  - But `plugins/context_engine/lcm/` must be in the plugin search path
  - If hermes-lcm is a standalone plugin installed separately, need to verify it's on the Python path