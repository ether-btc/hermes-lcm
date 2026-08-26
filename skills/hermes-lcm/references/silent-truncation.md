# Silent truncation and summary health

A specific LCM failure mode is **silent deterministic truncation**: a summarization call completes, but instead of a real summary the node stores a marker like `deterministic truncation` and a stub. The row is now lossy and degrades FTS / recall / cross-session memory until repaired. This reference covers how to detect it, why it happens, the two distinct failure modes that produce it, and the knobs that fix each.

## Symptom

```sql
sqlite3 -readonly ~/.hermes/lcm.db \
  "SELECT count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%';"
```

If this returns a non-zero count, there are silent-truncation nodes. Pair it with a `source_ids<>'[]'` (recoverable) vs empty `source_ids` (unrecoverable) split to triage the population.

The same query against the depth histogram distinguishes the two failure modes — see below.

## Two distinct failure modes

A single truncation can be produced by either of two completely different mechanisms. Identifying which one you're seeing is the entire fix-selection question.

### Mode A — parent-batch-compression timeout (latency-shaped)

- Manifests as **non-leaf** nodes (depth ≥ 1) whose `source_ids` lists many children that themselves summarize large chunks.
- The summarizer is given a long input (a stitched-together batch of child summaries) and runs out of time before the local LLM finishes.
- **Knob:** raise the per-call timeout. Two layers, must move together (see "Knobs" below).

### Mode B — single-call large-input overflow (capacity-shaped)

