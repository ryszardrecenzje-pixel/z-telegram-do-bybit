"""Abstract broker interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


Side = Literal["LONG", "SHORT"]
OrderSide = Literal["BUY", "SELL"]


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    signal_score: Optional[float] = None
    meta: Optional[str] = None


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    message: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)


class BaseBroker(ABC):
    """Common interface for all brokers (paper / live)."""

    name: str = "base"

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def get_balance(self) -> dict:
        """Return dict with at least 'equity' and 'currency'."""
        ...

    @abstractmethod
    async def get_open_positions(self) -> list[Position]:
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        side: Optional[Side] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs,
    ) -> OrderResult:
        ...

    @abstractmethod
    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        ...