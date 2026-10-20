"""
Simple paper / demo broker.
Stores positions and trade history in SQLite.
Uses the price from the signal (or last known) as fill price.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite

from bot.brokers.base import BaseBroker, OrderResult, Position, Side
from config import DB_PATH
from utils.logger import get_logger

logger = get_logger("paper_broker")


class PaperBroker(BaseBroker):
    name = "paper"

    def __init__(self, starting_balance: float = 10_000.0, currency: str = "USD"):
        self.starting_balance = starting_balance
        self.currency = currency
        self._db: Optional[aiosqlite.Connection] = None
        self._cash: float = starting_balance

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                sl REAL,
                tp REAL,
                opened_at TEXT NOT NULL,
                signal_score REAL,
                meta TEXT,
                status TEXT DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,  -- OPEN / CLOSE
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                pnl REAL DEFAULT 0,
                message TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_balance (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Init balance row
        cur = await self._db.execute("SELECT cash FROM paper_balance WHERE id = 1")
        row = await cur.fetchone()
        if row is None:
            await self._db.execute(
                "INSERT INTO paper_balance (id, cash, updated_at) VALUES (1, ?, ?)",
                (self.starting_balance, datetime.utcnow().isoformat()),
            )
            self._cash = self.starting_balance
        else:
            self._cash = float(row["cash"])
        await self._db.commit()
        logger.info(f"PaperBroker connected. Cash: {self._cash:.2f} {self.currency}")

    async def disconnect(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def get_balance(self) -> dict:
        positions = await self.get_open_positions()
        # Unrealized not calculated here (no live prices); equity ≈ cash for simplicity
        return {
            "cash": self._cash,
            "equity": self._cash,
            "currency": self.currency,
            "open_positions": len(positions),
        }

    async def get_open_positions(self) -> list[Position]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM paper_positions WHERE status = 'open' ORDER BY opened_at DESC"
        )
        rows = await cur.fetchall()
        result = []
        for r in rows:
            result.append(
                Position(
                    symbol=r["symbol"],
                    side=r["side"],
                    quantity=r["quantity"],
                    entry_price=r["entry_price"],
                    sl=r["sl"],
                    tp=r["tp"],
                    opened_at=datetime.fromisoformat(r["opened_at"]),
                    signal_score=r["signal_score"],
                    meta=r["meta"],
                )
            )
        return result

    async def open_position(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        **kwargs,
    ) -> OrderResult:
        assert self._db
        if price is None or price <= 0:
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                message="Missing or invalid price for paper fill",
            )

        cost = quantity * price
        if cost > self._cash:
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                message=f"Insufficient cash: need {cost:.2f}, have {self._cash:.2f}",
            )

        order_id = str(uuid.uuid4())[:12]
        now = datetime.utcnow().isoformat()

        await self._db.execute(
            """
            INSERT INTO paper_positions
            (symbol, side, quantity, entry_price, sl, tp, opened_at, signal_score, meta, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                symbol.upper(),
                side,
                quantity,
                price,
                sl,
                tp,
                now,
                kwargs.get("signal_score"),
                kwargs.get("meta"),
            ),
        )

        self._cash -= cost
        await self._db.execute(
            "UPDATE paper_balance SET cash = ?, updated_at = ? WHERE id = 1",
            (self._cash, now),
        )

        await self._db.execute(
            """
            INSERT INTO paper_trades
            (order_id, symbol, side, action, quantity, price, pnl, message, timestamp)
            VALUES (?, ?, ?, 'OPEN', ?, ?, 0, ?, ?)
            """,
            (order_id, symbol.upper(), side, quantity, price, "Paper open", now),
        )
        await self._db.commit()

        logger.info(f"PAPER OPEN {side} {quantity} {symbol} @ {price}")
        return OrderResult(
            success=True,
            order_id=order_id,
            symbol=symbol.upper(),
            side=side,
            quantity=quantity,
            price=price,
            message="Paper position opened",
        )

    async def close_position(
        self,
        symbol: str,
        side: Optional[Side] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs,
    ) -> OrderResult:
        assert self._db
        symbol = symbol.upper()

        query = "SELECT * FROM paper_positions WHERE symbol = ? AND status = 'open'"
        params: list = [symbol]
        if side:
            query += " AND side = ?"
            params.append(side)

        cur = await self._db.execute(query, params)
        rows = await cur.fetchall()
        if not rows:
            return OrderResult(
                success=False,
                symbol=symbol,
                message=f"No open position found for {symbol}",
            )

        if price is None or price <= 0:
            # fallback to entry price (no real market data in pure paper mode)
            price = float(rows[0]["entry_price"])

        total_pnl = 0.0
        closed_qty = 0.0
        order_id = str(uuid.uuid4())[:12]
        now = datetime.utcnow().isoformat()

        for r in rows:
            qty = float(r["quantity"]) if quantity is None else min(quantity, float(r["quantity"]))
            entry = float(r["entry_price"])
            pos_side = r["side"]

            if pos_side == "LONG":
                pnl = (price - entry) * qty
            else:
                pnl = (entry - price) * qty

            total_pnl += pnl
            closed_qty += qty

            await self._db.execute(
                "UPDATE paper_positions SET status = 'closed' WHERE id = ?",
                (r["id"],),
            )

            # Return capital + pnl
            self._cash += entry * qty + pnl

        await self._db.execute(
            "UPDATE paper_balance SET cash = ?, updated_at = ? WHERE id = 1",
            (self._cash, now),
        )

        await self._db.execute(
            """
            INSERT INTO paper_trades
            (order_id, symbol, side, action, quantity, price, pnl, message, timestamp)
            VALUES (?, ?, ?, 'CLOSE', ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                symbol,
                side or rows[0]["side"],
                closed_qty,
                price,
                total_pnl,
                kwargs.get("reason", "Paper close"),
                now,
            ),
        )
        await self._db.commit()

        logger.info(f"PAPER CLOSE {symbol} qty={closed_qty} @ {price} PnL={total_pnl:.2f}")
        return OrderResult(
            success=True,
            order_id=order_id,
            symbol=symbol,
            side=side or "CLOSE",
            quantity=closed_qty,
            price=price,
            message=f"Closed. PnL: {total_pnl:.2f}",
        )

    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM paper_trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]