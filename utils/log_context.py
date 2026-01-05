"""Logging context for tracking parish ID through async operations."""

from contextvars import ContextVar

# Context variable to hold current parish identifier
_parish_context: ContextVar[str] = ContextVar("parish_context", default="")


def set_parish_context(parish_id: str, parish_name: str) -> None:
    """Set the current parish context for logging."""
    _parish_context.set(f"[{parish_id}] {parish_name}")


def clear_parish_context() -> None:
    """Clear the current parish context."""
    _parish_context.set("")


def get_log_prefix() -> str:
    """Get the current log prefix (e.g., '[1234] St. Mary')."""
    return _parish_context.get()
