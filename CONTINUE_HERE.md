# hermes-lcm — Config Fix: DAG Depth + Compression Threshold (May 2026)

## Status: COMPLETED — Pushed to Fork

## Problem
Three compounding configuration issues prevented LCM from working as intended:
1. `incremental_max_depth=1` — DAG was flat, only one compression level (no hierarchical summaries)
2. `context_threshold=0.75` (default) → compression threshold at 153,600 tokens for MiniMax-M2.7 — far too late, caused "context length exceeded" errors
3. `fresh_tail_count=64` — too many recent messages protected per compression cycle

Additionally, `_hermes_compression_threshold()` only read `compression.threshold` from config.yaml, not `lcm.context_threshold`.

## What Was Changed

### config.py (defaults + `_hermes_compression_threshold`)
```
fresh_tail_count:     64 → 32
context_threshold:   0.75 → 0.35
incremental_max_depth: 1 → 3
```
`_hermes_compression_threshold()` now reads `lcm.context_threshold` from config.yaml first, falling back to `compression.threshold`, then to default.

### config.yaml (new `lcm:` section)
```yaml
lcm:
  context_threshold: 0.35
  incremental_max_depth: 3
  fresh_tail_count: 32
```

### tests/test_lcm_core.py
- `test_defaults`: updated assertions to new values (32, 0.35, 3)
- `test_from_env_invalid_numeric_values_fall_back_to_defaults`: same
- Added `test_from_env_lcm_section_overrides_compression_section`

## Verification
- `LCMConfig()` → fresh_tail_count=32, context_threshold=0.35, incremental_max_depth=3 ✓
- `LCMConfig.from_env()` → reads config.yaml lcm section correctly ✓
- `_hermes_compression_threshold()` → 0.35 (from config.yaml, not compression.threshold=0.5) ✓
- `test_lcm_core.py::TestConfig` → 176 passed ✓

## Effect
- MiniMax-M2.7 (204,800 context): compression now fires at **71,680 tokens** (was 153,600)
- DAG can build depth-3 hierarchical summaries (leaf → d1 → d2 → d3)
- 32-message fresh tail (was 64) — more context density per compression

## Remaining
- Monitor sessions for correct compression behavior after restart
- Track DAG depth distribution to confirm hierarchical compression building