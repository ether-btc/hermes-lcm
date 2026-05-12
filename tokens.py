"""Token counting utilities for LCM.

Uses tiktoken when available, falls back to char-based estimate.
Synergy 2: Optional fast word-boundary token estimate via rust_cave_001
for preflight compression gate checks.
"""

import logging
import re
from typing import Any, Dict, List

from .message_content import normalize_content_value

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_encoder = None
_encoder_checked = False

# ── Synergy 2: fast word-boundary token estimate ──────────────────────
_rust_word_count = None
try:
    from rust_cave_001 import estimate_tokens as _rust_estimate_tokens

    _rust_word_count = _rust_estimate_tokens
except ImportError:
    pass


def estimate_tokens_fast(text: str) -> int:
    """Fast token estimate via rust_cave_001 word count, or Python regex fallback.

    Uses a simple ``\\b\\w+\\b`` word-boundary pattern — not tiktoken-accurate,
    but sub-millisecond and suitable for preflight ``should_compress()`` gates.
    """
    if _rust_word_count is not None:
        return _rust_word_count(text)
    return len(re.findall(r"\b\w+\b", text))


# ── End Synergy 2 ─────────────────────────────────────────────────────

_encoder = None
_encoder_checked = False


def _get_encoder():
    """Lazily load tiktoken cl100k_base encoder."""
    global _encoder, _encoder_checked
    if _encoder_checked:
        return _encoder
    _encoder_checked = True
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        logger.debug("tiktoken not available, using char-based estimates")
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens in a string."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // _CHARS_PER_TOKEN + 1


def count_message_tokens(msg: Dict[str, Any]) -> int:
    """Estimate tokens for a single OpenAI-format message."""
    total = 4  # role + overhead
    content = normalize_content_value(msg.get("content")) or ""
    total += count_tokens(content)
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function", {})
            total += count_tokens(fn.get("name", ""))
            total += count_tokens(fn.get("arguments", ""))
        total += 3  # per-call overhead
    return total


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens for a message list."""
    return sum(count_message_tokens(m) for m in messages)
