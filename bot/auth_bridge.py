"""
File-based bridge for Telegram login, bot running flag, and UI logs.
Survives Streamlit script reruns on Cloud.
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Optional

from config import DATA_DIR

BRIDGE_PATH = DATA_DIR / "auth_bridge.json"
LOG_PATH = DATA_DIR / "ui_logs.json"


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
    data = _read()
    if value:
        data["force_relogin"] = True
        data.pop("session_string", None)
        data.pop("code", None)
        data.pop("password", None)
        data["status"] = "idle"
        data["bot_running"] = False
    else:
        data.pop("force_relogin", None)
    data["updated_at"] = time.time()
    _write(data)


def clear_force_relogin() -> None:
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


def set_bot_running(running: bool, broker_name: str | None = None) -> None:
    data = _read()
    data["bot_running"] = bool(running)
    data["bot_updated_at"] = time.time()
    if broker_name is not None:
        data["broker_name"] = broker_name
    if running:
        data["status"] = "running"
    elif data.get("status") == "running":
        data["status"] = "stopped"
    data["updated_at"] = time.time()
    _write(data)


def is_bot_running_file() -> bool:
    return bool(_read().get("bot_running"))


def get_broker_name() -> str:
    return str(_read().get("broker_name") or "")


def force_stopped() -> None:
    data = _read()
    data["status"] = "stopped"
    data["bot_running"] = False
    data["updated_at"] = time.time()
    data["bot_updated_at"] = time.time()
    data.pop("code", None)
    data.pop("password", None)
    _write(data)


def append_log(line: str) -> None:
    """Append one log line for the UI (max 150)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logs: list = []
        if LOG_PATH.exists():
            try:
                logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            except Exception:
                logs = []
        if not isinstance(logs, list):
            logs = []
        logs.insert(0, line)
        logs = logs[:150]
        LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_logs(limit: int = 100) -> list[str]:
    try:
        if LOG_PATH.exists():
            logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(logs, list):
                return [str(x) for x in logs[:limit]]
    except Exception:
        pass
    return []
