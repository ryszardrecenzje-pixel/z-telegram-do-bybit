"""
Telethon userbot – listens for signals from the configured bot.

Auth modes:
1. TELEGRAM_SESSION_STRING in secrets → no interactive login (best for Cloud)
2. File session already authorized
3. Interactive via Streamlit UI (code entered in the panel, not terminal)
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import User

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
        auth_state: Optional[dict] = None,
    ):
        self.executor = executor
        self.on_signal_callback = on_signal_callback
        self.client: Optional[TelegramClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Shared with Streamlit UI for interactive login
        # keys: status, phone_code_hash, error, code_event, password_event
        self.auth_state = auth_state if auth_state is not None else {}

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
        if session_str:
            logger.info("Using StringSession from secrets")
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

        # Persist StringSession so next restart needs no code
        try:
            new_str = StringSession.save(self.client.session)
            self.auth_state["session_string"] = new_str
            logger.info(
                "Session string ready – save it to secrets as telegram.session_string "
                "to skip login next time"
            )
        except Exception:
            pass

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
        self.auth_state["status"] = "running"
        logger.info("Listening for signals from @%s ...", signal_bot)
        self._task = asyncio.create_task(self.client.run_until_disconnected())

    async def _interactive_login(self) -> None:
        """Login using code/password provided via Streamlit UI (auth_state)."""
        phone = config.TELEGRAM_PHONE
        if not phone:
            raise RuntimeError("TELEGRAM_PHONE is required for first login")

        logger.info("Sending login code to %s ...", phone)
        self.auth_state["status"] = "awaiting_code"
        self.auth_state["error"] = None

        sent = await self.client.send_code_request(phone)
        self.auth_state["phone_code_hash"] = sent.phone_code_hash

        # Wait until UI provides the code (max ~5 min)
        code = await self._wait_for_key("code", timeout=300)
        if not code:
            self.auth_state["status"] = "error"
            self.auth_state["error"] = "Timeout – nie wpisano kodu z Telegrama"
            raise RuntimeError("Login timeout: no code entered in UI")

        try:
            await self.client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=self.auth_state.get("phone_code_hash"),
            )
        except SessionPasswordNeededError:
            logger.info("2FA password required")
            self.auth_state["status"] = "awaiting_password"
            password = await self._wait_for_key("password", timeout=300)
            if not password:
                self.auth_state["status"] = "error"
                self.auth_state["error"] = "Timeout – nie wpisano hasła 2FA"
                raise RuntimeError("Login timeout: no 2FA password")
            await self.client.sign_in(password=password)

        self.auth_state["status"] = "authorized"
        logger.info("Interactive login successful")

    async def _wait_for_key(self, key: str, timeout: float = 300) -> Optional[str]:
        """Poll auth_state until UI sets key, then consume it."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            val = self.auth_state.pop(key, None)
            if val:
                return str(val).strip()
            await asyncio.sleep(0.5)
        return None

    async def stop(self) -> None:
        self._running = False
        self.auth_state["status"] = "stopped"
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
