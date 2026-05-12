"""Tests for Synergy 3: Externalized payload compression (LZ4/gzip)."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from externalize import (
    _write_externalized_payload,
    _load_raw_payload,
    maybe_externalize_payload,
    externalize_ingest_payload,
    reassign_externalized_payloads,
    get_large_output_storage_dir,
)


@pytest.fixture
def storage_dir(tmp_path):
    d = tmp_path / "lcm-external"
    d.mkdir()
    return d


@pytest.fixture
def mock_config(storage_dir):
    cfg = MagicMock()
    cfg.large_output_externalization_enabled = True
    cfg.large_output_externalization_threshold_chars = 100
    cfg.large_output_externalization_path = str(storage_dir)
    cfg.external_compressor = "none"
    return cfg


SAMPLE_PAYLOAD = {
    "kind": "tool_result",
    "tool_call_id": "call_test",
    "session_id": "s1",
    "content": "test output data",
}


class TestWriteExternalizedPayload:
    """Tests for _write_externalized_payload with compression."""

    def test_no_compressor_writes_json(self, storage_dir):
        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="none")
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["content"] == "test output data"

    def test_gzip_compressor(self, storage_dir):
        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="gzip")
        assert path.exists()
        # Should be binary compressed
        raw = path.read_bytes()
        # gzip magic bytes: 1f 8b
        assert raw[:2] == b"\x1f\x8b"

    def test_lz4_compressor(self, storage_dir):
        try:
            from rust_cave_001 import decompress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="lz4")
        assert path.exists()
        raw = path.read_bytes()
        data = json.loads(decompress(raw).decode("utf-8"))
        assert data["content"] == "test output data"

    def test_lz4_falls_back_to_none_when_rust_unavailable(self, storage_dir):
        """When rust_cave_001 is not available, lz4 compressor writes uncompressed."""
        # We can't easily mock the import failure here, but we can verify that
        # the function doesn't crash and writes a valid JSON file
        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="lz4")
        assert path.exists()
        # If rust is available, this will be compressed. If not, it will be plain text.
        # Both are valid outcomes.
        raw = path.read_bytes()
        try:
            from rust_cave_001 import decompress
            decompress(raw)  # Try decompressing
        except ImportError:
            # No rust, should be plain JSON
            json.loads(raw.decode("utf-8"))


class TestRoundTrip:
    """End-to-end write/read tests."""

    def test_roundtrip_no_compression(self, storage_dir):
        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="none")
        result = _load_raw_payload(path)
        assert result is not None
        data = json.loads(result)
        assert data["content"] == "test output data"

    def test_roundtrip_gzip(self, storage_dir):
        path = storage_dir / "test.json"
        _write_externalized_payload(path, SAMPLE_PAYLOAD, compressor="gzip")
        result = _load_raw_payload(path)
        assert result is not None
        assert "test output data" in result

    def test_roundtrip_lz4(self, storage_dir):
        try:
            from rust_cave_001 import my_compress, decompress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        path = storage_dir / "test.cz"
        compressed = my_compress(json.dumps(SAMPLE_PAYLOAD).encode("utf-8"))
        path.write_bytes(compressed)
        result = _load_raw_payload(path)
        assert result is not None
        data = json.loads(result)
        assert data["content"] == "test output data"


class TestMaybeExternalizePayloadWithCompression:
    """Integration tests for maybe_externalize_payload with compressor."""

    def test_externalizes_with_none_compressor(self, storage_dir, mock_config):
        mock_config.external_compressor = "none"
        result = maybe_externalize_payload(
            "x" * 200,
            kind="tool_result",
            tool_call_id="call_r1",
            session_id="s1",
            role="tool",
            config=mock_config,
            hermes_home=str(storage_dir),
        )
        assert result is not None
        assert result["path"].suffix == ".json"

    def test_externalizes_with_lz4_compressor(self, storage_dir, mock_config):
        try:
            from rust_cave_001 import decompress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        mock_config.external_compressor = "lz4"
        result = maybe_externalize_payload(
            "y" * 200,
            kind="tool_result",
            tool_call_id="call_r2",
            session_id="s1",
            role="tool",
            config=mock_config,
            hermes_home=str(storage_dir),
        )
        assert result is not None
        # File should be LZ4 compressed
        raw = result["path"].read_bytes()
        data = json.loads(decompress(raw).decode("utf-8"))
        assert "y" * 200 in data["content"]


class TestConfigExternalCompressor:
    """Test that config.py external_compressor works."""

    def test_default_is_none(self):
        from config import LCMConfig
        cfg = LCMConfig()
        assert cfg.external_compressor == "none"

    def test_from_env_reads_lcm_external_compressor(self, monkeypatch):
        from config import LCMConfig
        monkeypatch.setenv("LCM_EXTERNAL_COMPRESSOR", "gzip")
        cfg = LCMConfig.from_env()
        assert cfg.external_compressor == "gzip"

    def test_from_env_falls_back_to_default(self, monkeypatch):
        from config import LCMConfig
        monkeypatch.delenv("LCM_EXTERNAL_COMPRESSOR", raising=False)
        cfg = LCMConfig.from_env()
        assert cfg.external_compressor == "none"
