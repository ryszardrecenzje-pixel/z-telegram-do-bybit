"""
Takes a parsed Signal and routes it through risk manager → broker.
"""

from __future__ import annotations

from bot.brokers.base import BaseBroker
from bot.risk_manager import RiskManager
from bot.signal_parser import Signal
from utils.logger import get_logger

logger = get_logger("executor")


class Executor:
    def __init__(self, broker: BaseBroker, risk_manager: RiskManager | None = None):
        self.broker = broker
        self.risk = risk_manager or RiskManager()

    async def handle_signal(self, signal: Signal) -> dict:
        """
        Process one signal. Returns a result dict for logging / UI.
        """
        result = {
            "action": signal.action,
            "symbol": signal.symbol,
            "side": signal.side,
            "success": False,
            "message": "",
            "order_id": None,
        }

        try:
            if signal.action == "BUY":
                return await self._handle_buy(signal, result)
            elif signal.action == "CLOSE":
                return await self._handle_close(signal, result)
            else:
                result["message"] = f"Unknown action: {signal.action}"
                return result
        except Exception as e:
            logger.exception("Executor error")
            result["message"] = str(e)
            return result

    async def _handle_buy(self, signal: Signal, result: dict) -> dict:
        if signal.price is None or signal.price <= 0:
            result["message"] = "Signal has no valid price"
            return result

        decision = await self.risk.check_open(
            self.broker,
            symbol=signal.symbol,
            side=signal.side,
            price=signal.price,
            sl=signal.sl,
        )
        if not decision.allowed:
            result["message"] = f"Risk blocked: {decision.reason}"
            logger.warning(result["message"])
            return result

        order = await self.broker.open_position(
            symbol=signal.symbol,
            side=signal.side,  # type: ignore
            quantity=decision.quantity,
            price=signal.price,
            sl=signal.sl,
            tp=signal.tp,
            signal_score=signal.score,
            meta=signal.meta,
        )

        result["success"] = order.success
        result["message"] = order.message
        result["order_id"] = order.order_id
        result["quantity"] = order.quantity
        result["price"] = order.price
        return result

    async def _handle_close(self, signal: Signal, result: dict) -> dict:
        decision = await self.risk.check_close(self.broker, signal.symbol)
        if not decision.allowed:
            result["message"] = f"Risk blocked close: {decision.reason}"
            return result

        order = await self.broker.close_position(
            symbol=signal.symbol,
            side=signal.side,  # type: ignore
            price=signal.price,
            reason=signal.reason,
        )

        result["success"] = order.success
        result["message"] = order.message
        result["order_id"] = order.order_id
        result["quantity"] = order.quantity
        result["price"] = order.price

        # Try to extract PnL from message for daily tracking
        if order.success and "PnL:" in (order.message or ""):
            try:
                pnl_str = order.message.split("PnL:")[-1].strip()
                pnl = float(pnl_str)
                self.risk.record_pnl(pnl)
            except Exception:
                pass

        return result