"""Time range parsing utilities."""

from __future__ import annotations

import re
import time

_MULTIPLIERS_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def parse_time_range(time_range: str) -> int:
    """Parse a human-readable time range like '2h', '30m', '7d' into milliseconds."""
    m = re.fullmatch(r"(\d+)([smhd])", time_range.strip())
    if not m:
        raise ValueError(
            f"Invalid time range '{time_range}'. "
            "Use formats like '30m', '2h', '6h', '2d', '7d'."
        )
    return int(m.group(1)) * _MULTIPLIERS_MS[m.group(2)]


def resolve_timestamps(
    start: int | None,
    end: int | None,
    time_range: str | None,
    default_range: str = "6h",
    unit: str = "ms",
    profile_time_range: str | None = None,
) -> tuple[int, int]:
    """Return (start, end) from explicit timestamps or a time range string.

    Priority: explicit start/end > time_range > profile default > command default.
    ``unit`` selects the output unit: 'ms' (most APIs) or 'ns' (legacy service APIs).

    A lone ``start`` or ``end`` is honoured rather than silently dropped: given
    only ``start`` the window runs to now; given only ``end`` it runs back one
    range from that end.
    """
    now_ms = int(time.time() * 1000)
    factor = 1_000_000 if unit == "ns" else 1
    now_val = now_ms * factor

    if start is not None and end is not None:
        if start >= end:
            raise ValueError(
                f"Invalid time window: start ({start}) must be before end ({end})."
            )
        return start, end

    effective_default = profile_time_range if profile_time_range else default_range
    duration = parse_time_range(time_range) if time_range else parse_time_range(effective_default)
    duration_val = duration * factor

    if start is not None:
        return start, now_val
    if end is not None:
        return end - duration_val, end
    return now_val - duration_val, now_val
