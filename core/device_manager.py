"""Persistent paired-device registry without storing bearer tokens in plaintext."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

from core.credentials import delete_secret, get_secret, set_secret

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = BASE_DIR / "config" / "phone_devices.json"
_lock = threading.RLock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = REGISTRY_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, REGISTRY_PATH)


def register(session_key: str) -> tuple[str, str] | None:
    device_token = secrets.token_urlsafe(32)
    device_id = secrets.token_urlsafe(12)
    if not set_secret("phone_device", device_id, session_key):
        return None
    with _lock:
        data = _load()
        data[_hash(device_token)] = {"device_id": device_id}
        _save(data)
    return device_token, device_id


def resolve(device_token: str) -> tuple[str, str] | None:
    with _lock:
        item = _load().get(_hash(device_token))
    if not item:
        return None
    device_id = item.get("device_id", "")
    session_key = get_secret("phone_device", device_id)
    return (session_key, device_id) if session_key and device_id else None


def revoke_all() -> int:
    with _lock:
        data = _load()
        count = 0
        for item in data.values():
            device_id = item.get("device_id", "")
            if device_id:
                delete_secret("phone_device", device_id)
                count += 1
        _save({})
        return count
