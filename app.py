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

try:
    from bot import signal_store
except ImportError:
    import bot.signal_store as signal_store  # type: ignore

from config import config
from utils.logger import get_logger

logger = get_logger("streamlit_app")

st.set_page_config(
    page_title="Telegram Signal Trader",
    page_icon="📈",
    layout="wide",
)

_lock = threading.Lock()
_shared_logs: deque = deque(maxlen=200)
_shared_signals: deque = deque(maxlen=50)
_shared_status = {
    "bot_running": False,
    "error": None,
    "broker_name": None,
    "stop_requested": False,
}
_runtime: dict = {
    "broker": None,
    "executor": None,
    "listener": None,
    "loop": None,
    "thread": None,
}
_auth_state: dict = {
    "status": "idle",
    "error": None,
    "session_string": None,
}


def add_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _lock:
        _shared_logs.appendleft(line)
    logger.info(msg)


def on_signal_callback(signal, result) -> None:
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

    try:
        signal_store.save_signal_sync(entry)
    except Exception as e:
        logger.warning("Could not persist signal (sync): %s", e)
        try:
            loop = _runtime.get("loop")
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(signal_store.save_signal(entry), loop)
        except Exception as e2:
            logger.warning("Could not persist signal (async): %s", e2)

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


def start_bot_background(broker_type: str, position_size_pct: float):
    with _lock:
        if _shared_status["bot_running"]:
            return
        _shared_status["stop_requested"] = False
        _shared_status["error"] = None

    force_login = auth_bridge.is_force_relogin()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            try:
                if broker_type == "Paper (Demo)":
                    broker = PaperBroker(starting_balance=10_000.0)
                elif broker_type == "Binance Testnet":
                    broker = BinanceBroker(live=False)
                else:
                    broker = BinanceBroker(live=True)

                await broker.connect()
                risk = RiskManager(position_size_pct=position_size_pct)
                executor = Executor(broker, risk)
                listener = TelegramListener(
                    executor,
                    on_signal_callback=on_signal_callback,
                    auth_state=_auth_state,
                    force_fresh_login=force_login,
                )

                global _runtime
                _runtime["broker"] = broker
                _runtime["executor"] = executor
                _runtime["listener"] = listener
                _runtime["loop"] = loop

                with _lock:
                    _shared_status["bot_running"] = True
                    _shared_status["error"] = None
                    _shared_status["broker_name"] = broker.name
                    _shared_status["stop_requested"] = False

                has_sess = bool((config.TELEGRAM_SESSION_STRING or "").strip()) and not force_login
                add_log(
                    f"Bot started ({broker.name}) | "
                    f"size={position_size_pct}% capital | "
                    f"invert={config.INVERT_SIGNAL} | "
                    f"SL={config.SL_PCT}% TP={config.TP_PCT}% | "
                    f"session_string={'YES' if has_sess else 'NO (fresh login)'} | "
                    f"force_relogin={force_login}"
                )

                await listener.start()
            except Exception as e:
                logger.exception("Bot start failed")
                with _lock:
                    _shared_status["bot_running"] = False
                    _shared_status["error"] = str(e)
                try:
                    auth_bridge.set_status("error", error=str(e))
                except Exception:
                    pass
                add_log(f"ERROR: {e}")

        try:
            loop.run_until_complete(main())
            while True:
                with _lock:
                    if _shared_status.get("stop_requested") or not _shared_status.get("bot_running"):
                        break
                loop.run_until_complete(asyncio.sleep(0.5))
        except Exception as e:
            logger.exception("Event loop error")
            with _lock:
                _shared_status["bot_running"] = False
                _shared_status["error"] = str(e)
            add_log(f"LOOP ERROR: {e}")
        finally:
            with _lock:
                _shared_status["bot_running"] = False
            try:
                # keep error status if set; otherwise mark stopped
                if auth_bridge.get_status() not in ("error", "awaiting_code", "awaiting_password", "sending_code"):
                    auth_bridge.force_stopped()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=run, daemon=True, name="bot-runner")
    _runtime["thread"] = t
    st.session_state.thread = t
    t.start()
    time.sleep(0.8)


def stop_bot():
    with _lock:
        _shared_status["stop_requested"] = True
        _shared_status["bot_running"] = False

    try:
        auth_bridge.force_stopped()
    except Exception:
        try:
            auth_bridge.set_status("stopped")
        except Exception:
            pass

    listener: Optional[TelegramListener] = _runtime.get("listener")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")

    if listener and loop and getattr(loop, "is_running", lambda: False)():
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
        _shared_status["stop_requested"] = True

    _runtime["listener"] = None
    _runtime["broker"] = None
    _runtime["executor"] = None
    _runtime["loop"] = None
    add_log("Bot stopped")


def _is_bot_running() -> bool:
    with _lock:
        if _shared_status.get("stop_requested"):
            return False
        running = bool(_shared_status.get("bot_running"))

    if not running:
        return False

    t = _runtime.get("thread")
    if t is not None and not t.is_alive():
        with _lock:
            _shared_status["bot_running"] = False
        try:
            if auth_bridge.get_status() not in ("error", "awaiting_code", "awaiting_password", "sending_code"):
                auth_bridge.force_stopped()
        except Exception:
            pass
        return False

    return True


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("📈 Telegram Signal Trader")
st.caption(
    "Nasłuchuje sygnałów z Twojego bota (Telethon) i wykonuje je na koncie demo / paper."
)

_auth_st = auth_bridge.get_status()
if _auth_st in ("sending_code", "awaiting_code", "awaiting_password") or _is_bot_running():
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=2500, key="ui_refresh")
    except ImportError:
        if _auth_st in ("sending_code", "awaiting_code", "awaiting_password"):
            st.info("⏳ Czekam na logowanie Telegram… klikaj **🔄 Odśwież** co 2–3 s.")

