"""JARVIS-owned productivity domains, separate from personal memory."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORE_PATH = BASE_DIR / "memory" / "productivity.json"
_lock = threading.RLock()
_MAX_ITEMS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {"version": 1, "tasks": {}, "schedule": {}, "inbox": {}}


def _load() -> dict:
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _empty()
            base.update(data)
            for key in ("tasks", "schedule", "inbox"):
                if not isinstance(base.get(key), dict):
                    base[key] = {}
            return base
    except Exception:
        pass
    return _empty()


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STORE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, STORE_PATH)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _upsert(domain: str, item: dict, item_id: str = "") -> dict:
    with _lock:
        data = _load()
        key = item_id or item.get("id") or _id(domain.rstrip("s"))
        current = dict(data[domain].get(key, {}))
        current.update(item)
        current["id"] = key
        current.setdefault("created_at", _now())
        current["updated_at"] = _now()
        data[domain][key] = current
        if len(data[domain]) > _MAX_ITEMS:
            ordered = sorted(data[domain], key=lambda k: data[domain][k].get("updated_at", ""))
            for old in ordered[:-_MAX_ITEMS]:
                del data[domain][old]
        _save(data)
        return dict(current)


def create_task(title: str, **fields) -> dict:
    return _upsert("tasks", {
        "title": str(title or "").strip(),
        "status": fields.pop("status", "open"),
        "priority": fields.pop("priority", "normal"),
        "due_at": fields.pop("due_at", ""),
        "category": fields.pop("category", ""),
        "project_id": fields.pop("project_id", ""),
        "source": fields.pop("source", "jarvis"),
        "recurrence": fields.pop("recurrence", ""),
        "notes": fields.pop("notes", ""),
        **fields,
    })


def update_task(task_id: str, **fields) -> dict | None:
    with _lock:
        existing = _load()["tasks"].get(task_id)
    return _upsert("tasks", fields, task_id) if existing else None


def list_tasks(status: str = "", include_archived: bool = False) -> list[dict]:
    with _lock:
        items = list(_load()["tasks"].values())
    if status:
        items = [item for item in items if item.get("status") == status]
    if not include_archived:
        items = [item for item in items if item.get("status") != "archived"]
    return sorted(items, key=lambda item: (item.get("due_at") or "9999", item.get("priority", "normal")))


def complete_task(task_id: str) -> dict | None:
    return update_task(task_id, status="completed", completed_at=_now())


def archive_task(task_id: str) -> dict | None:
    return update_task(task_id, status="archived", archived_at=_now())


def create_event(title: str, **fields) -> dict:
    return _upsert("schedule", {
        "title": str(title or "").strip(),
        "start_at": fields.pop("start_at", ""),
        "end_at": fields.pop("end_at", ""),
        "location": fields.pop("location", ""),
        "source": fields.pop("source", "jarvis"),
        "status": fields.pop("status", "confirmed"),
        **fields,
    })


def list_events(day: str = "") -> list[dict]:
    with _lock:
        items = list(_load()["schedule"].values())
    if day:
        items = [item for item in items if str(item.get("start_at", "")).startswith(day)]
    return sorted(items, key=lambda item: item.get("start_at", "9999"))


def create_inbox_item(subject: str, **fields) -> dict:
    return _upsert("inbox", {
        "subject": str(subject or "").strip(),
        "sender": fields.pop("sender", ""),
        "category": fields.pop("category", "informational"),
        "urgency": fields.pop("urgency", "normal"),
        "status": fields.pop("status", "unread"),
        "source": fields.pop("source", "plugin"),
        "external_id": fields.pop("external_id", ""),
        **fields,
    })


def list_inbox(category: str = "", urgency: str = "") -> list[dict]:
    with _lock:
        items = list(_load()["inbox"].values())
    if category:
        items = [item for item in items if item.get("category") == category]
    if urgency:
        items = [item for item in items if item.get("urgency") == urgency]
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def snapshot(domain: str = "") -> dict:
    with _lock:
        data = _load()
    if domain in ("tasks", "schedule", "inbox"):
        return {domain: data[domain]}
    return data
