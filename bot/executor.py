"""
Takes a parsed Signal and routes it through risk manager → broker.

Strategy rules (configurable in config):
- Invest POSITION_SIZE_PCT % of available capital per trade (default 10%).
- Invert signal side: signal LONG → open SHORT, signal SHORT → open LONG.
- Fixed SL = SL_PCT % and TP = TP_PCT % from entry price (default 1% / 3%).
"""

from __future__ import annotations

from typing import Optional, Tuple

from bot.brokers.base import BaseBroker
from bot.risk_manager import RiskManager
from bot.signal_parser import Signal
from config import config
from utils.logger import get_logger

logger = get_logger("executor")


def _invert_side(side: str) -> str:
    side = (side or "").upper()
    if side == "LONG":
        return "SHORT"
    if side == "SHORT":
        return "LONG"
    return side


def _calc_sl_tp(
    entry: float, trade_side: str, sl_pct: float, tp_pct: float
) -> Tuple[Optional[float], Optional[float]]:
    """
    Calculate absolute SL / TP prices from percentage distances.
    LONG:  SL below entry, TP above entry
    SHORT: SL above entry, TP below entry
    """
    if entry <= 0:
        return None, None

    sl_dist = entry * (sl_pct / 100.0)
    tp_dist = entry * (tp_pct / 100.0)

    if trade_side == "LONG":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:  # SHORT
        sl = entry + sl_dist
        tp = entry - tp_dist

    return round(sl, 6), round(tp, 6)


class Executor:
    def __init__(self, broker: BaseBroker, risk_manager: RiskManager | None = None):
        self.broker = broker
        self.risk = risk_manager or RiskManager()

    async def handle_signal(self, signal: Signal) -> dict:
        """
        Process one signal. Returns a result dict for logging / UI.
        """
        # Decide which side WE will trade
        our_side = (
            _invert_side(signal.side) if config.INVERT_SIGNAL else signal.side
        )

        result = {
            "action": signal.action,
            "symbol": signal.symbol,
            "signal_side": signal.side,
            "side": our_side,  # the side we actually trade
            "success": False,
            "message": "",
            "order_id": None,
        }

        try:
            if signal.action == "BUY":
                return await self._handle_buy(signal, our_side, result)
            elif signal.action == "CLOSE":
                return await self._handle_close(signal, our_side, result)
            else:
                result["message"] = f"Unknown action: {signal.action}"
                return result
        except Exception as e:
            logger.exception("Executor error")
            result["message"] = str(e)
            return result

    async def _handle_buy(
        self, signal: Signal, our_side: str, result: dict
    ) -> dict:
        if signal.price is None or signal.price <= 0:
            result["message"] = "Signal has no valid price"
            return result

        # Our own SL / TP based on fixed percentages (ignore signal SL/TP)
        sl, tp = _calc_sl_tp(
            signal.price, our_side, config.SL_PCT, config.TP_PCT
        )

        decision = await self.risk.check_open(
            self.broker,
            symbol=signal.symbol,
            side=our_side,
            price=signal.price,
            sl=sl,
        )
        if not decision.allowed:
            result["message"] = f"Risk blocked: {decision.reason}"
            logger.warning(result["message"])
            return result

        logger.info(
            f"INVERT: signal {signal.side} → we open {our_side} | "
            f"SL={sl} TP={tp} | qty={decision.quantity}"
        )

        order = await self.broker.open_position(
            symbol=signal.symbol,
            side=our_side,  # type: ignore
            quantity=decision.quantity,
            price=signal.price,
            sl=sl,
            tp=tp,
            signal_score=signal.score,
            meta=f"inverted from signal {signal.side} | {signal.meta or ''}",
        )

        result["success"] = order.success
        result["message"] = order.message
        result["order_id"] = order.order_id
        result["quantity"] = order.quantity
        result["price"] = order.price
        result["sl"] = sl
        result["tp"] = tp
        return result

    async def _handle_close(
        self, signal: Signal, our_side: str, result: dict
    ) -> dict:
        """
        Signal CLOSE LONG means the original long is closing.
        We opened the opposite (SHORT), so we close our SHORT.
        """
        decision = await self.risk.check_close(self.broker, signal.symbol)
        if not decision.allowed:
            result["message"] = f"Risk blocked close: {decision.reason}"
            return result

        # Close the side that WE are holding (the inverted one)
        order = await self.broker.close_position(
            symbol=signal.symbol,
            side=our_side,  # type: ignore
            price=signal.price,
            reason=signal.reason or f"Close inverted {our_side}",
        )

        result["success"] = order.success
        result["message"] = order.message
        result["order_id"] = order.order_id
        result["quantity"] = order.quantity
        result["price"] = order.price

        if order.success and "PnL:" in (order.message or ""):
            try:
                pnl_str = order.message.split("PnL:")[-1].strip()
                pnl = float(pnl_str)
                self.risk.record_pnl(pnl)
            except Exception:
                pass

        return result
