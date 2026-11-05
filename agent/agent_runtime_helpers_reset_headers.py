"""Rate-limit reset parsing from provider response headers, shared by ``extract_api_error_context``."""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from agent.credential_pool_timestamps import _parse_absolute_timestamp
from agent.retry_utils import parse_retry_after_seconds


def _set_reset_from_retry_after(context: dict[str, Any], retry_after: Any) -> None:
    if "reset_at" in context:
        return
    seconds = parse_retry_after_seconds(retry_after)
    if seconds is not None:
        context["reset_at"] = time.time() + seconds


# OpenAI-style relative windows: "6m0s", "1.5s", "20ms", "1h2m3s" (also a bare number of seconds).
_DURATION_COMPONENT_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
# Lowest-priority reset sources, after Retry-After and x-ratelimit-reset: OpenAI's per-bucket
# durations and Anthropic's per-bucket ISO-8601 timestamps. Plain OpenAI/Anthropic 429s often
# carry only these, and without them the retry status never names the reset window.
_VENDOR_RESET_HEADERS = (
    "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
    "anthropic-ratelimit-requests-reset", "anthropic-ratelimit-tokens-reset",
)
# Anthropic's unified headers describe the account-level 5h/7d windows and whether they are
# actually blocking the account. They rank below every other reset source, and only the
# unified-status carries their meaning: the resets of an "allowed" window are not binding.
# A spent account comes back as "rejected"; "blocked" is read the same way. The resets are
# epoch seconds, and unified-reset is the instant of the window that binds the account.
_UNIFIED_STATUS_HEADER = "anthropic-ratelimit-unified-status"
_UNIFIED_BLOCKING_STATUSES = frozenset({"rejected", "blocked"})
_UNIFIED_ACCOUNT_RESET_HEADER = "anthropic-ratelimit-unified-reset"
_UNIFIED_WINDOW_HEADERS = (
    ("anthropic-ratelimit-unified-5h-status", "anthropic-ratelimit-unified-5h-reset"),
    ("anthropic-ratelimit-unified-7d-status", "anthropic-ratelimit-unified-7d-reset"),
)


def _duration_string_seconds(text: str) -> Optional[float]:
    raw = text.strip().lower()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    parts = _DURATION_COMPONENT_RE.findall(raw)
    if not parts or "".join(n + u for n, u in parts) != raw:
        return None
    return sum(float(n) * _DURATION_UNIT_SECONDS[u] for n, u in parts)


def _header_reset_seconds(value: str, *, epoch: bool = False) -> Optional[float]:
    """Seconds until a vendor reset header's instant, or None when absent/past/unparseable.

    ``epoch`` reads a bare number as epoch seconds instead of a duration.
    """
    seconds = None if epoch else _duration_string_seconds(value)
    if seconds is None:
        absolute = _parse_absolute_timestamp(value)
        seconds = None if absolute is None else absolute - time.time()
    return seconds if seconds is not None and seconds > 0 else None


def _set_reset_from_vendor_headers(context: dict[str, Any], headers: Any) -> None:
    for name in _VENDOR_RESET_HEADERS:
        value = headers.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        seconds = _header_reset_seconds(value)
        if seconds is not None:
            context["reset_at"] = time.time() + seconds
            return


def _unified_status(headers: Any, name: str) -> str:
    value = headers.get(name)
    return value.strip().lower() if isinstance(value, str) else ""


def _unified_reset_seconds(headers: Any, name: str) -> Optional[float]:
    """Seconds until a unified reset header's instant; a bare number there is epoch seconds."""
    value = headers.get(name)
    if not isinstance(value, str) or not value.strip():
        return None
    return _header_reset_seconds(value, epoch=True)


def _set_reset_from_unified_headers(context: dict[str, Any], headers: Any) -> None:
    """Read Anthropic's account-level 5h/7d windows.

    Two separate jobs. The status flag is read whatever else the response said: a 429 body
    claiming the *request* would exceed the account's limit arrives even while the unified
    windows stand open — a 1-token probe of such an account succeeds within seconds
    (#131360), so the pool must hear "window open" even when a bucket header named a window.

    The resets are adopted only when the status says ``rejected`` (or ``blocked``) and nothing
    more specific (retry-after, a body window, a per-bucket reset) already named one.
    unified-reset is the account's own instant and wins. Without it, the windows whose own
    status is blocking bind; only when none says so does any window count. Among those, the
    earliest future instant wins: over-benching idles an account that has a window back,
    while a too-short bench just re-fails once and re-benches.
    """
    normalized_status = _unified_status(headers, _UNIFIED_STATUS_HEADER)
    if normalized_status == "allowed":
        context["account_window_open"] = True
    if normalized_status not in _UNIFIED_BLOCKING_STATUSES or "reset_at" in context:
        return
    seconds = _unified_reset_seconds(headers, _UNIFIED_ACCOUNT_RESET_HEADER)
    if seconds is None:
        windows = [
            (_unified_status(headers, status_name), window_reset)
            for status_name, reset_name in _UNIFIED_WINDOW_HEADERS
            if (window_reset := _unified_reset_seconds(headers, reset_name)) is not None
        ]
        binding = [reset for state, reset in windows if state in _UNIFIED_BLOCKING_STATUSES]
        resets = binding or [reset for _, reset in windows]
        seconds = min(resets) if resets else None
    if seconds is not None:
        context["reset_at"] = time.time() + seconds