with st.sidebar:
    st.header("Ustawienia")

    broker_type = st.selectbox(
        "Broker",
        ["Paper (Demo)", "Binance Testnet", "Binance Live"],
        index=0,
        help=(
            "Paper = wirtualne konto. "
            "Binance = USDT-M Futures. "
            "Live = prawdziwe środki."
        ),
    )

    position_size_pct = st.number_input(
        "Wielkość pozycji (% kapitału)",
        min_value=1.0,
        max_value=50.0,
        value=float(config.POSITION_SIZE_PCT),
        step=1.0,
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
        st.error("Brak TELEGRAM_API_ID – uzupełnij secrets")

    if not status.get("telegram_phone_set"):
        st.warning("Brak phone w secrets – potrzebny do ponownego logowania")

    st.divider()
    bot_running = _is_bot_running()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶ Start bota", type="primary", disabled=bot_running):
            start_bot_background(broker_type, position_size_pct)
            st.rerun()
    with col2:
        if st.button("⏹ Stop bota", disabled=not bot_running):
            stop_bot()
            st.rerun()

    if st.button("🔄 Reset statusu (jeśli zablokowany)"):
        stop_bot()
        with _lock:
            _shared_status["bot_running"] = False
            _shared_status["stop_requested"] = True
            _shared_status["error"] = None
        try:
            auth_bridge.force_stopped()
        except Exception:
            pass
        add_log("Status force-reset by user")
        st.rerun()

    # --- Re-login without local machine ---
    st.divider()
    st.subheader("Ponowne logowanie")
    st.caption(
        "Gdy sesja padła (AuthKeyDuplicated) albo chcesz nową sesję – "
        "bez generowania lokalnie."
    )
    if st.button("🔐 Ponowne logowanie Telegram", disabled=bot_running):
        stop_bot()
        auth_bridge.set_force_relogin(True)
        with _lock:
            _shared_status["error"] = None
            _shared_status["bot_running"] = False
            _shared_status["stop_requested"] = True
        add_log("Force re-login armed – next Start will request Telegram code")
        st.success(
            "Gotowe. Kliknij **▶ Start bota**. "
            "Kod przyjdzie do aplikacji Telegram – wpisz go poniżej."
        )
        st.rerun()

    if auth_bridge.is_force_relogin():
        st.warning("⚡ Tryb nowego logowania aktywny – Start poprosi o kod SMS/Telegram")

    if bot_running:
        bn = _shared_status.get("broker_name") or "?"
        status_label = f"🟢 Działa ({bn}) – nasłuchuje @{config.SIGNAL_BOT_USERNAME}"
    else:
        status_label = "🔴 Zatrzymany"
    st.markdown(f"**Status:** {status_label}")

    if _shared_status.get("error"):
        st.error(_shared_status["error"])

    bridge = auth_bridge.get_all()
    auth_status = bridge.get("status", "idle")
    st.caption(f"Auth status: `{auth_status}`")

    if auth_status == "sending_code":
        st.info("📤 Wysyłam prośbę o kod do Telegrama… odśwież za chwilę.")

    if auth_status in ("awaiting_code", "sending_code", "idle", "error", "stopped"):
        st.warning(
            "📱 Kod z Telegrama (aplikacja → oficjalna wiadomość «Telegram»). "
            "Wpisz poniżej i kliknij Wyślij."
        )
        code = st.text_input("Kod z Telegrama", key="tg_code_input")
        if st.button("Wyślij kod", type="primary") and code:
            auth_bridge.submit_code(code.strip())
            auth_bridge.set_status("awaiting_code")
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
        with st.expander("💾 WAŻNE: zapisz nową sesję do Secrets", expanded=True):
            st.caption(
                "Wklej do Streamlit Secrets jako telegram.session_string "
                "(zastąp starą wartość), potem Save."
            )
            st.code(sess)
            st.info(
                "Po zapisaniu w Secrets bot będzie startował bez kodu. "
                "Nie uruchamiaj tej samej sesji lokalnie i na Cloud naraz."
            )

    if st.button("🔄 Odśwież"):
        st.rerun()


tab1, tab2, tab3, tab4 = st.tabs(["Sygnały", "Pozycje", "Historia", "Logi"])

with tab1:
    st.subheader("Ostatnie sygnały")
    with _lock:
        signals = list(_shared_signals)
    try:
        db_signals = signal_store.load_signals_sync(50)
        seen = {(s.get("order_id"), s.get("time")) for s in signals}
        for s in db_signals:
            key = (s.get("order_id"), s.get("time"))
            if key not in seen:
                signals.append(s)
                seen.add(key)
    except Exception as e:
        logger.warning("UI load signals failed: %s", e)

    if signals:
        try:
            signals = sorted(signals, key=lambda x: x.get("time") or "", reverse=True)
        except Exception:
            pass
        st.dataframe(pd.DataFrame(signals), use_container_width=True)
    else:
        st.info(
            "Brak przetworzonych sygnałów. Uruchom bota i poczekaj na wiadomość od sygnałowego bota."
        )

with tab2:
    st.subheader("Otwarte pozycje")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _is_bot_running() and loop:
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
    st.subheader("Historia transakcji")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _is_bot_running() and loop:
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
- Klucze API i dane Telethon trzymaj wyłącznie w Streamlit Secrets.
- Na początku zawsze używaj trybu **Paper (Demo)**.
- **Binance Live** = prawdziwe środki.
- Jedna `session_string` = tylko Cloud **albo** lokalnie, nigdy jednocześnie.
- Po **Ponownym logowaniu** skopiuj nowy `session_string` do Secrets.
"""
)
