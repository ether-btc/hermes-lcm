"""Cycle 3 probes for hermes-lcm config.py.

Targets the cycle 1 fix + sibling functions not covered by cycle 1.
Run from /home/hermes-pi/projects/hermes-lcm:
    python3 -m pytest tests/cycle3-probes.py -xvs

Probes:
  X1: auxiliary.compression.timeout = "inf" — should fall back (NOT propagate)
  X2: auxiliary.compression.timeout = "nan" — should fall back
  X3: auxiliary.compression.timeout = "-1" — negative timeout should warn + fall back
  X4: auxiliary.compression.timeout = "1.7" — fractional silently truncates to 1
  X5: L1 — HERMES_HOME="" falls back to Path.home() / ".hermes" (silent)
  X6: cycle 1 fix didn't break pre-existing TestConfig tests (spot-check)
"""
import sys
import os
from pathlib import Path
import pytest

PROJECT_ROOT = Path("/home/hermes-pi/projects/hermes-lcm")
sys.path.insert(0, str(PROJECT_ROOT))

import hermes_lcm.config as config_mod
from hermes_lcm.config import LCMConfig


@pytest.fixture
def forced_no_yaml(monkeypatch):
    monkeypatch.setattr(config_mod, "yaml", None)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for k in (
        "LCM_CONTEXT_THRESHOLD",
        "LCM_SUMMARY_TIMEOUT_MS",
        "LCM_AUXILIARY_COMPRESSION_TIMEOUT_MS",
    ):
        monkeypatch.delenv(k, raising=False)
    return home


def test_x1_auxiliary_timeout_inf_falls_back(hermes_home, forced_no_yaml):
    """X1: auxiliary.compression.timeout: inf should fall back, not propagate."""
    (hermes_home / "config.yaml").write_text(
        "auxiliary:\n  compression:\n    timeout: inf\n"
    )
    # Use a known default; the function returns int
    val = config_mod._hermes_auxiliary_compression_timeout_ms(5000)
    # If inf propagated: int(inf * 1000) raises OverflowError, caught by
    # broad except, returns default. So this should pass.
    # But there's a silent warning log AND the function returns the default,
    # which is the expected behavior.
    assert val == 5000, f"inf propagated as {val} (OverflowError should be caught)"


def test_x2_auxiliary_timeout_nan_falls_back(hermes_home, forced_no_yaml):
    """X2: nan — int(float(nan)) raises ValueError, caught by broad except."""
    (hermes_home / "config.yaml").write_text(
        "auxiliary:\n  compression:\n    timeout: nan\n"
    )
    val = config_mod._hermes_auxiliary_compression_timeout_ms(5000)
    assert val == 5000, f"nan propagated as {val}"


def test_x3_auxiliary_timeout_negative_is_footgun(hermes_home, forced_no_yaml):
    """X3: negative timeout (-1) currently passes the float+int and propagates.

    A user with auxiliary.compression.timeout: -1 would get int(-1 * 1000) = -1000
    back from the parser. Callers that use this as a timeout would behave
    pathologically (immediate timeout).

    This is a footgun: no validation that the value is positive.
    """
    (hermes_home / "config.yaml").write_text(
        "auxiliary:\n  compression:\n    timeout: -1\n"
    )
    val = config_mod._hermes_auxiliary_compression_timeout_ms(5000)
    # BUG if val < 0 — negative timeouts are nonsensical.
    # The cycle 1 fix did not address this.
    assert val >= 0, (
        f"negative timeout propagated as {val}. "
        "BUG: should fall back to default on negative timeouts."
    )


def test_x4_auxiliary_timeout_fractional_units(hermes_home, forced_no_yaml):
    """X4: 1.7 (seconds) becomes 1700 (ms) via *1000.

    int(float("1.7") * 1000) = 1700. This is correct (seconds → ms).
    The X4 concern is that fractional milliseconds would be lost:
    int(1.0007 * 1000) = 1000 (not 1001). For a timeout, sub-millisecond
    precision is irrelevant, so this is acceptable.
    """
    (hermes_home / "config.yaml").write_text(
        "auxiliary:\n  compression:\n    timeout: 1.7\n"
    )
    val = config_mod._hermes_auxiliary_compression_timeout_ms(5000)
    assert val == 1700, f"got {val}, expected 1700 (1.7s * 1000)"


def test_x5_hermes_home_empty_falls_back_silently(monkeypatch):
    """L1: HERMES_HOME='' falls back to Path.home() / '.hermes' silently.

    Not a regression — predates the branch. Documented as L1 followup.
    """
    monkeypatch.setenv("HERMES_HOME", "")
    monkeypatch.delenv("LCM_CONTEXT_THRESHOLD", raising=False)
    c = LCMConfig.from_env()
    assert c.context_threshold == 0.35


def test_x6_cycle1_fix_defaults(tmp_path, monkeypatch):
    """X6: spot-check that cycle 1 fix didn't break the default config path.

    When HERMES_HOME points to a non-existent dir and no LCM env vars are
    set, LCMConfig should return the new defaults from the branch.
    """
    fake_home = tmp_path / "nonexistent"
    monkeypatch.setenv("HERMES_HOME", str(fake_home))
    for k in (
        "LCM_CONTEXT_THRESHOLD",
        "LCM_FRESH_TAIL_COUNT",
        "LCM_INCREMENTAL_MAX_DEPTH",
        "LCM_SUMMARY_TIMEOUT_MS",
    ):
        monkeypatch.delenv(k, raising=False)

    c = LCMConfig.from_env()
    assert c.fresh_tail_count == 32
    assert c.context_threshold == 0.35
    assert c.incremental_max_depth == 3
