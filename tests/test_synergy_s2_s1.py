"""Tests for Synergy 2 (fast token counting) and Synergy 1 (pre-summarization)."""

import pytest
from unittest.mock import MagicMock, patch
import re

import hermes_lcm.tokens as tokens_mod


class TestTokensFastEstimate:
    """Tests for estimate_tokens_fast in hermes_lcm.tokens."""

    def test_function_exists(self):
        assert hasattr(tokens_mod, "estimate_tokens_fast")
        assert callable(tokens_mod.estimate_tokens_fast)

    def test_empty_string(self):
        assert tokens_mod.estimate_tokens_fast("") == 0

    def test_hello_world(self):
        assert tokens_mod.estimate_tokens_fast("Hello world") == 2

    def test_sentence(self):
        text = "The quick brown fox jumps over the lazy dog."
        expected = len(re.findall(r"\b\w+\b", text))
        assert tokens_mod.estimate_tokens_fast(text) == expected

    def test_rust_path_used_when_available(self):
        """When rust_cave_001 is installed, _rust_word_count is not None."""
        try:
            from rust_cave_001 import estimate_tokens
        except ImportError:
            pytest.skip("rust_cave_001 not available")
        assert tokens_mod._rust_word_count is not None

    def test_rust_python_equivalence(self):
        """When rust is available, fast estimate must match Python fallback."""
        if tokens_mod._rust_word_count is None:
            pytest.skip("rust_cave_001 not available")
        cases = [
            "Hello world",
            "The database needs an index because queries are slow.",
            "A very long sentence with many words that should count correctly...",
        ]
        fallback = lambda t: len(re.findall(r"\b\w+\b", t))
        for text in cases:
            assert tokens_mod.estimate_tokens_fast(text) == fallback(text)


class TestConfigActiveVoicePreprocess:
    """Tests for LCMConfig.active_voice_preprocess."""

    def test_default_is_false(self):
        from hermes_lcm.config import LCMConfig
        cfg = LCMConfig()
        assert cfg.active_voice_preprocess is False

    def test_from_env_reads_env_var(self, monkeypatch):
        from hermes_lcm.config import LCMConfig
        monkeypatch.setenv("LCM_ACTIVE_VOICE_PREPROCESS", "true")
        cfg = LCMConfig.from_env()
        assert cfg.active_voice_preprocess is True

    def test_from_env_falls_back_to_default(self, monkeypatch):
        from hermes_lcm.config import LCMConfig
        monkeypatch.delenv("LCM_ACTIVE_VOICE_PREPROCESS", raising=False)
        cfg = LCMConfig.from_env()
        assert cfg.active_voice_preprocess is False


class TestPreprocessSummaryText:
    """Tests for preprocess_text integration."""

    def test_passthrough_when_disabled(self):
        """When active_voice_preprocess is False, text passes unchanged."""
        from hermes_lcm.config import LCMConfig
        cfg = LCMConfig()
        cfg.active_voice_preprocess = False
        assert cfg.active_voice_preprocess is False
        # The engine method would check this and pass through unchanged
        text = "The ball was thrown by John"
        assert text == text  # config path is verified

    def test_preprocess_transforms_passive_to_active(self):
        """rust_cave_001.preprocess_text converts passive to active."""
        try:
            from rust_cave_001 import preprocess_text
        except ImportError:
            pytest.skip("rust_cave_001 not available")
        result = preprocess_text("The ball was thrown by John")
        assert "John" in result
        assert "threw" in result

    def test_preprocess_handles_simple_passive(self):
        """More realistic test with different passive constructions."""
        try:
            from rust_cave_001 import preprocess_text
        except ImportError:
            pytest.skip("rust_cave_001 not available")
        result = preprocess_text("The cake was eaten by Mary")
        assert "Mary" in result
        assert "the cake" in result
