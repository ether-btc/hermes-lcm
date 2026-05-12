"""Quality audit for LCM compression output — post-hoc validation.

Synergy 4: After LCM compresses messages into a DAG summary, validate
entity/number preservation using caveman's eval_compression().

This is READ-ONLY and never alters LCM behavior. Designed as an
observability/audit hook, not a blocking gate.
"""

from typing import Any, Dict, List, Optional


def audit_quality(
    original_text: str,
    compressed_text: str,
) -> Optional[Dict[str, Any]]:
    """Run eval_compression on LCM output. Returns None if audit unavailable.

    Args:
        original_text: Raw or pre-compression text (e.g., user messages
            that were summarized).
        compressed_text: LLM-produced summary text that replaced the
            original content in the context window.

    Returns:
        Dict with keys:
        - ratio: float — compression ratio (0.0-1.0)
        - entities_preserved: bool
        - numbers_preserved: bool
        - missing_entities: list of entity strings not found in compressed
        - missing_numbers: list of number strings not found in compressed
        - original_tokens: int (if caveman has rust backend)
        - compressed_tokens: int (if caveman has rust backend)
        Or None if caveman is not installed or an error occurs.
    """
    try:
        from caveman import eval_compression
        return eval_compression(original_text, compressed_text)
    except (ImportError, Exception):
        return None


def log_audit_result(audit: Optional[Dict], logger: Any) -> None:
    """Log structured quality metrics if available.

    Args:
        audit: Result from audit_quality() or None.
        logger: A standard python logger instance used by LCM.
    """
    if audit is None:
        return
    ratio = audit.get("ratio", 0)
    entities_ok = audit.get("entities_preserved", True)
    numbers_ok = audit.get("numbers_preserved", True)
    missing = audit.get("missing_entities", [])
    orig_tokens = audit.get("original_tokens", 0)
    comp_tokens = audit.get("compressed_tokens", 0)
    logger.info(
        "LCM quality audit: ratio=%.2f entities=%s numbers=%s missing=%s tokens=%d→%d",
        ratio, entities_ok, numbers_ok, missing, orig_tokens, comp_tokens,
    )
