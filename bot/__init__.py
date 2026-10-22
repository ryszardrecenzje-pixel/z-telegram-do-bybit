"""Bot package – explicit exports so `from bot import signal_store` always works."""

from bot import signal_store as signal_store  # noqa: F401
from bot import auth_bridge as auth_bridge  # noqa: F401

__all__ = ["signal_store", "auth_bridge"]
