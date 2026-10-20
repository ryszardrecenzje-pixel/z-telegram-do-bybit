"""
Central configuration loader.
Priority: Streamlit secrets > environment variables > .env file > defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env if present (local development)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "trading.db"
SESSION_DIR = DATA_DIR
SESSION_DIR.mkdir(exist_ok=True)


def _get_secret(section: str, key: str, default: Any = None) -> Any:
    """Try Streamlit secrets first, then env vars."""
    try:
        import streamlit as st

        if hasattr(st, "secrets") and section in st.secrets:
            val = st.secrets[section].get(key)
            if val is not None and val != "":
                return val
    except Exception:
        pass

    env_key = f"{section.upper()}_{key.upper()}"
    return os.getenv(env_key, default)


class Config:
    # Telegram
    TELEGRAM_API_ID: int = int(_get_secret("telegram", "api_id", 0) or 0)
    TELEGRAM_API_HASH: str = str(_get_secret("telegram", "api_hash", "") or "")
    TELEGRAM_PHONE: str = str(_get_secret("telegram", "phone", "") or "")
    SIGNAL_BOT_USERNAME: str = str(
        _get_secret("telegram", "signal_bot_username", "TraderRicziV2") or "TraderRicziV2"
    )

    # Binance
    BINANCE_API_KEY: str = str(_get_secret("binance", "api_key", "") or "")
    BINANCE_API_SECRET: str = str(_get_secret("binance", "api_secret", "") or "")
    BINANCE_TESTNET: bool = str(_get_secret("binance", "testnet", "true")).lower() in (
        "1",
        "true",
        "yes",
    )

    # Risk
    MAX_RISK_PER_TRADE_PCT: float = float(
        _get_secret("risk", "max_risk_per_trade_pct", 1.0) or 1.0
    )
    MAX_DAILY_LOSS_PCT: float = float(
        _get_secret("risk", "max_daily_loss_pct", 3.0) or 3.0
    )
    DEFAULT_POSITION_SIZE_USD: float = float(
        _get_secret("risk", "default_position_size_usd", 100.0) or 100.0
    )

    # App
    PAPER_MODE: bool = True  # default to paper / demo
    SESSION_FILE: str = str(SESSION_DIR / "telethon.session")


config = Config()