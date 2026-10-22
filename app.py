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
_auth_state: dict = {"status": "idle", "error": None, "session_string": None}


def add_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _lock:
        _shared_logs.appendleft(line)
    try:
        auth_bridge.append_log(line)
    except Exception:
        pass
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
        logger.warning("persist signal: %s", e)
    add_log(
        f"{signal.action} {signal.symbol} signal={signal.side} → we={our_side} | "
        f"{'OK' if result.get('success') else 'FAIL'}: {result.get('message')}"
    )


def start_bot_background(broker_type: str, position_size_pct: float):
    t_existing = _runtime.get("thread")
    if t_existing is not None and t_existing.is_alive() and auth_bridge.is_bot_running_file():
        add_log("Bot already running – ignore Start")
        return

    with _lock:
        _shared_status["stop_requested"] = False
        _shared_status["error"] = None
        _shared_status["bot_running"] = True

    force_login = auth_bridge.is_force_relogin()
    config.reload()
    has_secrets_session = bool((config.TELEGRAM_SESSION_STRING or "").strip())

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            broker = None
            listener = None
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

                auth_bridge.set_bot_running(True, broker_name=broker.name)

                add_log(
                    f"Bot started ({broker.name}) | size={position_size_pct}% | "
                    f"invert={config.INVERT_SIGNAL} | SL={config.SL_PCT}% TP={config.TP_PCT}% | "
                    f"secrets_session={'YES' if has_secrets_session else 'NO'} | "
                    f"force_relogin={force_login}"
                )

                await listener.start()
                add_log("Listener ready – waiting for signals…")

                # CRITICAL: only exit when user presses Stop
                while True:
                    with _lock:
                        if _shared_status.get("stop_requested"):
                            add_log("Stop requested – shutting down")
                            break
                    await asyncio.sleep(0.4)

            except Exception as e:
                logger.exception("Bot failed")
                with _lock:
                    _shared_status["bot_running"] = False
                    _shared_status["error"] = str(e)
                auth_bridge.set_bot_running(False)
                try:
                    auth_bridge.set_status("error", error=str(e))
                except Exception:
                    pass
                add_log(f"ERROR: {e}")
            finally:
                if listener:
                    try:
                        await listener.stop()
                    except Exception:
                        pass
                if broker:
                    try:
                        await broker.disconnect()
                    except Exception:
                        pass

        try:
            loop.run_until_complete(main())
        except Exception as e:
            logger.exception("Event loop error")
            add_log(f"LOOP ERROR: {e}")
        finally:
            with _lock:
                _shared_status["bot_running"] = False
                _shared_status["stop_requested"] = False
            auth_bridge.set_bot_running(False)
            _runtime["listener"] = None
            _runtime["broker"] = None
            _runtime["executor"] = None
            _runtime["loop"] = None
            try:
                # give pending tasks a moment
                pending = asyncio.all_tasks(loop) if not loop.is_closed() else []
                for task in pending:
                    task.cancel()
                if pending and not loop.is_closed():
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass

    t = threading.Thread(target=run, daemon=True, name="bot-runner")
    _runtime["thread"] = t
    t.start()
    time.sleep(1.2)


def stop_bot():
    with _lock:
        _shared_status["stop_requested"] = True
        _shared_status["bot_running"] = False
    auth_bridge.set_bot_running(False)
    try:
        auth_bridge.force_stopped()
    except Exception:
        pass

    listener = _runtime.get("listener")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")

    if listener and loop:
        try:
            if not loop.is_closed() and loop.is_running():

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
                    fut.result(timeout=8)
                except Exception as e:
                    add_log(f"Stop error: {e}")
        except Exception as e:
            add_log(f"Stop cleanup: {e}")

    add_log("Bot stopped")


def _is_bot_running() -> bool:
    file_running = auth_bridge.is_bot_running_file()
    with _lock:
        mem = bool(_shared_status.get("bot_running"))
        stop_req = bool(_shared_status.get("stop_requested"))
    if stop_req:
        return False
    t = _runtime.get("thread")
    alive = t is not None and t.is_alive()
    if alive and (mem or file_running):
        return True
    if file_running:
        return True
    return False


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("📈 Telegram Signal Trader")
st.caption("Nasłuchuje sygnałów z Telegrama i wykonuje je na Paper / Binance.")

bot_running = _is_bot_running()
_auth_st = auth_bridge.get_status()

if bot_running or _auth_st in ("sending_code", "awaiting_code", "awaiting_password"):
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=3000, key="ui_refresh")
    except ImportError:
        pass

