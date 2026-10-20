"""
Uruchom LOKALNIE (nie na Streamlit Cloud), żeby wygenerować StringSession.

  python scripts/generate_session.py

Potem wklej wynik do Streamlit Secrets jako:
  [telegram]
  session_string = "1BVts..."
"""

from __future__ import annotations

import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main():
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone = os.getenv("TELEGRAM_PHONE", "")

    if not api_id or not api_hash:
        print("Ustaw TELEGRAM_API_ID i TELEGRAM_API_HASH w .env")
        sys.exit(1)

    print("Logowanie do Telegrama...")
    print("(kod przyjdzie w aplikacji Telegram / SMS)\n")

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        client.start(phone=phone or None)
        session_string = client.session.save()
        print("\n" + "=" * 60)
        print("SKOPIUJ PONIŻSZY STRING DO STREAMLIT SECRETS:")
        print("=" * 60)
        print(session_string)
        print("=" * 60)
        print(
            "\nW secrets.toml:\n\n"
            "[telegram]\n"
            f'api_id = {api_id}\n'
            f'api_hash = "{api_hash}"\n'
            f'phone = "{phone}"\n'
            f'session_string = "{session_string}"\n'
            'signal_bot_username = "TraderRicziV2"\n'
        )


if __name__ == "__main__":
    main()
