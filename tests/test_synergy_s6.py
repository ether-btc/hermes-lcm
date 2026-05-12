"""Tests for Synergy 6: .cz (LZ4 compressed) read support in externalize.py."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from externalize import (
    _load_raw_payload,
    load_externalized_payload,
    find_externalized_payload_for_message,
    reassign_externalized_payloads,
    DEFAULT_LARGE_OUTPUT_DIRNAME,
)


@pytest.fixture
def storage_dir(tmp_path):
    """Create a temp directory that acts as LCM external storage."""
    d = tmp_path / "lcm-external"
    d.mkdir()
    return d


@pytest.fixture
def mock_config(storage_dir):
    """Config that points to our temp storage dir."""
    cfg = MagicMock()
    cfg.large_output_externalization_path = str(storage_dir)
    return cfg


def _write_json_payload(storage_dir, filename, payload_dict):
    """Helper: write an uncompressed .json payload."""
    path = storage_dir / filename
    path.write_text(json.dumps(payload_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestLoadRawPayload:
    """Tests for the _load_raw_payload helper."""

    def test_reads_json_file(self, storage_dir):
        path = storage_dir / "test.json"
        path.write_text("hello world", encoding="utf-8")
        result = _load_raw_payload(path)
        assert result == "hello world"

    def test_returns_none_for_missing_file(self, storage_dir):
        result = _load_raw_payload(storage_dir / "nonexistent.json")
        assert result is None

    def test_returns_none_for_non_file_path(self, storage_dir):
        subdir = storage_dir / "subdir"
        subdir.mkdir()
        result = _load_raw_payload(subdir)
        assert result is None

    def test_cz_file_returns_none_for_invalid_lz4_data(self, storage_dir, caplog):
        """When .cz file contains invalid LZ4 data, _load_raw_payload returns None."""
        # This tests the ValueError error handler in _load_raw_payload
        path = storage_dir / "test_invalid.cz"
        path.write_bytes(b"this is not valid lz4 data")
        result = _load_raw_payload(path)
        # With invalid data, Rust will fail to decompress and we should get None
        # or the OSError/ValueError handler will catch it
        # Note: if rust_cave_001 is available but data is invalid, the try/except
        # in _load_raw_payload catches ValueError and returns None
        assert result is None

    def test_cz_file_with_valid_lz4_round_trip(self, storage_dir):
        """If rust_cave_001 is available, .cz files decompress correctly."""
        try:
            from rust_cave_001 import my_compress, decompress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        original = '{"key": "value", "number": 42}'
        compressed = my_compress(original.encode("utf-8"))
        path = storage_dir / "test.cz"
        path.write_bytes(compressed)
        result = _load_raw_payload(path)
        assert result == original


class TestLoadExternalizedPayload:
    """Tests for load_externalized_payload with .cz support."""

    def test_loads_json_payload(self, storage_dir, mock_config):
        payload = {
            "kind": "tool_result",
            "tool_call_id": "call_abc",
            "session_id": "session_1",
            "content": "test data",
        }
        _write_json_payload(storage_dir, "20260512_120000_call_abc_123.json", payload)
        result = load_externalized_payload(
            "20260512_120000_call_abc_123.json",
            config=mock_config,
            hermes_home=str(storage_dir),
        )
        assert result is not None
        assert result["content"] == "test data"

    def test_loads_compressed_payload(self, storage_dir, mock_config):
        """A .cz payload should be decompressed and read as JSON."""
        try:
            from rust_cave_001 import my_compress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        payload = {
            "kind": "tool_result",
            "tool_call_id": "call_xyz",
            "session_id": "session_2",
            "content": "compressed test data",
            "content_chars": 20,
            "content_bytes": 20,
        }
        json_text = json.dumps(payload, ensure_ascii=False, indent=2)
        compressed = my_compress(json_text.encode("utf-8"))
        path = storage_dir / "20260512_130000_call_xyz_456.cz"
        path.write_bytes(compressed)
        result = load_externalized_payload(
            "20260512_130000_call_xyz_456.cz",
            config=mock_config,
            hermes_home=str(storage_dir),
        )
        assert result is not None
        assert result["content"] == "compressed test data"


class TestFindExternalizedPayload:
    """Tests that find_externalized_payload_for_message finds .cz files too."""

    def test_finds_compressed_payload(self, storage_dir, mock_config):
        try:
            from rust_cave_001 import my_compress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        content = "this is unique searchable content xyz789"
        payload = {
            "kind": "tool_result",
            "tool_call_id": "call_find",
            "session_id": "session_3",
            "content": content,
        }
        json_text = json.dumps(payload, ensure_ascii=False, indent=2)
        compressed = my_compress(json_text.encode("utf-8"))

        # Use the actual digest prefix that find_externalized_payload_for_message computes
        from externalize import _content_digest_prefix
        digest = _content_digest_prefix(content)
        path = storage_dir / f"20260512_140000_call_find_{digest}_unique_suffix.cz"
        path.write_bytes(compressed)

        result = find_externalized_payload_for_message(
            content,
            tool_call_id="call_find",
            session_id="session_3",
            kind="tool_result",
            config=mock_config,
            hermes_home=str(storage_dir),
        )
        assert result is not None
        assert result["tool_call_id"] == "call_find"
        assert result["session_id"] == "session_3"
        assert result["ref"].endswith(".cz")


class TestReassignExternalizedPayloads:
    """Tests that reassign handles .cz files."""

    def test_reassigns_json_payload(self, storage_dir, mock_config):
        payload = {
            "kind": "tool_result",
            "tool_call_id": "call_old",
            "session_id": "old_session",
            "content": "data",
        }
        _write_json_payload(storage_dir, "20260512_150000_call_old_123.json", payload)
        count = reassign_externalized_payloads(
            "old_session", "new_session",
            config=mock_config, hermes_home=str(storage_dir),
        )
        assert count == 1
        # Verify the file was actually updated
        with open(storage_dir / "20260512_150000_call_old_123.json") as f:
            updated = json.load(f)
        assert updated["session_id"] == "new_session"

    def test_reassigns_compressed_payload(self, storage_dir, mock_config):
        try:
            from rust_cave_001 import my_compress
        except ImportError:
            pytest.skip("rust_cave_001 not available")

        payload = {
            "kind": "tool_result",
            "tool_call_id": "call_cz",
            "session_id": "old_session_cz",
            "content": "compressed data",
        }
        json_text = json.dumps(payload, ensure_ascii=False, indent=2)
        compressed = my_compress(json_text.encode("utf-8"))
        path = storage_dir / "20260512_160000_call_cz_789.cz"
        path.write_bytes(compressed)

        count = reassign_externalized_payloads(
            "old_session_cz", "new_session_cz",
            config=mock_config, hermes_home=str(storage_dir),
        )
        assert count == 1
