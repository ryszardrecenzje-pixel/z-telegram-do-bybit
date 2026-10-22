"""Bot package.

Submodules are imported explicitly where needed, e.g.:
  from bot.signal_store import save_signal_sync
  from bot import auth_bridge
"""

# Intentionally minimal – do not import submodules here (avoids circular
# imports and broken `from bot import signal_store` during partial deploys).
