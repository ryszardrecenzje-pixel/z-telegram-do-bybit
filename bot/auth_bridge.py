"""
File-based bridge for Telegram login code between Streamlit UI and background thread.
Survives Streamlit script reruns (unlike module-level dicts on Cloud).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR

BRIDGE_PATH = DATA_DIR / "auth_bridge.json"


def _read() -> dict:
    try:
        if BRIDGE_PATH.exists():
            return json.loads(BRIDGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    BRIDGE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_status(status: str, **extra: Any) -> None:
    data = _read()
    data["status"] = status
    data["updated_at"] = time.time()
    for k, v in extra.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    _write(data)


def get_status() -> str:
    return str(_read().get("status") or "idle")


def get_all() -> dict:
    return _read()


def submit_code(code: str) -> None:
    data = _read()
    data["code"] = code.strip()
    data["updated_at"] = time.time()
    _write(data)


def submit_password(password: str) -> None:
    data = _read()
    data["password"] = password
    data["updated_at"] = time.time()
    _write(data)


def pop_code() -> Optional[str]:
    data = _read()
    code = data.pop("code", None)
    _write(data)
    return str(code).strip() if code else None


def pop_password() -> Optional[str]:
    data = _read()
    pwd = data.pop("password", None)
    _write(data)
    return str(pwd) if pwd else None


def set_session_string(s: str) -> None:
    set_status(get_status() or "authorized", session_string=s)


def get_session_string() -> Optional[str]:
    s = _read().get("session_string")
    return str(s) if s else None


def reset() -> None:
    _write({"status": "idle", "updated_at": time.time()})
