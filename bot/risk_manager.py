"""
Basic risk checks before executing a trade.
Position size = POSITION_SIZE_PCT % of available capital (default 10%).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.brokers.base import BaseBroker
from config import config
from utils.logger import get_logger

logger = get_logger("risk_manager")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float = 0.0


class RiskManager:
    def __init__(
        self,
        position_size_pct: float | None = None,
        max_daily_loss_pct: float | None = None,
        **kwargs,  # accept legacy args from UI without breaking
    ):
        self.position_size_pct = position_size_pct or config.POSITION_SIZE_PCT
        self.max_daily_loss_pct = max_daily_loss_pct or config.MAX_DAILY_LOSS_PCT
        self._daily_pnl: float = 0.0
        self._starting_equity: Optional[float] = None

    async def update_equity(self, broker: BaseBroker) -> None:
        bal = await broker.get_balance()
        equity = float(bal.get("equity", 0))
        if self._starting_equity is None:
            self._starting_equity = equity

    def record_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl

    async def check_open(
        self,
        broker: BaseBroker,
        symbol: str,
        side: str,
        price: float,
        sl: Optional[float] = None,
    ) -> RiskDecision:
        await self.update_equity(broker)
        bal = await broker.get_balance()
        equity = float(bal.get("equity", 0))
        cash = float(bal.get("cash", equity))

        if equity <= 0:
            return RiskDecision(False, "Equity is zero or negative")

        # Daily loss circuit breaker
        if self._starting_equity and self._starting_equity > 0:
            daily_loss_pct = (-self._daily_pnl / self._starting_equity) * 100
            if daily_loss_pct >= self.max_daily_loss_pct:
                return RiskDecision(
                    False,
                    f"Daily loss limit reached ({daily_loss_pct:.2f}% >= {self.max_daily_loss_pct}%)",
                )

        # Position size = X% of available capital
        notional = cash * (self.position_size_pct / 100.0)
        # leave a small buffer so we never try to use 100% of cash
        notional = min(notional, cash * 0.98)

        if notional <= 0 or price <= 0:
            return RiskDecision(False, "Invalid notional or price")

        quantity = notional / price

        if quantity * price > cash:
            return RiskDecision(False, f"Not enough cash for size {quantity:.4f}")

        logger.info(
            f"Size: {self.position_size_pct}% of capital → "
            f"{notional:.2f} notional → qty={quantity:.6f} {symbol}"
        )
        return RiskDecision(True, "OK", quantity=round(quantity, 6))

    async def check_close(self, broker: BaseBroker, symbol: str) -> RiskDecision:
        return RiskDecision(True, "OK")
