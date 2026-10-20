"""
Parser for signals coming from TraderRicziV2 bot.

Example BUY:
[PAPER] SIGNAL BUY
WMT LONG
price=119.269997 score=76
SL=118.472333 TP=120.757514
LONG | S=76 | Meta=0.65 | range | MTF=bullish

Example CLOSE:
[PAPER] SIGNAL CLOSE
NOW LONG
price=149.639999 score=80
SL=-- TP=--
Odwrócenie long RSI+MACD
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from utils.logger import get_logger

logger = get_logger("signal_parser")

SignalAction = Literal["BUY", "CLOSE", "UNKNOWN"]


@dataclass
class Signal:
    action: SignalAction
    symbol: str
    side: str  # LONG / SHORT
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    score: Optional[float] = None
    meta: Optional[str] = None
    reason: Optional[str] = None
    raw: str = ""
    is_paper: bool = True


def _to_float(val: str) -> Optional[float]:
    if not val or val.strip() in ("--", "-", "None", "null"):
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def parse_signal(text: str) -> Optional[Signal]:
    """
    Parse a single Telegram message text into a Signal object.
    Returns None if the message is not a recognized signal.
    """
    if not text:
        return None

    text = text.strip()
    upper = text.upper()

    # Must contain SIGNAL keyword
    if "SIGNAL" not in upper:
        return None

    is_paper = "[PAPER]" in upper or "PAPER" in upper

    # Detect action
    action: SignalAction = "UNKNOWN"
    if re.search(r"SIGNAL\s+BUY", upper):
        action = "BUY"
    elif re.search(r"SIGNAL\s+CLOSE", upper):
        action = "CLOSE"
    else:
        return None

    # Symbol + side (second line usually: "WMT LONG" or "NOW LONG")
    symbol = ""
    side = "LONG"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for ln in lines[1:4]:  # look in first few lines
        m = re.match(r"^([A-Z0-9.\-]+)\s+(LONG|SHORT)\b", ln.upper())
        if m:
            symbol = m.group(1)
            side = m.group(2)
            break

    if not symbol:
        # fallback: try to find a ticker-like token after SIGNAL BUY/CLOSE
        m = re.search(r"SIGNAL\s+(?:BUY|CLOSE)\s*\n\s*([A-Z0-9.\-]+)", text, re.I)
        if m:
            symbol = m.group(1).upper()

    if not symbol:
        logger.warning(f"Could not extract symbol from signal:\n{text[:200]}")
        return None

    # price=... score=...
    price = None
    score = None
    m = re.search(r"price\s*=\s*([0-9.]+)", text, re.I)
    if m:
        price = _to_float(m.group(1))

    m = re.search(r"score\s*=\s*([0-9.]+)", text, re.I)
    if m:
        score = _to_float(m.group(1))

    # SL=... TP=...
    sl = None
    tp = None
    m = re.search(r"SL\s*=\s*([0-9.\-]+)", text, re.I)
    if m:
        sl = _to_float(m.group(1))
    m = re.search(r"TP\s*=\s*([0-9.\-]+)", text, re.I)
    if m:
        tp = _to_float(m.group(1))

    # Meta line or reason
    meta = None
    reason = None
    for ln in lines:
        if "Meta=" in ln or "MTF=" in ln:
            meta = ln.strip()
        if action == "CLOSE" and ("Odwrócenie" in ln or "RSI" in ln or "MACD" in ln):
            reason = ln.strip()

    signal = Signal(
        action=action,
        symbol=symbol.upper(),
        side=side,
        price=price,
        sl=sl,
        tp=tp,
        score=score,
        meta=meta,
        reason=reason,
        raw=text,
        is_paper=is_paper,
    )
    logger.info(f"Parsed signal: {action} {signal.symbol} {side} @ {price}")
    return signal