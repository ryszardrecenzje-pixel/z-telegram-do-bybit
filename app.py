"""
Streamlit control panel for the Telegram Signal Trader.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

from bot.brokers.paper_broker import PaperBroker
from bot.brokers.binance_broker import BinanceBroker
from bot.executor import Executor
from bot.risk_manager import RiskManager
from bot.telegram_listener import TelegramListener
from config import config
from utils.logger import get_logger

logger = get_logger("streamlit_app")

st.set_page_config(
    page_title="Telegram Signal Trader",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def init_state():
    defaults = {
        "bot_running": False,
        "listener": None,
        "broker": None,
        "executor": None,
        "loop": None,
        "thread": None,
        "recent_signals": [],
        "logs": [],
        "error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


def add_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.insert(0, f"[{ts}] {msg}")
    st.session_state.logs = st.session_state.logs[:200]


def on_signal_callback(signal, result):
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "action": signal.action,
        "symbol": signal.symbol,
        "side": signal.side,
        "price": signal.price,
        "score": signal.score,
        "success": result.get("success"),
        "message": result.get("message"),
        "order_id": result.get("order_id"),
    }
    st.session_state.recent_signals.insert(0, entry)
    st.session_state.recent_signals = st.session_state.recent_signals[:50]
    add_log(
        f"{signal.action} {signal.symbol} → "
        f"{'OK' if result.get('success') else 'FAIL'}: {result.get('message')}"
    )


# ---------------------------------------------------------------------------
# Async runner in background thread
# ---------------------------------------------------------------------------

def start_bot_background(broker_type: str, position_size: float, max_risk: float):
    if st.session_state.bot_running:
        return

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.loop = loop

        async def main():
            try:
                if broker_type == "Paper (Demo)":
                    broker = PaperBroker(starting_balance=10_000.0)
                else:
                    broker = BinanceBroker()

                await broker.connect()
                risk = RiskManager(
                    max_risk_per_trade_pct=max_risk,
                    default_position_size_usd=position_size,
                )
                executor = Executor(broker, risk)
                listener = TelegramListener(executor, on_signal_callback=on_signal_callback)

                st.session_state.broker = broker
                st.session_state.executor = executor
                st.session_state.listener = listener
                st.session_state.bot_running = True
                add_log(f"Bot started with {broker.name} broker")

                await listener.start()
            except Exception as e:
                logger.exception("Bot start failed")
                st.session_state.error = str(e)
                st.session_state.bot_running = False
                add_log(f"ERROR: {e}")

        loop.run_until_complete(main())
        # Keep alive
        try:
            loop.run_forever()
        except Exception:
            pass

    t = threading.Thread(target=run, daemon=True)
    st.session_state.thread = t
    t.start()


def stop_bot():
    listener: Optional[TelegramListener] = st.session_state.listener
    broker = st.session_state.broker
    loop = st.session_state.loop

    if listener and loop:
        async def _stop():
            await listener.stop()
            if broker:
                await broker.disconnect()

        try:
            fut = asyncio.run_coroutine_threadsafe(_stop(), loop)
            fut.result(timeout=10)
        except Exception as e:
            add_log(f"Stop error: {e}")

    st.session_state.bot_running = False
    st.session_state.listener = None
    st.session_state.broker = None
    add_log("Bot stopped")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("📈 Telegram Signal Trader")
st.caption("Nasłuchuje sygnałów z Twojego bota (Telethon) i wykonuje je na koncie demo / paper.")

# Sidebar – settings
with st.sidebar:
    st.header("Ustawienia")

    broker_type = st.selectbox(
        "Broker",
        ["Paper (Demo)", "Binance (Testnet/Live)"],
        index=0,
        help="Na start zawsze używaj Paper. Binance tylko gdy masz klucze i chcesz krypto.",
    )

    position_size = st.number_input(
        "Wielkość pozycji (USD)",
        min_value=10.0,
        max_value=10_000.0,
        value=float(config.DEFAULT_POSITION_SIZE_USD),
        step=10.0,
    )
    max_risk = st.number_input(
        "Max ryzyko na trade (%)",
        min_value=0.1,
        max_value=10.0,
        value=float(config.MAX_RISK_PER_TRADE_PCT),
        step=0.1,
    )

    st.divider()
    st.subheader("Telegram")
    st.write(f"Signal bot: **@{config.SIGNAL_BOT_USERNAME}**")
    if config.TELEGRAM_API_ID:
        st.success("API ID ustawione")
    else:
        st.error("Brak TELEGRAM_API_ID – uzupełnij secrets / .env")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Start bota", type="primary", disabled=st.session_state.bot_running):
            start_bot_background(broker_type, position_size, max_risk)
            st.rerun()
    with col2:
        if st.button("⏹ Stop bota", disabled=not st.session_state.bot_running):
            stop_bot()
            st.rerun()

    status = "🟢 Działa" if st.session_state.bot_running else "🔴 Zatrzymany"
    st.markdown(f"**Status:** {status}")

    if st.session_state.error:
        st.error(st.session_state.error)


# Main area
tab1, tab2, tab3, tab4 = st.tabs(["Sygnały", "Pozycje", "Historia", "Logi"])

with tab1:
    st.subheader("Ostatnie sygnały")
    if st.session_state.recent_signals:
        df = pd.DataFrame(st.session_state.recent_signals)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Brak przetworzonych sygnałów. Uruchom bota i poczekaj na wiadomość od sygnałowego bota.")

with tab2:
    st.subheader("Otwarte pozycje (Paper)")
    if st.session_state.broker and st.session_state.bot_running:
        try:
            loop = st.session_state.loop
            if loop:
                positions = asyncio.run_coroutine_threadsafe(
                    st.session_state.broker.get_open_positions(), loop
                ).result(timeout=5)
                bal = asyncio.run_coroutine_threadsafe(
                    st.session_state.broker.get_balance(), loop
                ).result(timeout=5)

                c1, c2, c3 = st.columns(3)
                c1.metric("Cash", f"{bal.get('cash', 0):.2f} {bal.get('currency', '')}")
                c2.metric("Equity", f"{bal.get('equity', 0):.2f}")
                c3.metric("Otwarte pozycje", bal.get("open_positions", 0))

                if positions:
                    rows = [
                        {
                            "Symbol": p.symbol,
                            "Side": p.side,
                            "Qty": p.quantity,
                            "Entry": p.entry_price,
                            "SL": p.sl,
                            "TP": p.tp,
                            "Score": p.signal_score,
                            "Opened": p.opened_at.isoformat(timespec="seconds"),
                        }
                        for p in positions
                    ]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.write("Brak otwartych pozycji.")
        except Exception as e:
            st.warning(f"Nie można pobrać pozycji: {e}")
    else:
        st.info("Uruchom bota, żeby zobaczyć pozycje.")

with tab3:
    st.subheader("Historia transakcji (Paper)")
    if st.session_state.broker and st.session_state.bot_running:
        try:
            loop = st.session_state.loop
            trades = asyncio.run_coroutine_threadsafe(
                st.session_state.broker.get_trade_history(100), loop
            ).result(timeout=5)
            if trades:
                st.dataframe(pd.DataFrame(trades), use_container_width=True)
            else:
                st.write("Brak transakcji.")
        except Exception as e:
            st.warning(f"Błąd historii: {e}")
    else:
        st.info("Uruchom bota.")

with tab4:
    st.subheader("Logi")
    if st.session_state.logs:
        st.code("\n".join(st.session_state.logs), language="text")
    else:
        st.write("Brak logów.")

st.divider()
st.markdown(
    """
**Uwagi bezpieczeństwa**
- Klucze API i dane Telethon trzymaj wyłącznie w `.streamlit/secrets.toml` lub zmiennych środowiskowych.
- Na początku zawsze używaj trybu **Paper (Demo)**.
- Symbole typu WMT / BA / NOW to akcje – klasyczny Binance Spot ich nie obsłuży. Paper broker jest do tego stworzony.
- Po pierwszym uruchomieniu Telethon poprosi o kod z SMS / aplikacji Telegram (tylko lokalnie).
"""
)