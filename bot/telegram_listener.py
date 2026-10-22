"""
Telethon userbot – listens for signals from the configured bot.

On Streamlit Cloud pure push events are unreliable, so we also poll
the private chat with the signal bot every few seconds.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional, Set

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.errors.rpcerrorlist import AuthKeyDuplicatedError
from telethon.sessions import StringSession

from bot import auth_bridge
from bot.executor import Executor
from bot.signal_parser import parse_signal
from config import config
from utils.logger import get_logger

logger = get_logger("telegram_listener")

POLL_INTERVAL_SEC = 8

AUTH_KEY_MSG = (
    "Sesja Telegram unieważniona (AuthKeyDuplicatedError). "
    "Kliknij w panelu «Ponowne logowanie Telegram», potem Start bota, "
    "wpisz kod z aplikacji Telegram i skopiuj nowy session_string do Secrets."
)


class TelegramListener:
    def __init__(
        self,
        executor: Executor,
        on_signal_callback: Optional[Callable] = None,
        auth_state: Optional[dict] = None,
        force_fresh_login: bool = False,
    ):
        self.executor = executor
        self.on_signal_callback = on_signal_callback
        self.force_fresh_login = force_fresh_login
        self.client: Optional[TelegramClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._phone_code_hash: Optional[str] = None
        self._seen_ids: Set[int] = set()
        self._signal_bot_entity = None
        self._fatal_auth = False

    async def start(self) -> None:
        if self._running:
            logger.warning("Listener already running")
            return

        if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
            raise RuntimeError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set "
                "(get them at https://my.telegram.org/apps)"
            )

        # force_relogin from UI button, or constructor flag
        force = self.force_fresh_login or auth_bridge.pop_force_relogin()

        session_str = ""
        if not force:
            session_str = (config.TELEGRAM_SESSION_STRING or "").strip()
            if not session_str:
                session_str = (auth_bridge.get_session_string() or "").strip()
        else:
            logger.info("FORCE fresh login – ignoring existing session_string")
            auth_bridge.clear_session_string()

        if session_str:
            logger.info("Using StringSession")
            session = StringSession(session_str)
        else:
            # Empty StringSession → interactive login (works on Streamlit Cloud via UI code input)
            logger.info("No session – starting interactive login (code via Streamlit UI)")
            session = StringSession()

        self.client = TelegramClient(
            session,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )

        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self._interactive_login()
        except AuthKeyDuplicatedError:
            self._fatal_auth = True
            auth_bridge.set_status("error", error=AUTH_KEY_MSG)
            auth_bridge.set_force_relogin(True)
            raise RuntimeError(AUTH_KEY_MSG)

        me = await self.client.get_me()
        logger.info("Telethon logged in as %s", me.username or me.phone)

        try:
            new_str = StringSession.save(self.client.session)
            auth_bridge.set_session_string(new_str)
            logger.info("Session string saved to auth_bridge – copy it to secrets")
        except Exception:
            logger.exception("Could not export session string")

        signal_bot = config.SIGNAL_BOT_USERNAME.lstrip("@")
        self._signal_bot_entity = await self._resolve_signal_bot(signal_bot)

        try:
            recent = await self.client.get_messages(self._signal_bot_entity, limit=15)
            for msg in recent:
                if msg and msg.id:
                    self._seen_ids.add(msg.id)
            logger.info("Seeded %d recent message ids (won't re-process)", len(self._seen_ids))
        except AuthKeyDuplicatedError:
            self._fatal_auth = True
            auth_bridge.set_status("error", error=AUTH_KEY_MSG)
            auth_bridge.set_force_relogin(True)
            raise RuntimeError(AUTH_KEY_MSG)
        except Exception:
            logger.exception("Could not seed recent messages")

        @self.client.on(events.NewMessage(from_users=self._signal_bot_entity))
        async def handler(event: events.NewMessage.Event):
            try:
                await self._handle_message(event.message)
            except Exception:
                logger.exception("Error in push handler")

        @self.client.on(events.NewMessage(incoming=True))
        async def debug_all(event: events.NewMessage.Event):
            try:
                text = event.message.message or ""
                if not text.strip():
                    return
                sender = await event.get_sender()
                uname = getattr(sender, "username", None) or str(getattr(sender, "id", "?"))
                logger.info("TG incoming from=%s: %s", uname, text[:80].replace("\n", " | "))
            except Exception:
                pass

        self._running = True
        auth_bridge.set_status("running")
        logger.info(
            "Listening for signals from @%s (push + poll every %ss)",
            signal_bot,
            POLL_INTERVAL_SEC,
        )

        self._task = asyncio.create_task(self.client.run_until_disconnected())
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _resolve_signal_bot(self, signal_bot: str):
        try:
            entity = await self.client.get_entity(signal_bot)
            logger.info(
                "Resolved by username @%s → id=%s username=%s",
                signal_bot,
                getattr(entity, "id", "?"),
                getattr(entity, "username", "?"),
            )
            return entity
        except Exception as e:
            logger.warning("get_entity(@%s) failed: %s – searching dialogs...", signal_bot, e)

        target = signal_bot.lower().replace(" ", "")
        matches = []
        bot_dialogs_log = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            is_bot = bool(getattr(entity, "bot", False))
            uname = (getattr(entity, "username", None) or "") or ""
            title = (dialog.name or "") or ""
            first = (getattr(entity, "first_name", None) or "") or ""
            if is_bot or dialog.is_user:
                bot_dialogs_log.append(
                    f"id={getattr(entity, 'id', '?')} bot={is_bot} "
                    f"@{uname or '-'} title={title!r}"
                )
            if not (is_bot or dialog.is_user):
                continue
            candidates = [
                uname.lower(),
                title.lower().replace(" ", ""),
                first.lower().replace(" ", ""),
            ]
            if target in candidates or any(target in c for c in candidates if c):
                matches.append(entity)
                logger.info(
                    "Dialog match: id=%s @%s title=%r",
                    getattr(entity, "id", "?"),
                    uname,
                    title,
                )

        logger.info("Bot/user dialogs found (%d):", len(bot_dialogs_log))
        for line in bot_dialogs_log[:40]:
            logger.info("  %s", line)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            for m in matches:
                if (getattr(m, "username", None) or "").lower() == target:
                    return m
            return matches[0]

        raise RuntimeError(
            f"Nie znaleziono bota pasującego do '{signal_bot}'. "
            "W logach jest lista czatów – sprawdź prawdziwy @username bota."
        )

    async def _poll_loop(self) -> None:
        while self._running and self.client and not self._fatal_auth:
            try:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                if not self._running or not self.client or not self._signal_bot_entity:
                    continue
                if not self.client.is_connected():
                    logger.warning("Client disconnected – stopping poll")
                    break
                messages = await self.client.get_messages(self._signal_bot_entity, limit=10)
                for msg in reversed(list(messages)):
                    if not msg or not msg.id:
                        continue
                    if msg.id in self._seen_ids:
                        continue
                    self._seen_ids.add(msg.id)
                    if len(self._seen_ids) > 500:
                        self._seen_ids = set(sorted(self._seen_ids)[-300:])
                    await self._handle_message(msg)
            except asyncio.CancelledError:
                break
            except AuthKeyDuplicatedError:
                self._fatal_auth = True
                self._running = False
                logger.error(AUTH_KEY_MSG)
                auth_bridge.set_status("error", error=AUTH_KEY_MSG)
                auth_bridge.set_force_relogin(True)
                try:
                    if self.client:
                        await self.client.disconnect()
                except Exception:
                    pass
                break
            except ConnectionError as e:
                logger.warning("Connection error in poll: %s – stopping", e)
                break
            except Exception:
                if self._fatal_auth:
                    break
                logger.exception("Poll loop error")
                await asyncio.sleep(5)

    async def _handle_message(self, message) -> None:
        if message is None or self._fatal_auth:
            return
        mid = getattr(message, "id", None)
        if mid is not None:
            if mid in self._seen_ids and mid in getattr(self, "_processed_signals", set()):
                return
            self._seen_ids.add(mid)

        text = message.message or ""
        if not text.strip():
            return

        logger.info("Processing msg id=%s: %s", mid, text[:120].replace("\n", " | "))

        signal = parse_signal(text)
        if signal is None:
            logger.info("Not a SIGNAL format – ignored")
            return

        if not hasattr(self, "_processed_signals"):
            self._processed_signals = set()
        if mid is not None:
            if mid in self._processed_signals:
                return
            self._processed_signals.add(mid)

        result = await self.executor.handle_signal(signal)
        logger.info("Execution result: %s", result)

        if self.on_signal_callback:
            try:
                self.on_signal_callback(signal, result)
            except Exception:
                logger.exception("UI callback error")

    async def _interactive_login(self) -> None:
        phone = config.TELEGRAM_PHONE
        if not phone:
            raise RuntimeError(
                "TELEGRAM_PHONE jest wymagany do logowania. "
                "Dodaj w Streamlit Secrets: [telegram] phone = \"+48...\""
            )

        logger.info("Sending login code to %s ...", phone)
        auth_bridge.set_status("sending_code", error=None)

        try:
            sent = await self.client.send_code_request(phone)
            self._phone_code_hash = sent.phone_code_hash
            auth_bridge.set_status("awaiting_code", phone_code_hash=sent.phone_code_hash)
            logger.info("Code requested OK. Sprawdź aplikację Telegram.")
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
        try:
            auth_bridge.force_stopped()
        except Exception:
            auth_bridge.set_status("stopped")
        for t in (self._poll_task, self._task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        self._poll_task = None
        self._task = None
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        logger.info("Telegram listener stopped")

    @property
    def is_running(self) -> bool:
        return self._running and not self._fatal_auth
