"""
Map signal symbols (e.g. AAPL) to exchange symbols (e.g. AAPLB on Binance).

Override via secrets:

  [symbols]
  AAPL = "AAPLB"
  AMZN = "AMZNB"
  # or full pair:
  # AAPL = "AAPLB/USDT"
"""

from __future__ import annotations

from typing import Optional

from config import config
from utils.logger import get_logger

logger = get_logger("symbol_map")

# Defaults for Binance tokenized / stock-style tickers (user can override in secrets)
DEFAULT_BINANCE_MAP: dict[str, str] = {
    "AAPL": "AAPLB",
    "AMZN": "AMZNB",
    "TSLA": "TSLAB",
    "MSFT": "MSFTB",
    "GOOGL": "GOOGLB",
    "GOOG": "GOOGLB",
    "META": "METAB",
    "NVDA": "NVDAB",
    "NFLX": "NFLXB",
    "AMD": "AMDB",
    "BABA": "BABAB",
    "COIN": "COINB",
    "MSTR": "MSTRB",
    "WMT": "WMTB",
    "BA": "BAB",  # careful – may need override
    "NOW": "NOWB",
}


def _secrets_map() -> dict[str, str]:
    """Read optional [symbols] section or SYMBOL_MAP_* flat keys from secrets."""
    out: dict[str, str] = {}
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return out
        # Nested [symbols]
        try:
            section = secrets.get("symbols") if hasattr(secrets, "get") else secrets["symbols"]
            if section is not None:
                for k in section:
                    out[str(k).upper()] = str(section[k]).strip()
        except Exception:
            pass
        # Flat SYMBOL_AAPL = "AAPLB"
        try:
            for key in list(secrets.keys()) if hasattr(secrets, "keys") else []:
                sk = str(key)
                if sk.upper().startswith("SYMBOL_") and len(sk) > 7:
                    out[sk[7:].upper()] = str(secrets[key]).strip()
        except Exception:
            pass
    except Exception:
        pass
    return out


def map_symbol(symbol: str, broker: str = "binance") -> str:
    """
    Map a signal ticker to exchange ticker (without /USDT yet).
    Returns e.g. AAPLB for AAPL on binance.
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        return raw

    # Already looks like a pair
    if "/" in raw:
        return raw

    if broker.lower() in ("binance", "binance_live", "binance_testnet"):
        overrides = _secrets_map()
        if raw in overrides:
            mapped = overrides[raw].upper()
            logger.info("Symbol map (secrets): %s → %s", raw, mapped)
            return mapped
        if raw in DEFAULT_BINANCE_MAP:
            mapped = DEFAULT_BINANCE_MAP[raw]
            logger.info("Symbol map (default): %s → %s", raw, mapped)
            return mapped
        # Heuristic: many Binance stock tokens append B
        if not raw.endswith("B") and len(raw) <= 5:
            candidate = raw + "B"
            logger.info("Symbol map (heuristic +B): %s → %s", raw, candidate)
            return candidate

    return raw


def to_binance_pair(symbol: str, quote: str = "USDT") -> str:
    """
    Produce ccxt market symbol, e.g. AAPL → AAPLB/USDT
    If already AAPLB/USDT, return as-is.
    """
    mapped = map_symbol(symbol, broker="binance")
    if "/" in mapped:
        return mapped
    return f"{mapped}/{quote}"