- Manifests as **depth-0 leaf** nodes with very high `source_token_count` (typically > 2× the model's `n_ctx`).
- Example seen in production: `node_id=1760, depth=0, source_token_count=21099, summary_len=7648`, summarizer=Qwen2.5-0.5B at `n_ctx=4096`.
- The summarizer physically cannot fit the input. Raising the timeout does **nothing** — the call returns the truncation marker regardless of how long you wait.
- **Knob:** an input-token-budget guard *before* sending to the summarizer (truncate, refuse, or route to a larger-context model). Out of scope for the timeout fix.

**How to distinguish:** look at depth and `source_token_count` of new truncation nodes. If depth=0 and source_token_count >> n_ctx, it's Mode B. If depth ≥ 1 and source_token_count roughly fits n_ctx, it's Mode A.

## Knobs (Mode A only)

Two layers, read at different times, must move in lockstep to preserve a margin.

### Layer 1 — inner per-call timeout (LCM, cold-restart)

Env var `LCM_SUMMARY_TIMEOUT_MS` (default `60000`) is read by `hermes-lcm/config.py:838-842`. **However, hermes-lcm reads `auxiliary.compression.timeout` from `config.yaml` first** (`hermes-lcm/config.py:266-278`, units = seconds, multiplied by 1000 internally), and only falls back to env if YAML is unset.

- Policy-compliant: `~/.hermes/config.yaml` → `auxiliary.compression.timeout: 90` (in seconds).
- Bridge / fallback: `~/.config/hermes-gateway.env` → `LCM_SUMMARY_TIMEOUT_MS=90000`.

Per the project's `AGENTS.md`, behavioral settings belong in `config.yaml`, not `.env`. The env override is allowed as a bridge but is policy-inconsistent.

### Layer 2 — outer hygiene fence (gateway, hot-reloadable YAML)

`~/.hermes/config.yaml` → `compression.hygiene_timeout_seconds: 120` is read at `gateway/run.py:19266` alongside sibling keys `hygiene_total_ceiling_seconds` (line 19282) and `hygiene_failure_cooldown_seconds` (line 19299).

Critically, **hygiene acts as an idle fence at `gateway/run.py:19538-19572`**, and the LCM may not emit progress against it. If you raise only the inner Layer-1 timeout, the outer 30-second fence (default) can still preempt a slow-but-progressing call. The two knobs must move together so the outer fence remains ≥ 30 s longer than the inner timeout. This is the single non-obvious insight that determines whether the fix actually works.

### Cold vs hot reload

- `LCM_SUMMARY_TIMEOUT_MS` and `auxiliary.compression.timeout` are **cold-restart** knobs — they load at engine config time. `systemctl --user restart hermes-gateway` required after any change.
- `compression.hygiene_timeout_seconds` is **hot-reloadable** YAML. No restart needed.

If your config.yaml edit does not appear to take effect, you are probably looking at a hot knob that just needs reload, or a cold knob that needs a restart. Do not paper over a missing restart with another config edit.

## Verifying a fix landed correctly

Two scripts, both should exit 0.

### Re-derivation script (verify.sh pattern)

Run the literal SQL queries against the live DB and compare to your snapshot:

```bash
sqlite3 -readonly ~/.hermes/lcm.db \
  "SELECT count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%';"
```

A working snapshot script (`scripts/verify.sh` in the lcm-hygiene project) re-derives the count and exits 0 only if the live number matches the expected baseline and the expected keys are present in env / config.yaml.

### Drift-check script (templates/drift-check-script.sh)

Use after a fix lands to detect silent regressions during the observation window. Captures the L3 count and source-id-recoverable split into an append-only log so future sessions can quantify drift.

```bash
bash templates/drift-check-script.sh [baseline_l3_count]
```

Appends a line to `evidence/drift-log.txt` with `status=OK|DRIFT|UNKNOWN` plus totals, recoverable count, and unrecoverable count. Run once per session after a fix lands. If you see a `DRIFT` line within the first hour after applying a Mode-A fix, investigate before applying more knobs — the fix may have surfaced Mode B nodes that look identical in the count query.

## Recovery (separate from the fix)

Once the bleed is stopped, the existing lossy nodes still need repair. Two populations:

- **Recoverable** (`source_ids<>'[]'`): source messages still in the DB. A recovery script can re-summarize using the original source tokens.
- **Unrecoverable** (empty `source_ids`): source messages pruned. No automatic recovery; only an explicit re-summary from new evidence would work.

The lcm-hygiene project (`~/projects/lcm-hygiene/`) has a recovery script (`scripts/lcm_recover.py`) with two safety layers worth carrying forward to any recovery tool:

1. Default `--db /tmp/lcm_recovery_test.db`, not the live DB.
2. `--live` opt-in for any LLM-call path; without it the script writes deterministic stubs.

Before running `--live` against the production DB: spike on a copy first (`sqlite3 ~/.hermes/lcm.db ".backup /tmp/lcm.db.copy"`), validate the FTS postings match, run `--limit 5` then `--limit 50`, and confirm the integrity check at the end passes. Mode B nodes may still fail the LLM re-summary if the underlying context-size problem is not addressed.

## Pitfalls

- **Phantom-key trap**: a `grep -rn` scoped to one component can falsely report a YAML knob as nonexistent because the read site lives in another component. The `compression.hygiene_timeout_seconds` key was filed as a phantom in one audit because the search was scoped to `hermes-lcm/` and the read lives at `gateway/run.py:19266`. Always grep the whole repo before declaring a key phantom, and prefer the YAML read site over the audit text.
- **Cold-restart assumption**: edits to `LCM_SUMMARY_TIMEOUT_MS` or `auxiliary.compression.timeout` look like they didn't take effect if you don't restart the gateway. Don't keep editing — restart once.
- **TRIGGER-TO-INCREASE confusion**: raising the timeout further when the failure mode is Mode B does not help and exhausts your budget on impossible calls. The synthesis brief from Codex distinguishes these explicitly: "context-overflow failures do not qualify."
- **Recursion when fixing the fix**: running the drift check before the gateway has restarted will see the old config still in effect. If you find new Mode-A truncations, check that the restart actually happened and the env file was re-sourced.

## Quick triage checklist

1. `sqlite3 -readonly ~/.hermes/lcm.db "SELECT depth, count(*) FROM summary_nodes WHERE summary LIKE '%deterministic truncation%' GROUP BY depth;"` — depth histogram.
2. For depth-0 nodes with `source_token_count >> n_ctx`: Mode B, address input-token budget, not timeout.
3. For depth ≥ 1 nodes: Mode A, raise both knobs in lockstep, restart gateway, drift-check.
4. If raising the timeout does not stop the bleed, you are almost certainly in Mode B regardless of which depth the nodes are at. Re-investigate.