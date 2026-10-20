"""
Central configuration loader.
Priority: Streamlit secrets (nested or flat) > environment variables > .env file > defaults.
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
    """
    Try Streamlit secrets in this order:
      1. nested:  st.secrets[section][key]
      2. flat:    st.secrets[f"{SECTION}_{KEY}"]   e.g. TELEGRAM_API_ID
    Then environment variables / .env:
      SECTION_KEY  e.g. TELEGRAM_API_ID
    """
    flat_key = f"{section.upper()}_{key.upper()}"

    try:
        import streamlit as st

        if hasattr(st, "secrets"):
            # 1) Nested section
            if section in st.secrets:
                val = st.secrets[section].get(key)
                if val is not None and val != "":
                    return val
            # 2) Flat key (how some people paste into Streamlit Cloud)
            if flat_key in st.secrets:
                val = st.secrets[flat_key]
                if val is not None and val != "":
                    return val
    except Exception:
        pass

    return os.getenv(flat_key, default)


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

    # Risk / strategy
    MAX_DAILY_LOSS_PCT: float = float(
        _get_secret("risk", "max_daily_loss_pct", 3.0) or 3.0
    )
    # % of available capital used per trade
    POSITION_SIZE_PCT: float = float(
        _get_secret("risk", "position_size_pct", 10.0) or 10.0
    )
    # Fixed SL / TP as % of entry price
    SL_PCT: float = float(_get_secret("risk", "sl_pct", 1.0) or 1.0)
    TP_PCT: float = float(_get_secret("risk", "tp_pct", 3.0) or 3.0)
    # If True: signal LONG → we open SHORT (and vice versa)
    INVERT_SIGNAL: bool = str(
        _get_secret("risk", "invert_signal", "true")
    ).lower() in ("1", "true", "yes")

    # App
    PAPER_MODE: bool = True
    SESSION_FILE: str = str(SESSION_DIR / "telethon.session")


config = Config()
