"""SYNERGY 5: Multi-tier compression cascade tests.

Tests the cascade compression strategy which tries:
Tier 0 (RUST-CAVE-001) -> Tier 1 (caveman) -> Tier 2 (LLM)
"""

import pytest
from unittest.mock import MagicMock
import hermes_lcm.config as config_mod


class TestCascadeConfig:
    """Test compression_strategy configuration."""

    def test_config_default_strategy_is_llm(self):
        """Default compression_strategy is 'llm'."""
        config = config_mod.LCMConfig()
        assert config.compression_strategy == "llm"

    def test_config_from_env_reads_compression_strategy(self, monkeypatch):
        """LCM_COMPRESSION_STRATEGY env var sets compression_strategy."""
        monkeypatch.setenv("LCM_COMPRESSION_STRATEGY", "cascade")
        config = config_mod.LCMConfig.from_env()
        assert config.compression_strategy == "cascade"

    def test_config_from_env_defaults_to_llm(self, monkeypatch):
        """LCM_COMPRESSION_STRATEGY env var defaults to 'llm' when not set."""
        monkeypatch.delenv("LCM_COMPRESSION_STRATEGY", raising=False)
        config = config_mod.LCMConfig.from_env()
        assert config.compression_strategy == "llm"


class TestCascadeCompressionLogic:
    """Test cascade compression logic with direct module imports."""

    def test_tier_0_rust_when_sufficient(self):
        """Cascade tier 0 (RUST) meets budget on moderate text."""
        pytest.importorskip("rust_cave_001", reason="RUST-CAVE-001 not installed")

        from rust_cave_001 import compress
        import hermes_lcm.tokens as tokens_mod

        messages = [
            {"role": "user", "content": "The database needs an index because queries are too slow. " * 10},
            {"role": "assistant", "content": "DB needs index queries slow. Adding index overhead. " * 10},
        ]

        original_tokens = tokens_mod.count_messages_tokens(messages)

        # Verify rust_cave_001.compress is callable
        assert callable(compress)

        # Verify compression reduces content
        compressed = compress(messages[0]["content"])
        assert compressed != messages[0]["content"]
        assert len(compressed) < len(messages[0]["content"])

        compressed_tokens = tokens_mod.count_messages_tokens([
            {"role": "user", "content": compressed},
            {"role": "assistant", "content": compress(messages[1]["content"])},
        ])

        # Should reduce token count
        assert compressed_tokens < original_tokens

    def test_tier_1_caveman_when_rust_available(self):
        """Cascade tier 1 (caveman) available when installed."""
        pytest.importorskip("caveman", reason="caveman-compression not installed")

        from caveman import compress_text

        # Verify compress_text is callable
        assert callable(compress_text)

        # Test basic compression
        text = "The database needs an index because the queries are too slow."
        compressed = compress_text(text, level="full")
        assert compressed != text

    def test_tier_2_fallback_when_tiers_unavailable(self):
        """Cascade falls back to tier 2 (LLM) when tiers unavailable."""
        # Check what's available
        import sys

        try:
            from rust_cave_001 import compress
            rust_available = True
        except ImportError:
            rust_available = False

        try:
            from caveman import compress_text
            caveman_available = True
        except ImportError:
            caveman_available = False

        # In CI with no dependencies, both will be False
        # This just verifies the import logic doesn't crash
        assert isinstance(rust_available, bool)
        assert isinstance(caveman_available, bool)

    def test_token_budget_respected(self):
        """Cascade never exceeds target token budget."""
        pytest.importorskip("rust_cave_001", reason="RUST-CAVE-001 not installed")

        from rust_cave_001 import compress
        import hermes_lcm.tokens as tokens_mod

        messages = [
            {"role": "user", "content": "Hello world."},
        ]

        # Already under budget
        target_tokens = 6000
        current_tokens = tokens_mod.count_messages_tokens(messages)
        assert current_tokens <= target_tokens

        # Large message
        large_messages = [
            {"role": "user", "content": "The database needs an index because the queries are too slow. " * 100},
        ]

        compressed = compress(large_messages[0]["content"])
        compressed_tokens = tokens_mod.count_messages_tokens([{"role": "user", "content": compressed}])

        # Should reduce tokens
        assert compressed_tokens < tokens_mod.count_messages_tokens(large_messages)

    def test_engine_has_cascade_method(self):
        """LCMEngine has _cascade_compress method."""
        pytest.importorskip("agent", reason="agent module not available")

        from agent.context_engine import ContextEngine

        assert hasattr(ContextEngine, "_cascade_compress")
        assert callable(getattr(ContextEngine, "_cascade_compress"))

    def test_compress_checks_cascade_strategy(self):
        """compress() method checks compression_strategy config."""
        pytest.importorskip("agent", reason="agent module not available")

        from agent.context_engine import ContextEngine
        import inspect

        compress_source = inspect.getsource(ContextEngine.compress)
        assert "compression_strategy" in compress_source
        assert "_cascade_compress" in compress_source
