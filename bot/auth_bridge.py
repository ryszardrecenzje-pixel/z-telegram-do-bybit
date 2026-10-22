"""
File-based bridge for Telegram login code between Streamlit UI and background thread.
Survives Streamlit script reruns (unlike module-level dicts on Cloud).
"""

from __future__ import annotations

import json
import time
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
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        BRIDGE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    data = _read()
    data["session_string"] = s
    data["status"] = data.get("status") or "authorized"
    data["updated_at"] = time.time()
    # Successful login always ends force-relogin mode
    data.pop("force_relogin", None)
    _write(data)


def get_session_string() -> Optional[str]:
    s = _read().get("session_string")
    return str(s) if s else None


def clear_session_string() -> None:
    data = _read()
    data.pop("session_string", None)
    _write(data)


def set_force_relogin(value: bool = True) -> None:
    """Next bot start ignores secrets session_string and does interactive login."""
    data = _read()
    if value:
        data["force_relogin"] = True
        data.pop("session_string", None)
        data.pop("code", None)
        data.pop("password", None)
        data["status"] = "idle"
    else:
        data.pop("force_relogin", None)
    data["updated_at"] = time.time()
    _write(data)


def clear_force_relogin() -> None:
    """Cancel force-relogin – next Start will use secrets session_string."""
    data = _read()
    data.pop("force_relogin", None)
    data["updated_at"] = time.time()
    if data.get("status") in ("error", "awaiting_code", "sending_code", "awaiting_password"):
        data["status"] = "idle"
    data.pop("code", None)
    data.pop("password", None)
    _write(data)


def pop_force_relogin() -> bool:
    data = _read()
    flag = bool(data.pop("force_relogin", False))
    if flag:
        data.pop("session_string", None)
        _write(data)
    return flag


def is_force_relogin() -> bool:
    return bool(_read().get("force_relogin"))


def reset() -> None:
    _write({"status": "idle", "updated_at": time.time()})


def force_stopped() -> None:
    """Hard-set stopped so UI never sticks on green after Stop."""
    data = _read()
    data["status"] = "stopped"
    data["updated_at"] = time.time()
    data.pop("code", None)
    data.pop("password", None)
    # Do NOT clear force_relogin here – user may have armed it intentionally
    _write(data)
