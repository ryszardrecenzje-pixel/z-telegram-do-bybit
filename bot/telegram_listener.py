"""
Telethon userbot – listens for signals.

Login code is entered in Streamlit UI and passed via auth_bridge (file),
so it works on Streamlit Cloud across script reruns.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import User

from bot import auth_bridge
from bot.executor import Executor
from bot.signal_parser import parse_signal
from config import config
from utils.logger import get_logger

logger = get_logger("telegram_listener")


class TelegramListener:
    def __init__(
        self,
        executor: Executor,
        on_signal_callback: Optional[Callable] = None,
        auth_state: Optional[dict] = None,  # kept for compat, unused
    ):
        self.executor = executor
        self.on_signal_callback = on_signal_callback
        self.client: Optional[TelegramClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._phone_code_hash: Optional[str] = None

    async def start(self) -> None:
        if self._running:
            logger.warning("Listener already running")
            return

        if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
            raise RuntimeError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set "
                "(get them at https://my.telegram.org/apps)"
            )

        session_str = (config.TELEGRAM_SESSION_STRING or "").strip()
        if not session_str:
            # maybe saved from previous interactive login
            session_str = (auth_bridge.get_session_string() or "").strip()

        if session_str:
            logger.info("Using StringSession")
            session = StringSession(session_str)
        else:
            logger.info("Using file session: %s", config.SESSION_FILE)
            session = config.SESSION_FILE

        self.client = TelegramClient(
            session,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )

        await self.client.connect()

        if not await self.client.is_user_authorized():
            await self._interactive_login()

        me = await self.client.get_me()
        logger.info("Telethon logged in as %s", me.username or me.phone)

        try:
            new_str = StringSession.save(self.client.session)
            auth_bridge.set_session_string(new_str)
            logger.info("Session string saved to auth_bridge – copy it to secrets")
        except Exception:
            logger.exception("Could not export session string")

        signal_bot = config.SIGNAL_BOT_USERNAME.lstrip("@")

        @self.client.on(events.NewMessage)
        async def handler(event: events.NewMessage.Event):
            try:
                sender = await event.get_sender()
                if not isinstance(sender, User):
                    return
                if not sender.username or sender.username.lower() != signal_bot.lower():
                    return

                text = event.message.message or ""
                logger.info("Message from @%s: %s...", sender.username, text[:120])

                signal = parse_signal(text)
                if signal is None:
                    return

                result = await self.executor.handle_signal(signal)
                logger.info("Execution result: %s", result)

                if self.on_signal_callback:
                    try:
                        self.on_signal_callback(signal, result)
                    except Exception:
                        logger.exception("UI callback error")
            except Exception:
                logger.exception("Error handling Telegram message")

        self._running = True
        auth_bridge.set_status("running")
        logger.info("Listening for signals from @%s ...", signal_bot)
        self._task = asyncio.create_task(self.client.run_until_disconnected())

    async def _interactive_login(self) -> None:
        phone = config.TELEGRAM_PHONE
        if not phone:
            raise RuntimeError("TELEGRAM_PHONE is required for first login")

        logger.info("Sending login code to %s ...", phone)
        auth_bridge.set_status("sending_code", error=None)

        try:
            sent = await self.client.send_code_request(phone)
            self._phone_code_hash = sent.phone_code_hash
            auth_bridge.set_status("awaiting_code", phone_code_hash=sent.phone_code_hash)
            logger.info(
                "Code requested OK. Sprawdź aplikację Telegram (oficjalna wiadomość z kodem)."
            )
        except Exception as e:
            auth_bridge.set_status("error", error=f"Nie udało się wysłać kodu: {e}")
            logger.exception("send_code_request failed")
            raise

        code = await self._wait_pop(auth_bridge.pop_code, timeout=600)
        if not code:
            auth_bridge.set_status("error", error="Timeout – nie wpisano kodu w panelu")
            raise RuntimeError("Login timeout: no code entered in UI")

        try:
            await self.client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=self._phone_code_hash,
            )
        except SessionPasswordNeededError:
            logger.info("2FA password required")
            auth_bridge.set_status("awaiting_password")
            password = await self._wait_pop(auth_bridge.pop_password, timeout=600)
            if not password:
                auth_bridge.set_status("error", error="Timeout – brak hasła 2FA")
                raise RuntimeError("Login timeout: no 2FA password")
            await self.client.sign_in(password=password)

        auth_bridge.set_status("authorized")
        logger.info("Interactive login successful")

    async def _wait_pop(self, pop_fn, timeout: float = 600) -> Optional[str]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            val = pop_fn()
            if val:
                return val
            await asyncio.sleep(0.5)
        return None

    async def stop(self) -> None:
        self._running = False
        auth_bridge.set_status("stopped")
        if self.client:
            await self.client.disconnect()
            self.client = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Telegram listener stopped")

    @property
    def is_running(self) -> bool:
        return self._running
