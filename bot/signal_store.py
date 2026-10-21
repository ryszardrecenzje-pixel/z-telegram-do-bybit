"""Persist processed signals to SQLite so UI survives Streamlit reruns."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import aiosqlite

from config import DB_PATH


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


async def save_signal(entry: dict[str, Any]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure(db)
        await db.execute(
            """
            INSERT INTO processed_signals
            (time, action, symbol, signal_side, our_side, price, sl, tp, score, success, message, order_id, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
            ),
        )
        await db.commit()


async def load_signals(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        await _ensure(db)
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM processed_signals ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            out.append(
                {
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
            )
        return out
