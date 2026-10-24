"""
Binance USDT-M Futures broker via ccxt.

Classic short: sell/open short position (profit when price falls).
Classic long: buy/open long position.

Symbol mapping: AAPL → AAPLB → AAPLB/USDT:USDT (or whatever market exists).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import ccxt.async_support as ccxt

from bot.brokers.base import BaseBroker, OrderResult, Position, Side
from bot.symbol_map import map_symbol, to_binance_pair
from config import config
from utils.logger import get_logger

logger = get_logger("binance_broker")


class BinanceBroker(BaseBroker):
    """USDT-M perpetual/futures – supports real SHORT and LONG."""

    name = "binance"

    def __init__(self, live: Optional[bool] = None):
        self.exchange: Optional[ccxt.binanceusdm] = None
        if live is None:
            self.live = not config.BINANCE_TESTNET
        else:
            self.live = live
        self.name = "binance_live" if self.live else "binance_testnet"

    async def connect(self) -> None:
        if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
            raise RuntimeError(
                "Brak BINANCE_API_KEY / BINANCE_API_SECRET w secrets"
            )

        opts = {
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",  # USDT-M perpetual
            },
        }

        # binanceusdm = USDT-M futures
        self.exchange = ccxt.binanceusdm(opts)

        # Demo Trading (demo.binance.com) – NIE używać starego sandbox/testnet
        # https://github.com/ccxt/ccxt/issues/27039
        if not self.live:
            if hasattr(self.exchange, "enable_demo_trading"):
                self.exchange.enable_demo_trading(True)
                logger.info("Binance Demo Trading enabled (demo-fapi.binance.com)")
            else:
                # Fallback: ręczne URL-e demo futures
                try:
                    api = self.exchange.urls.get("api") or {}
                    for k, v in list(api.items()):
                        if isinstance(v, str) and "fapi.binance.com" in v:
                            api[k] = v.replace(
                                "fapi.binance.com", "demo-fapi.binance.com"
                            )
                    self.exchange.urls["api"] = api
                    logger.info("Binance Demo URLs patched manually")
                except Exception as e:
                    logger.warning("Could not enable demo trading: %s", e)

        await self.exchange.load_markets()
        logger.info(
            "Binance USDT-M Futures connected mode=%s markets=%d",
            "LIVE" if self.live else "DEMO",
            len(self.exchange.markets or {}),
        )

    async def disconnect(self) -> None:
        if self.exchange:
            await self.exchange.close()
            self.exchange = None

    def _resolve_market(self, signal_symbol: str) -> str:
        """Map signal symbol to a futures market id present on the exchange."""
        assert self.exchange
        candidates: list[str] = []

        mapped = map_symbol(signal_symbol, "binance")
        for base in (mapped, signal_symbol.upper(), mapped.rstrip("B")):
            base = base.upper()
            if not base:
                continue
            candidates.extend(
                [
                    f"{base}/USDT:USDT",
                    f"{base}/USDT",
                    f"{base}USDT",
                ]
            )

        seen = set()
        ordered = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                ordered.append(c)

        for cand in ordered:
            if cand in self.exchange.markets:
                logger.info(
                    "Futures market OK: %s (signal %s)", cand, signal_symbol
                )
                return cand

        base_u = mapped.upper()
        for m, info in (self.exchange.markets or {}).items():
            b = (info.get("base") or "").upper()
            if b == base_u or b == signal_symbol.upper():
                logger.info("Futures fuzzy: %s → %s", signal_symbol, m)
                return m

        raise ValueError(
            f"Brak rynku futures Binance dla '{signal_symbol}' "
            f"(próbowano: {ordered[:6]}). "
            f"Ustaw [symbols] w secrets, np. {signal_symbol.upper()} = \"AAPLB\""
        )

    async def get_balance(self) -> dict:
        assert self.exchange
        bal = await self.exchange.fetch_balance()
        usdt = bal.get("USDT") or bal.get("total") or {}
        if isinstance(usdt, dict):
            cash = float(usdt.get("free") or 0)
            equity = float(usdt.get("total") or cash)
        else:
            cash = equity = 0.0
        try:
            positions = await self.get_open_positions()
            n = len(positions)
        except Exception:
            n = 0
        return {
            "cash": cash,
            "equity": equity,
            "currency": "USDT",
            "open_positions": n,
        }

    async def get_open_positions(self) -> list[Position]:
        assert self.exchange
        raw = await self.exchange.fetch_positions()
        out: list[Position] = []
        for p in raw or []:
            try:
                contracts = float(p.get("contracts") or 0)
            except Exception:
                contracts = 0
            if contracts == 0:
                continue
            side_raw = (p.get("side") or "").lower()
            if side_raw in ("short", "sell"):
                side: Side = "SHORT"
            else:
                side = "LONG"
            entry = float(p.get("entryPrice") or p.get("average") or 0)
            sym = p.get("symbol") or ""
            out.append(
                Position(
                    symbol=sym,
                    side=side,
                    quantity=abs(contracts),
                    entry_price=entry,
                    opened_at=datetime.utcnow(),
                )
            )
        return out

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
        try:
            market_symbol = self._resolve_market(symbol)
        except Exception as e:
            return OrderResult(
                success=False, symbol=symbol, side=side, message=str(e)
            )

        order_side = "buy" if side == "LONG" else "sell"

        try:
            quantity = float(
                self.exchange.amount_to_precision(market_symbol, quantity)
            )
        except Exception:
            quantity = float(f"{quantity:.6f}")

        if quantity <= 0:
            return OrderResult(
                success=False,
                symbol=symbol,
                side=side,
                message="Quantity after precision is 0",
            )

        params: dict = {}
        try:
            params["positionSide"] = "LONG" if side == "LONG" else "SHORT"
        except Exception:
            pass

        logger.info(
            "Binance futures %s OPEN %s %s qty=%s (signal=%s) SL=%s TP=%s",
            "LIVE" if self.live else "DEMO",
            side,
            market_symbol,
            quantity,
            symbol,
            sl,
            tp,
        )

        try:
            order = await self.exchange.create_order(
                market_symbol,
                "market",
                order_side,
                quantity,
                params=params,
            )
            fill = float(
                order.get("average")
                or order.get("price")
                or 0
            )

            await self._place_sl_tp(
                market_symbol, side, quantity, sl, tp
            )

            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                symbol=market_symbol,
                side=side,
                quantity=quantity,
                price=fill,
                message=f"Futures OPEN {side} {market_symbol}",
            )
        except Exception as e:
            if "positionSide" in str(e) or "position side" in str(e).lower():
                try:
                    order = await self.exchange.create_order(
                        market_symbol, "market", order_side, quantity
                    )
                    fill = float(order.get("average") or order.get("price") or 0)
                    await self._place_sl_tp(
                        market_symbol, side, quantity, sl, tp
                    )
                    return OrderResult(
                        success=True,
                        order_id=str(order.get("id")),
                        symbol=market_symbol,
                        side=side,
                        quantity=quantity,
                        price=fill,
                        message=f"Futures OPEN {side} {market_symbol} (one-way)",
                    )
                except Exception as e2:
                    logger.exception("Binance futures open failed (retry)")
                    return OrderResult(
                        success=False, symbol=symbol, side=side, message=str(e2)
                    )
            logger.exception("Binance futures open failed")
            return OrderResult(
                success=False, symbol=symbol, side=side, message=str(e)
            )

    async def _place_sl_tp(
        self,
        market_symbol: str,
        side: Side,
        quantity: float,
        sl: Optional[float],
        tp: Optional[float],
    ) -> None:
        if not self.exchange:
            return
        close_side = "sell" if side == "LONG" else "buy"
        params_base = {"reduceOnly": True}
        try:
            params_base["positionSide"] = "LONG" if side == "LONG" else "SHORT"
        except Exception:
            pass

        for label, stop_price, otype in (
            ("SL", sl, "STOP_MARKET"),
            ("TP", tp, "TAKE_PROFIT_MARKET"),
        ):
            if not stop_price or stop_price <= 0:
                continue
            try:
                params = dict(params_base)
                params["stopPrice"] = float(
                    self.exchange.price_to_precision(market_symbol, stop_price)
                )
                await self.exchange.create_order(
                    market_symbol,
                    otype,
                    close_side,
                    quantity,
                    params=params,
                )
                logger.info("Placed %s %s @ %s", label, market_symbol, stop_price)
            except Exception as e:
                logger.warning("Could not place %s: %s", label, e)

    async def close_position(
        self,
        symbol: str,
        side: Optional[Side] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs,
    ) -> OrderResult:
        assert self.exchange
        try:
            market_symbol = self._resolve_market(symbol)
        except Exception as e:
            return OrderResult(success=False, symbol=symbol, message=str(e))

        qty = quantity
        pos_side: Optional[Side] = side
        if qty is None or pos_side is None:
            positions = await self.get_open_positions()
            for p in positions:
                if market_symbol.split("/")[0] in p.symbol.replace(":", "/"):
                    qty = p.quantity
                    pos_side = p.side
                    break
                if symbol.upper() in p.symbol.upper():
                    qty = p.quantity
                    pos_side = p.side
                    break

        if not qty or qty <= 0:
            return OrderResult(
                success=False,
                symbol=symbol,
                message="No open futures position to close",
            )

        pos_side = pos_side or "LONG"
        order_side = "sell" if pos_side == "LONG" else "buy"

        try:
            qty = float(self.exchange.amount_to_precision(market_symbol, qty))
        except Exception:
            qty = float(f"{qty:.6f}")

        params: dict = {"reduceOnly": True}
        try:
            params["positionSide"] = "LONG" if pos_side == "LONG" else "SHORT"
        except Exception:
            pass

        logger.info(
            "Binance futures CLOSE %s %s qty=%s",
            pos_side,
            market_symbol,
            qty,
        )

        try:
            order = await self.exchange.create_order(
                market_symbol, "market", order_side, qty, params=params
            )
            fill = float(order.get("average") or order.get("price") or 0)
            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                symbol=market_symbol,
                side=pos_side,
                quantity=qty,
                price=fill,
                message=f"Futures CLOSE {pos_side} {market_symbol}",
            )
        except Exception as e:
            try:
                order = await self.exchange.create_order(
                    market_symbol,
                    "market",
                    order_side,
                    qty,
                    params={"reduceOnly": True},
                )
                fill = float(order.get("average") or order.get("price") or 0)
                return OrderResult(
                    success=True,
                    order_id=str(order.get("id")),
                    symbol=market_symbol,
                    side=pos_side,
                    quantity=qty,
                    price=fill,
                    message=f"Futures CLOSE {pos_side} {market_symbol}",
                )
            except Exception as e2:
                logger.exception("Binance futures close failed")
                return OrderResult(
                    success=False, symbol=symbol, message=str(e2)
                )

    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        return []
