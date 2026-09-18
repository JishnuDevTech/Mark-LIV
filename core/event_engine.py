"""Persistent, deduplicated events owned by JARVIS Core."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
EVENTS_PATH = BASE_DIR / "memory" / "events.json"
PRIORITIES = ("LOW", "NORMAL", "IMPORTANT", "URGENT")
_MAX_EVENTS = 1000
_lock = threading.RLock()
_listener = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    try:
        data = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(events: list[dict]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = EVENTS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(events[-_MAX_EVENTS:], indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, EVENTS_PATH)


def publish(event_type: str, message: str, *, source: str = "jarvis",
            importance: str = "NORMAL", task_id: str = "", project_id: str = "",
            dedupe_key: str = "", **fields) -> dict:
    importance = str(importance).upper()
    if importance not in PRIORITIES:
        importance = "NORMAL"
    classification = {}
    try:
        from core.runtime_state import runtime_state
        classification = runtime_state.decide_event(
            {"event_type": event_type, "message": message, "priority": importance},
        )
    except Exception:
        pass
    with _lock:
        events = _load()
        if dedupe_key:
            for event in events:
                if event.get("dedupe_key") == dedupe_key and event.get("delivery", {}).get("status") != "expired":
                    return dict(event)
        event = {
            "event_id": uuid.uuid4().hex,
            "event_type": str(event_type),
            "timestamp": _now(),
            "source": str(source),
            "importance": importance,
            "task_id": str(task_id or ""),
            "project_id": str(project_id or ""),
            "message": str(message)[:2000],
            "dedupe_key": dedupe_key,
            "classification": classification,
            "delivery": {"status": "pending", "devices": {}},
        }
        # Optional routing metadata (for example target_agent) stays in the
        # canonical event stream without requiring a second communication bus.
        event.update({str(key): value for key, value in fields.items() if value is not None})
        events.append(event)
        _save(events)
    if _listener:
        try:
            _listener(dict(event))
        except Exception:
            pass
    return dict(event)


def set_listener(listener) -> None:
    """Register a best-effort delivery callback; storage remains authoritative."""
    global _listener
    _listener = listener


def classify(event: dict | str, *, context: str = "") -> dict:
    """Classify an event through the central runtime policy."""
    from core.runtime_state import runtime_state
    return runtime_state.classify_event(event, context=context)


def decide(event: dict | str, *, context: str = "") -> dict:
    """Return the relevance/priority/autonomy delivery decision."""
    from core.runtime_state import runtime_state
    return runtime_state.decide_event(event, context=context)


def recent(limit: int = 50, minimum: str = "LOW") -> list[dict]:
    minimum = str(minimum).upper()
    threshold = PRIORITIES.index(minimum) if minimum in PRIORITIES else 0
    with _lock:
        events = _load()
    return [event for event in reversed(events) if PRIORITIES.index(event.get("importance", "NORMAL")) >= threshold][:max(1, min(limit, 200))]


def pending_for_device(device_id: str, limit: int = 100) -> list[dict]:
    pending = []
    for event in recent(1000, "IMPORTANT"):
        delivery = event.get("delivery", {}).get("devices", {})
        if delivery.get(device_id) != "delivered":
            pending.append(event)
        if len(pending) >= limit:
            break
    return pending


def mark_delivered(event_ids: list[str], device_id: str) -> None:
    ids = {str(event_id) for event_id in event_ids}
    with _lock:
        events = _load()
        for event in events:
            if event.get("event_id") in ids:
                delivery = event.setdefault("delivery", {"status": "pending", "devices": {}})
                devices = delivery.setdefault("devices", {})
                devices[str(device_id)] = "delivered"
                delivery["status"] = "delivered"
        _save(events)