with st.sidebar:
    st.header("Ustawienia")
    broker_type = st.selectbox(
        "Broker", ["Paper (Demo)", "Binance Testnet", "Binance Live"], index=0
    )
    position_size_pct = st.number_input(
        "Wielkość pozycji (% kapitału)",
        min_value=1.0,
        max_value=50.0,
        value=float(config.POSITION_SIZE_PCT),
        step=1.0,
    )
    st.info(
        f"Strategia: sygnał odwracany | SL **{config.SL_PCT}%** · TP **{config.TP_PCT}%**"
    )

    st.divider()
    st.subheader("Telegram")
    config.reload()
    st.write(f"Signal bot: **@{config.SIGNAL_BOT_USERNAME}**")
    status = config.status_summary()
    if status["telegram_api_id_set"] and status["telegram_api_hash_set"]:
        st.success("Telegram: API ID + Hash ustawione")
    else:
        st.error("Brak API ID/HASH w secrets")

    has_sess = bool((config.TELEGRAM_SESSION_STRING or "").strip())
    if has_sess:
        st.success("session_string w Secrets: TAK")
    else:
        st.warning("Brak session_string w Secrets")

    st.divider()
    bot_running = _is_bot_running()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ Start bota", type="primary", disabled=bot_running):
            start_bot_background(broker_type, position_size_pct)
            time.sleep(0.3)
            st.rerun()
    with c2:
        if st.button("⏹ Stop bota", disabled=not bot_running):
            stop_bot()
            st.rerun()

    if st.button("🔄 Reset statusu"):
        stop_bot()
        with _lock:
            _shared_status["bot_running"] = False
            _shared_status["stop_requested"] = False
            _shared_status["error"] = None
        auth_bridge.set_bot_running(False)
        auth_bridge.force_stopped()
        if has_sess:
            auth_bridge.clear_force_relogin()
        add_log("Status reset")
        st.rerun()

    st.divider()
    st.subheader("Ponowne logowanie")
    force_on = auth_bridge.is_force_relogin()
    if force_on:
        st.warning("⚡ Tryb kodu aktywny")
        if st.button("✅ Anuluj – użyj sesji z Secrets", type="primary"):
            auth_bridge.clear_force_relogin()
            st.rerun()
    else:
        if st.button("🔐 Ponowne logowanie Telegram", disabled=bot_running):
            stop_bot()
            auth_bridge.set_force_relogin(True)
            st.rerun()

    bot_running = _is_bot_running()
    if bot_running:
        bn = _shared_status.get("broker_name") or auth_bridge.get_broker_name() or "?"
        st.markdown(f"**Status:** 🟢 Działa ({bn})")
    else:
        st.markdown("**Status:** 🔴 Zatrzymany")

    if _shared_status.get("error"):
        st.error(_shared_status["error"])

    bridge = auth_bridge.get_all()
    auth_status = bridge.get("status", "idle")
    st.caption(
        f"Auth: `{auth_status}` · running_file=`{auth_bridge.is_bot_running_file()}`"
    )

    need_code = force_on or auth_status in (
        "sending_code",
        "awaiting_code",
        "awaiting_password",
    )
    if need_code:
        st.warning("📱 Kod z aplikacji Telegram")
        code = st.text_input("Kod", key="tg_code")
        if st.button("Wyślij kod") and code:
            auth_bridge.submit_code(code.strip())
            auth_bridge.set_status("awaiting_code")
            st.rerun()
        if auth_status == "awaiting_password":
            pwd = st.text_input("Hasło 2FA", type="password", key="tg_pwd")
            if st.button("Wyślij hasło") and pwd:
                auth_bridge.submit_password(pwd)
                st.rerun()

    if auth_status == "error" and bridge.get("error"):
        st.error(str(bridge["error"]))

    sess = bridge.get("session_string") or auth_bridge.get_session_string()
    if sess and auth_status in ("authorized", "running"):
        with st.expander("💾 session_string"):
            st.code(sess)

    if st.button("🔄 Odśwież"):
        st.rerun()


tab1, tab2, tab3, tab4 = st.tabs(["Sygnały", "Pozycje", "Historia", "Logi"])

with tab1:
    st.subheader("Ostatnie sygnały")
    with _lock:
        signals = list(_shared_signals)
    try:
        for s in signal_store.load_signals_sync(50):
            key = (s.get("order_id"), s.get("time"))
            if key not in {(x.get("order_id"), x.get("time")) for x in signals}:
                signals.append(s)
    except Exception:
        pass
    if signals:
        signals = sorted(signals, key=lambda x: x.get("time") or "", reverse=True)
        st.dataframe(pd.DataFrame(signals), use_container_width=True)
    else:
        st.info("Brak sygnałów – uruchom bota i poczekaj.")

with tab2:
    st.subheader("Otwarte pozycje")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _is_bot_running() and loop and not loop.is_closed():
        try:
            positions = asyncio.run_coroutine_threadsafe(
                broker.get_open_positions(), loop
            ).result(timeout=5)
            bal = asyncio.run_coroutine_threadsafe(broker.get_balance(), loop).result(
                timeout=5
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Cash", f"{bal.get('cash', 0):.2f}")
            c2.metric("Equity", f"{bal.get('equity', 0):.2f}")
            c3.metric("Pozycje", bal.get("open_positions", 0))
            if positions:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Symbol": p.symbol,
                                "Side": p.side,
                                "Qty": p.quantity,
                                "Entry": p.entry_price,
                                "SL": p.sl,
                                "TP": p.tp,
                            }
                            for p in positions
                        ]
                    ),
                    use_container_width=True,
                )
            else:
                st.write("Brak otwartych pozycji.")
        except Exception as e:
            st.warning(str(e))
    else:
        st.info("Uruchom bota.")

with tab3:
    st.subheader("Historia")
    broker = _runtime.get("broker")
    loop = _runtime.get("loop")
    if broker and _is_bot_running() and loop and not loop.is_closed():
        try:
            trades = asyncio.run_coroutine_threadsafe(
                broker.get_trade_history(100), loop
            ).result(timeout=5)
            if trades:
                st.dataframe(pd.DataFrame(trades), use_container_width=True)
            else:
                st.write("Brak transakcji.")
        except Exception as e:
            st.warning(str(e))
    else:
        st.info("Uruchom bota.")

with tab4:
    st.subheader("Logi")
    with _lock:
        mem_logs = list(_shared_logs)
    file_logs = auth_bridge.load_logs(100)
    # merge, prefer order from file (persists)
    seen = set()
    combined = []
    for line in mem_logs + file_logs:
        if line not in seen:
            seen.add(line)
            combined.append(line)
    if combined:
        st.code("\n".join(combined[:80]), language="text")
    else:
        st.write("Brak logów – po Start pojawią się tutaj i w terminalu po prawej.")

st.caption("session_string w Secrets → tylko Start. Nie klikaj Ponowne logowanie bez potrzeby.")
