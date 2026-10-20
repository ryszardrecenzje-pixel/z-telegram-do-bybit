"""
Central configuration loader.
Priority: Streamlit secrets (nested or flat) > environment variables > .env file > defaults.

Values are read lazily (on first access) so Streamlit secrets are available.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "trading.db"
SESSION_DIR = DATA_DIR
SESSION_DIR.mkdir(exist_ok=True)


def _read_secret(section: str, key: str, default: Any = None) -> Any:
    """
    Read one secret. Tries in order:
      1. st.secrets[section][key]          (nested TOML)
      2. st.secrets[SECTION_KEY]           (flat, e.g. TELEGRAM_API_ID)
      3. st.secrets[key]                   (bare key)
      4. os.environ[SECTION_KEY]           (.env / system env)
    """
    flat_key = f"{section.upper()}_{key.upper()}"

    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is not None:
            # 1) Nested: [telegram] api_id = ...
            try:
                section_obj = None
                if hasattr(secrets, "get"):
                    section_obj = secrets.get(section)
                elif section in secrets:
                    section_obj = secrets[section]
                if section_obj is not None:
                    if hasattr(section_obj, "get"):
                        val = section_obj.get(key)
                    elif key in section_obj:
                        val = section_obj[key]
                    else:
                        val = None
                    if val is not None and str(val).strip() != "":
                        return val
            except Exception:
                pass

            # 2) Flat: TELEGRAM_API_ID = ...
            try:
                if flat_key in secrets:
                    val = secrets[flat_key]
                    if val is not None and str(val).strip() != "":
                        return val
            except Exception:
                pass

            # 3) Bare key
            try:
                if key in secrets:
                    val = secrets[key]
                    if val is not None and str(val).strip() != "":
                        return val
            except Exception:
                pass
    except Exception:
        pass

    env_val = os.getenv(flat_key)
    if env_val is not None and str(env_val).strip() != "":
        return env_val

    return default


class _Config:
    """Lazy config – values are resolved when first accessed."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _get(self, name: str, section: str, key: str, default: Any, cast=None) -> Any:
        if name in self._cache:
            return self._cache[name]
        raw = _read_secret(section, key, default)
        if cast is not None and raw is not None:
            try:
                raw = cast(raw)
            except Exception:
                raw = default
        self._cache[name] = raw
        return raw

    def reload(self) -> None:
        """Clear cache so next access re-reads secrets."""
        self._cache.clear()

    @property
    def TELEGRAM_API_ID(self) -> int:
        return int(self._get("TELEGRAM_API_ID", "telegram", "api_id", 0, int) or 0)

    @property
    def TELEGRAM_API_HASH(self) -> str:
        return str(self._get("TELEGRAM_API_HASH", "telegram", "api_hash", "") or "")

    @property
    def TELEGRAM_PHONE(self) -> str:
        return str(self._get("TELEGRAM_PHONE", "telegram", "phone", "") or "")

    @property
    def TELEGRAM_SESSION_STRING(self) -> str:
        """Telethon StringSession – required on Streamlit Cloud."""
        return str(self._get("TELEGRAM_SESSION_STRING", "telegram", "session_string", "") or "")

    @property
    def SIGNAL_BOT_USERNAME(self) -> str:
        return str(
            self._get("SIGNAL_BOT_USERNAME", "telegram", "signal_bot_username", "TraderRicziV2")
            or "TraderRicziV2"
        )

    @property
    def BINANCE_API_KEY(self) -> str:
        return str(self._get("BINANCE_API_KEY", "binance", "api_key", "") or "")

    @property
    def BINANCE_API_SECRET(self) -> str:
        return str(self._get("BINANCE_API_SECRET", "binance", "api_secret", "") or "")

    @property
    def BINANCE_TESTNET(self) -> bool:
        v = self._get("BINANCE_TESTNET", "binance", "testnet", "true")
        return str(v).lower() in ("1", "true", "yes")

    @property
    def POSITION_SIZE_PCT(self) -> float:
        return float(self._get("POSITION_SIZE_PCT", "risk", "position_size_pct", 10.0) or 10.0)

    @property
    def SL_PCT(self) -> float:
        return float(self._get("SL_PCT", "risk", "sl_pct", 1.0) or 1.0)

    @property
    def TP_PCT(self) -> float:
        return float(self._get("TP_PCT", "risk", "tp_pct", 3.0) or 3.0)

    @property
    def MAX_DAILY_LOSS_PCT(self) -> float:
        return float(self._get("MAX_DAILY_LOSS_PCT", "risk", "max_daily_loss_pct", 3.0) or 3.0)

    @property
    def INVERT_SIGNAL(self) -> bool:
        v = self._get("INVERT_SIGNAL", "risk", "invert_signal", "true")
        return str(v).lower() in ("1", "true", "yes")

    @property
    def PAPER_MODE(self) -> bool:
        return True

    @property
    def SESSION_FILE(self) -> str:
        return str(SESSION_DIR / "telethon.session")

    def status_summary(self) -> dict:
        """For UI diagnostics – does not expose secret values."""
        return {
            "telegram_api_id_set": bool(self.TELEGRAM_API_ID),
            "telegram_api_hash_set": bool(self.TELEGRAM_API_HASH),
            "telegram_phone_set": bool(self.TELEGRAM_PHONE),
            "telegram_session_string_set": bool(self.TELEGRAM_SESSION_STRING),
            "signal_bot": self.SIGNAL_BOT_USERNAME,
            "binance_key_set": bool(self.BINANCE_API_KEY),
            "binance_secret_set": bool(self.BINANCE_API_SECRET),
            "binance_testnet": self.BINANCE_TESTNET,
            "position_size_pct": self.POSITION_SIZE_PCT,
            "sl_pct": self.SL_PCT,
            "tp_pct": self.TP_PCT,
            "invert_signal": self.INVERT_SIGNAL,
        }


config = _Config()
