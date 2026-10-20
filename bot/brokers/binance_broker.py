"""
Binance broker via ccxt.
Works with Spot Testnet when BINANCE_TESTNET=true.
Note: US stock tickers (WMT, BA, NOW) are NOT available on classic Binance Spot.
They may be available as tokenized assets in supported regions – this is left
as a future extension. For pure stock signals we recommend PaperBroker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import ccxt.async_support as ccxt

from bot.brokers.base import BaseBroker, OrderResult, Position, Side
from config import config
from utils.logger import get_logger

logger = get_logger("binance_broker")


class BinanceBroker(BaseBroker):
    name = "binance"

    def __init__(self):
        self.exchange: Optional[ccxt.binance] = None

    async def connect(self) -> None:
        opts = {
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if config.BINANCE_TESTNET:
            opts["sandbox"] = True  # ccxt testnet flag
            # For official testnet you may also need:
            # opts["urls"] = {"api": {"public": "https://testnet.binance.vision/api", ...}}

        self.exchange = ccxt.binance(opts)
        await self.exchange.load_markets()
        logger.info(f"Binance connected (testnet={config.BINANCE_TESTNET})")

    async def disconnect(self) -> None:
        if self.exchange:
            await self.exchange.close()
            self.exchange = None

    async def get_balance(self) -> dict:
        assert self.exchange
        bal = await self.exchange.fetch_balance()
        usdt = bal.get("USDT", {})
        return {
            "cash": float(usdt.get("free", 0)),
            "equity": float(usdt.get("total", 0)),
            "currency": "USDT",
        }

    async def get_open_positions(self) -> list[Position]:
        # Spot does not have classic "positions". Return empty for now.
        # For futures one would use fetch_positions().
        return []

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
        assert self.exchange
        # Convert stock-like ticker to crypto pair if needed, e.g. BTC → BTC/USDT
        market_symbol = self._to_market_symbol(symbol)
        order_side = "buy" if side == "LONG" else "sell"

        try:
            if price:
                order = await self.exchange.create_order(
                    market_symbol, "limit", order_side, quantity, price
                )
            else:
                order = await self.exchange.create_order(
                    market_symbol, "market", order_side, quantity
                )

            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=float(order.get("average") or order.get("price") or 0),
                message="Binance order placed",
            )
        except Exception as e:
            logger.exception("Binance open failed")
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                message=str(e),
            )

    async def close_position(
        self,
        symbol: str,
        side: Optional[Side] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs,
    ) -> OrderResult:
        # For spot we would sell the asset. Simplified.
        assert self.exchange
        market_symbol = self._to_market_symbol(symbol)
        try:
            # Market sell the whole free balance of the base asset if quantity not given
            if quantity is None:
                bal = await self.exchange.fetch_balance()
                base = market_symbol.split("/")[0]
                quantity = float(bal.get(base, {}).get("free", 0))
                if quantity <= 0:
                    return OrderResult(
                        success=False,
                        symbol=symbol,
                        message="No free balance to close",
                    )

            order = await self.exchange.create_order(
                market_symbol, "market", "sell", quantity
            )
            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                symbol=symbol,
                side="CLOSE",
                quantity=quantity,
                price=float(order.get("average") or 0),
                message="Binance close (sell) executed",
            )
        except Exception as e:
            logger.exception("Binance close failed")
            return OrderResult(success=False, symbol=symbol, message=str(e))

    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        return []

    def _to_market_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        if "/" in s:
            return s
        # Heuristic: if it looks like crypto base, append /USDT
        if s in ("BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE"):
            return f"{s}/USDT"
        # Otherwise leave as-is (may fail for pure stock tickers)
        return s