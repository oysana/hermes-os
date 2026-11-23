"""Synthetic source for bounded native file lookup; not application code."""

DEFAULT_WINDOW = 11
LEGACY_DELAY_MS = 1900


def resolve_delay(override=None):
    fallback_ms = 2800
    return fallback_ms if override is None else override


def bounded_window(values, count=DEFAULT_WINDOW):
    return values[:max(count, 0)]
