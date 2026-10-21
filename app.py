"""
Streamlit control panel for the Telegram Signal Trader.
Run with:  streamlit run app.py
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

from bot.brokers.paper_broker import PaperBroker
from bot.brokers.binance_broker import BinanceBroker
from bot.executor import Executor
from bot.risk_manager import RiskManager
from bot.telegram_listener import TelegramListener
from bot import auth_bridge
from config import config
from utils.logger import get_logger

logger = get_logger("streamlit_app")

st.set_page_config(
    page_title="Telegram Signal Trader",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Thread-safe shared state (background threads must NOT touch st.session_state)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_shared_logs: deque = deque(maxlen=200)
_shared_signals: deque = deque(maxlen=50)
_shared_status = {
    "bot_running": False,
    "error": None,
    "broker_name": None,
}
_runtime: dict = {
    "broker": None,
    "executor": None,
    "listener": None,
    "loop": None,
}
# Shared with Telethon for interactive login via Streamlit UI
_auth_state: dict = {
    "status": "idle",  # idle | awaiting_code | awaiting_password | authorized | running | error
    "error": None,
    "session_string": None,
}


def add_log(msg: str) -> None:
    """Safe from any thread."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _lock:
        _shared_logs.appendleft(line)
    logger.info(msg)


def on_signal_callback(signal, result) -> None:
    """Called from Telethon thread – must be thread-safe."""
    our_side = result.get("side") or signal.side
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "action": signal.action,
        "symbol": signal.symbol,
        "signal_side": signal.side,
        "our_side": our_side,
        "price": signal.price,
        "sl": result.get("sl"),
        "tp": result.get("tp"),
        "score": signal.score,
        "success": result.get("success"),
        "message": result.get("message"),
        "order_id": result.get("order_id"),
    }
    with _lock:
        _shared_signals.appendleft(entry)
    add_log(
        f"{signal.action} {signal.symbol} signal={signal.side} → we={our_side} | "
        f"{'OK' if result.get('success') else 'FAIL'}: {result.get('message')}"
    )


def init_state():
    defaults = {
        "listener": None,
        "broker": None,
        "executor": None,
        "loop": None,
        "thread": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# ---------------------------------------------------------------------------
# Async runner in background thread
# ---------------------------------------------------------------------------

def start_bot_background(broker_type: str, position_size_pct: float):
    if _shared_status["bot_running"]:
        return

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st_session_loop_ref = loop  # store on shared later via session if needed

        async def main():
            try:
                if broker_type == "Paper (Demo)":
                    broker = PaperBroker(starting_balance=10_000.0)
                else:
                    broker = BinanceBroker()

                await broker.connect()
                risk = RiskManager(position_size_pct=position_size_pct)
                executor = Executor(broker, risk)
                listener = TelegramListener(
                    executor,
                    on_signal_callback=on_signal_callback,
                    auth_state=_auth_state,
                )

                # Module-level refs (thread-safe); session_state is main-thread only
                global _runtime
                _runtime["broker"] = broker
                _runtime["executor"] = executor
                _runtime["listener"] = listener
                _runtime["loop"] = loop

                with _lock:
                    _shared_status["bot_running"] = True
                    _shared_status["error"] = None
                    _shared_status["broker_name"] = broker.name

                add_log(
                    f"Bot started ({broker.name}) | "
                    f"size={position_size_pct}% capital | "
                    f"invert={config.INVERT_SIGNAL} | "
                    f"SL={config.SL_PCT}% TP={config.TP_PCT}%"
                )

                await listener.start()
            except Exception as e:
                logger.exception("Bot start failed")
                with _lock:
                    _shared_status["bot_running"] = False
                    _shared_status["error"] = str(e)
                add_log(f"ERROR: {e}")

        try:
            loop.run_until_complete(main())
            loop.run_forever()
        except Exception as e:
            logger.exception("Event loop error")
            with _lock:
                _shared_status["bot_running"] = False
                _shared_status["error"] = str(e)
            add_log(f"LOOP ERROR: {e}")

    t = threading.Thread(target=run, daemon=True, name="bot-runner")
    st.session_state.thread = t
    t.start()
    # Give the thread a moment to set status
    time.sleep(0.5)


def stop_bot():
    listener: Optional[TelegramListener] = _runtime.get("listener")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")

    if listener and loop and loop.is_running():
        async def _stop():
            try:
                await listener.stop()
            except Exception as e:
                add_log(f"Listener stop: {e}")
            if broker:
                try:
                    await broker.disconnect()
                except Exception as e:
                    add_log(f"Broker disconnect: {e}")

        try:
            fut = asyncio.run_coroutine_threadsafe(_stop(), loop)
            fut.result(timeout=15)
        except Exception as e:
            add_log(f"Stop error: {e}")

        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

    with _lock:
        _shared_status["bot_running"] = False

    _runtime["listener"] = None
    _runtime["broker"] = None
    _runtime["executor"] = None
    _runtime["loop"] = None
    add_log("Bot stopped")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("📈 Telegram Signal Trader")
st.caption(
    "Nasłuchuje sygnałów z Twojego bota (Telethon) i wykonuje je na koncie demo / paper."
)

# Auto-refresh while waiting for Telegram code / 2FA
_auth_st = auth_bridge.get_status()
if _auth_st in ("sending_code", "awaiting_code", "awaiting_password"):
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=2000, key="auth_refresh")
    except ImportError:
        st.info("⏳ Czekam na logowanie Telegram… klikaj **🔄 Odśwież** co 2–3 s.")

