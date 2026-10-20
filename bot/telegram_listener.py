"""
Telethon userbot that listens for messages from the configured signal bot
and forwards parsed signals to the Executor.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from telethon import TelegramClient, events
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
    ):
        self.executor = executor
        self.on_signal_callback = on_signal_callback  # for Streamlit UI updates
        self.client: Optional[TelegramClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            logger.warning("Listener already running")
            return

        if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
            raise RuntimeError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set "
                "(get them at https://my.telegram.org/apps)"
            )

        self.client = TelegramClient(
            config.SESSION_FILE,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )

        await self.client.start(phone=config.TELEGRAM_PHONE)
        me = await self.client.get_me()
        logger.info(f"Telethon logged in as {me.username or me.phone}")

        signal_bot = config.SIGNAL_BOT_USERNAME.lstrip("@")

        @self.client.on(events.NewMessage)
        async def handler(event: events.NewMessage.Event):
            try:
                sender = await event.get_sender()
                if not isinstance(sender, User):
                    return
                # Match by username
                if not sender.username or sender.username.lower() != signal_bot.lower():
                    return

                text = event.message.message or ""
                logger.info(f"Message from @{sender.username}: {text[:120]}...")

                signal = parse_signal(text)
                if signal is None:
                    return

                result = await self.executor.handle_signal(signal)
                logger.info(f"Execution result: {result}")

                if self.on_signal_callback:
                    try:
                        self.on_signal_callback(signal, result)
                    except Exception:
                        logger.exception("UI callback error")

            except Exception:
                logger.exception("Error handling Telegram message")

        self._running = True
        logger.info(f"Listening for signals from @{signal_bot} ...")

        # Keep the client running
        self._task = asyncio.create_task(self.client.run_until_disconnected())

    async def stop(self) -> None:
        self._running = False
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