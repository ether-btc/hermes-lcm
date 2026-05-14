# hermes-lcm — Continue Here

**Status:** COMPLETE — pushed to `fix/dag-repair-script`
**HEAD:** `b8dc3ed` (conversation_id fix) + `3de9f1a` (repair script)
**Last Updated:** 2026-05-14 18:00 UTC

---

## Project Overview

hermes-lcm (Lossless Context Management) — context engine plugin for hermes-agent.
`context.engine: lcm` in `~/.hermes/config.yaml`.

---

## Work Done

### 1. conversation_id Fix — PUSHED (fix/dag-repair-script)
Moved `conversation_id` derivation from `command.py` display-only patch to `engine.py` `on_session_start()` lifecycle binding.
- `engine.py:838-851` `_bind_lifecycle_state()` now receives `conversation_id` from gateway kwargs
- `lifecycle_state.py:126` fallback to `session_id` when `conversation_id` is None
- Fixes: sessions across `/new` or restarts were separate lifecycle islands

### 2. DAG Repair Script — PUSHED
`repair_lcm_dag.py` — backfills `lcm_lifecycle_state` rows and fixes frontier gaps.

### 3. LCM Env Vars — PUSHED
Added to `hermes-gateway.service`:
```
LCM_CONTEXT_THRESHOLD=0.35
LCM_INCREMENTAL_MAX_DEPTH=3
LCM_FRESH_TAIL_COUNT=24
LCM_CONDENSATION_FANIN=2
LCM_DYNAMIC_LEAF_CHUNK_ENABLED=true
LCM_CACHE_FRIENDLY_CONDENSATION_ENABLED=false
```

---

## Related Work

### steezkelly/hermes-lcm — Issue #133 / PR #167
PR #167 (upstream attempt to fix issue #133) was closed unmerged.
Review found derivation happened too late and only affected display output.
Root cause: gateway never passes `conversation_id` or platform IDs (`chat_id`, `guild_id`) to `on_session_start()`.
The fix belongs in the Hermes gateway (hermes-agent), not LCM's display layer.

---

## Git State

```
Branch:      fix/dag-repair-script (pushed to origin)
origin/upstream: up to date
Working tree: clean
Open PRs:    none
```

---

## Next Steps

- Monitor new sessions for depth>0 node creation (next compression cycle)
- Consider a startup hook to run `repair_lcm_dag.py` daily
- Issue #133 (steezkelly/hermes-lcm) — requires gateway-side fix to pass conversation_id