with st.sidebar:
    st.header("Ustawienia")

    broker_type = st.selectbox(
        "Broker",
        ["Paper (Demo)", "Binance (Testnet/Live)"],
        index=0,
        help="Na start zawsze używaj Paper. Binance tylko gdy masz klucze i chcesz krypto.",
    )

    position_size_pct = st.number_input(
        "Wielkość pozycji (% kapitału)",
        min_value=1.0,
        max_value=50.0,
        value=float(config.POSITION_SIZE_PCT),
        step=1.0,
        help="Ile procent dostępnego kapitału przeznaczać na jedną transakcję.",
    )

    st.info(
        f"**Strategia:** sygnał odwracany  "
        f"(LONG→SHORT / SHORT→LONG)  |  "
        f"SL **{config.SL_PCT}%**  ·  TP **{config.TP_PCT}%**"
    )

    st.divider()
    st.subheader("Telegram")
    config.reload()
    st.write(f"Signal bot: **@{config.SIGNAL_BOT_USERNAME}**")
    status = config.status_summary()
    if status["telegram_api_id_set"] and status["telegram_api_hash_set"]:
        st.success("Telegram: API ID + Hash ustawione")
    elif status["telegram_api_id_set"]:
        st.warning("Jest API ID, ale brak API HASH")
    else:
        st.error("Brak TELEGRAM_API_ID – uzupełnij secrets / .env")
        with st.expander("Jak ustawić secrets?"):
            st.code(
                """[telegram]
api_id = 31647829
api_hash = "twój_hash"
phone = "+48514934698"
signal_bot_username = "TraderRicziV2"

[binance]
api_key = "..."
api_secret = "..."
testnet = true

[risk]
position_size_pct = 10.0
sl_pct = 1.0
tp_pct = 3.0
invert_signal = true""",
                language="toml",
            )

    st.divider()
    bot_running = _shared_status["bot_running"]
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Start bota", type="primary", disabled=bot_running):
            start_bot_background(broker_type, position_size_pct)
            st.rerun()
    with col2:
        if st.button("⏹ Stop bota", disabled=not bot_running):
            stop_bot()
            st.rerun()

    status_label = "🟢 Działa" if bot_running else "🔴 Zatrzymany"
    st.markdown(f"**Status:** {status_label}")

    if _shared_status.get("error"):
        st.error(_shared_status["error"])

    # --- Interactive Telegram login (code / 2FA) via file bridge ---
    bridge = auth_bridge.get_all()
    auth_status = bridge.get("status", "idle")
    st.caption(f"Auth status: `{auth_status}`")

    if auth_status == "sending_code":
        st.info("📤 Wysyłam prośbę o kod do Telegrama… odśwież za chwilę.")

    # ALWAYS show code field when waiting OR when user may still have a code
    if auth_status in ("awaiting_code", "sending_code", "idle", "error", "stopped"):
        st.warning(
            "📱 Kod z Telegrama (aplikacja → oficjalna wiadomość „Telegram”). "
            "Wpisz poniżej i kliknij Wyślij."
        )
        code = st.text_input("Kod z Telegrama", key="tg_code_input")
        if st.button("Wyślij kod", type="primary") and code:
            auth_bridge.submit_code(code.strip())
            auth_bridge.set_status("awaiting_code")  # keep waiting state
            st.success("Kod zapisany – bot powinien się zalogować. Odśwież za 2–3 s.")
            st.rerun()

    if auth_status == "awaiting_password":
        st.warning("🔒 Wymagane hasło 2FA")
        pwd = st.text_input("Hasło 2FA", type="password", key="tg_pwd_input")
        if st.button("Wyślij hasło") and pwd:
            auth_bridge.submit_password(pwd)
            st.info("Hasło zapisane…")
            st.rerun()

    if auth_status == "error" and bridge.get("error"):
        st.error(f"Logowanie Telegram: {bridge['error']}")

    sess = bridge.get("session_string") or auth_bridge.get_session_string()
    if sess and auth_status in ("authorized", "running"):
        with st.expander("💾 Zapisz sesję do secrets (żeby nie logować się ponownie)"):
            st.caption("Wklej do Streamlit Secrets jako telegram.session_string:")
            st.code(sess)

    if st.button("🔄 Odśwież"):
        st.rerun()


tab1, tab2, tab3, tab4 = st.tabs(["Sygnały", "Pozycje", "Historia", "Logi"])

with tab1:
    st.subheader("Ostatnie sygnały")
    with _lock:
        signals = list(_shared_signals)
    if signals:
        st.dataframe(pd.DataFrame(signals), use_container_width=True)
    else:
        st.info(
            "Brak przetworzonych sygnałów. Uruchom bota i poczekaj na wiadomość od sygnałowego bota."
        )

with tab2:
    st.subheader("Otwarte pozycje (Paper)")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _shared_status["bot_running"] and loop:
        try:
            positions = asyncio.run_coroutine_threadsafe(
                broker.get_open_positions(), loop
            ).result(timeout=5)
            bal = asyncio.run_coroutine_threadsafe(
                broker.get_balance(), loop
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
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _shared_status["bot_running"] and loop:
        try:
            trades = asyncio.run_coroutine_threadsafe(
                broker.get_trade_history(100), loop
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
    with _lock:
        logs = list(_shared_logs)
    if logs:
        st.code("\n".join(logs), language="text")
    else:
        st.write("Brak logów.")

st.divider()
st.markdown(
    """
**Uwagi bezpieczeństwa**
- Klucze API i dane Telethon trzymaj wyłącznie w `.streamlit/secrets.toml` lub zmiennych środowiskowych.
- Na początku zawsze używaj trybu **Paper (Demo)**.
- Symbole typu WMT / BA / NOW to akcje – klasyczny Binance Spot ich nie obsłuży. Paper broker jest do tego stworzony.
- **Streamlit Cloud:** pierwsze logowanie Telethon (kod SMS) działa tylko lokalnie. Na Cloud wrzuć gotowy plik sesji `data/telethon.session`.
"""
)
