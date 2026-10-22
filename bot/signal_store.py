"""Persist processed signals to SQLite so UI survives Streamlit reruns."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH
from utils.logger import get_logger

logger = get_logger("signal_store")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


async def _ensure(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            action TEXT,
            symbol TEXT,
            signal_side TEXT,
            our_side TEXT,
            price REAL,
            sl REAL,
            tp REAL,
            score REAL,
            success INTEGER,
            message TEXT,
            order_id TEXT,
            raw TEXT
        )
        """
    )
    await db.commit()


def _ensure_sync(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            action TEXT,
            symbol TEXT,
            signal_side TEXT,
            our_side TEXT,
            price REAL,
            sl REAL,
            tp REAL,
            score REAL,
            success INTEGER,
            message TEXT,
            order_id TEXT,
            raw TEXT
        )
        """
    )
    conn.commit()


def _row_tuple(entry: dict[str, Any]) -> tuple:
    return (
        entry.get("time") or datetime.utcnow().isoformat(timespec="seconds"),
        entry.get("action"),
        entry.get("symbol"),
        entry.get("signal_side"),
        entry.get("our_side"),
        entry.get("price"),
        entry.get("sl"),
        entry.get("tp"),
        entry.get("score"),
        1 if entry.get("success") else 0,
        entry.get("message"),
        entry.get("order_id"),
        json.dumps(entry, default=str),
    )


async def save_signal(entry: dict[str, Any]) -> None:
    """Async save; falls back to sync on failure."""
    try:
        _ensure_parent(DB_PATH)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await _ensure(db)
            await db.execute(
                """
                INSERT INTO processed_signals
                (time, action, symbol, signal_side, our_side, price, sl, tp, score, success, message, order_id, raw)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _row_tuple(entry),
            )
            await db.commit()
        logger.info("Signal saved: %s %s", entry.get("action"), entry.get("symbol"))
    except Exception as e:
        logger.warning("Async save_signal failed (%s) – trying sync", e)
        try:
            save_signal_sync(entry)
        except Exception as e2:
            logger.exception("Sync save_signal also failed: %s", e2)


def save_signal_sync(entry: dict[str, Any]) -> None:
    """Thread-safe sync write – safe from any thread without an event loop."""
    _ensure_parent(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        _ensure_sync(conn)
        conn.execute(
            """
            INSERT INTO processed_signals
            (time, action, symbol, signal_side, our_side, price, sl, tp, score, success, message, order_id, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _row_tuple(entry),
        )
        conn.commit()
        logger.info("Signal saved (sync): %s %s", entry.get("action"), entry.get("symbol"))
    finally:
        conn.close()


async def load_signals(limit: int = 50) -> list[dict]:
    try:
        _ensure_parent(DB_PATH)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await _ensure(db)
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM processed_signals ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.warning("Async load_signals failed (%s) – trying sync", e)
        return load_signals_sync(limit)


def load_signals_sync(limit: int = 50) -> list[dict]:
    try:
        _ensure_parent(DB_PATH)
        if not DB_PATH.exists():
            return []
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_sync(conn)
            cur = conn.execute(
                "SELECT * FROM processed_signals ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("load_signals_sync failed: %s", e)
        return []


def _row_to_dict(r) -> dict:
    return {
        "time": r["time"],
        "action": r["action"],
        "symbol": r["symbol"],
        "signal_side": r["signal_side"],
        "our_side": r["our_side"],
        "price": r["price"],
        "sl": r["sl"],
        "tp": r["tp"],
        "score": r["score"],
        "success": bool(r["success"]),
        "message": r["message"],
        "order_id": r["order_id"],
    }
