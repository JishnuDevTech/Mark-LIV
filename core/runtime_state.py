"""The persisted JARVIS runtime facade.

This is an index and coordination surface, not a second implementation of
memory, tasks, events, permissions, or device managers.  Existing managers
remain authoritative; this facade records their latest health/snapshot and
provides the cross-system decisions needed by the runtime.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "memory" / "runtime_state.json"
_lock = threading.RLock()
_CATEGORIES = (
    "device", "mobile", "computer", "screen", "plugin", "provider",
    "agent", "task", "event", "notification",
)
_PRIORITIES = ("LOW", "NORMAL", "IMPORTANT", "URGENT")
_AUTONOMY = ("manual", "suggest", "supervised", "autonomous")
_AVAILABILITY = ("available", "busy", "away", "do_not_disturb", "offline")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {
        "version": 1, "updated_at": _now(),
        "components": {key: {} for key in _CATEGORIES},
        "availability": {"status": "available", "updated_at": _now()},
        "autonomy": "suggest", "goals": {}, "background_tasks": {},
        "preferences": {}, "health": {}, "recovery": {}, "external_capabilities": {},
        "experiences": [],
    }


def _load() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    base = _empty()
    if isinstance(data, dict):
        base.update(data)
    base["components"] = {
        key: dict((data.get("components") or {}).get(key, {}))
        for key in _CATEGORIES
    }
    for key in ("goals", "background_tasks", "preferences", "health", "recovery"):
        if not isinstance(base.get(key), dict):
            base[key] = {}
    if not isinstance(base.get("experiences"), list):
        base["experiences"] = []
    if not isinstance(base.get("external_capabilities"), dict):
        base["external_capabilities"] = {}
    return base


def _save(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, STATE_PATH)


class RuntimeState:
    """Small, thread-safe facade used by main.py and integration managers."""

    categories = _CATEGORIES

    def __init__(self, path: Path = STATE_PATH):
        self.path = Path(path)
        self._resume_hooks: dict[str, Callable] = {}

    def snapshot(self) -> dict:
        with _lock:
            return _load()

    def update_component(self, category: str, name: str, state: Any) -> dict:
        category = str(category).lower()
        if category not in _CATEGORIES:
            raise ValueError(f"Unknown runtime component: {category}")
        with _lock:
            data = _load()
            data["components"][category][str(name)] = {
                "state": state, "updated_at": _now(),
            }
            data["updated_at"] = _now()
            _save(data)
            return dict(data["components"][category][str(name)])

    def update_components(self, category: str, states: dict) -> None:
        for name, state in (states or {}).items():
            self.update_component(category, name, state)

    def set_availability(self, status: str, **details) -> dict:
        status = str(status).lower()
        if status not in _AVAILABILITY:
            raise ValueError(f"Availability must be one of {_AVAILABILITY}")
        with _lock:
            data = _load()
            data["availability"] = {"status": status, **details, "updated_at": _now()}
            _save(data)
            return dict(data["availability"])

    def set_autonomy(self, level: str) -> str:
        level = str(level).lower()
        if level not in _AUTONOMY:
            raise ValueError(f"Autonomy must be one of {_AUTONOMY}")
        with _lock:
            data = _load()
            data["autonomy"] = level
            data["updated_at"] = _now()
            _save(data)
        return level

    def set_preferences(self, values: dict) -> dict:
        with _lock:
            data = _load()
            current = data.get("preferences", {})
            current = dict(current) if isinstance(current, dict) else {}
            current.update(values or {})
            data["preferences"] = current
            data["updated_at"] = _now()
            _save(data)
            return dict(current)

    def preferences(self) -> dict:
        return dict(self.snapshot().get("preferences", {}))

    def register_external_capabilities(
        self, source: str, capabilities: list[str], *,
        device_id: str = "default", metadata: dict | None = None,
    ) -> dict:
        """Register capabilities exposed by a connected client such as VS Code."""
        source = str(source or "unknown").strip().lower() or "unknown"
        key = f"{source}:{device_id}"
        record = {
            "source": source,
            "device_id": str(device_id or "default"),
            "capabilities": sorted({str(item).strip() for item in capabilities if str(item).strip()}),
            "metadata": dict(metadata or {}),
            "connected": True,
            "updated_at": _now(),
        }
        with _lock:
            data = _load()
            data["external_capabilities"][key] = record
            data["updated_at"] = _now()
            _save(data)
        return dict(record)

    def record_verified_experience(self, *, operation: str, outcome: str,
                                   method: str = "", verified: bool = False,
                                   details: dict | None = None) -> dict:
        """Store bounded operational feedback, never personal conversation data."""
        if not verified:
            raise ValueError("Only verified experiences may be recorded")
        item = {
            "id": uuid.uuid4().hex,
            "operation": str(operation),
            "outcome": str(outcome),
            "method": str(method),
            "details": dict(details or {}),
            "created_at": _now(),
        }
        with _lock:
            data = _load()
            experiences = list(data.get("experiences", []))
            experiences.append(item)
            data["experiences"] = experiences[-200:]
            data["updated_at"] = _now()
            _save(data)
        return dict(item)

    def verified_experiences(self, operation: str = "", limit: int = 20) -> list[dict]:
        rows = self.snapshot().get("experiences", [])
        if operation:
            rows = [row for row in rows if row.get("operation") == operation]
        return list(reversed(rows[-max(1, min(int(limit), 200)):]))

    def save_goal(self, title: str, *, goal_id: str = "", status: str = "active",
                  priority: str = "normal", **fields) -> dict:
        title = str(title or "").strip()
        if not title:
            raise ValueError("Goal title is required")
        goal_id = goal_id or f"goal_{uuid.uuid4().hex[:12]}"
        with _lock:
            data = _load()
            current = dict(data["goals"].get(goal_id, {}))
            current.update({"id": goal_id, "title": title, "status": status,
                            "priority": priority, **fields})
            current.setdefault("created_at", _now())
            current["updated_at"] = _now()
            data["goals"][goal_id] = current
            _save(data)
            return dict(current)

    def goals(self, status: str = "") -> list[dict]:
        with _lock:
            rows = list(_load()["goals"].values())
        return [row for row in rows if not status or row.get("status") == status]

    def register_background_task(self, objective: str, *, task_id: str = "",
                                 resume_hook: Callable | None = None, **fields) -> dict:
        task_id = task_id or f"bg_{uuid.uuid4().hex[:12]}"
        with _lock:
            data = _load()
            task = dict(data["background_tasks"].get(task_id, {}))
            task.update({"task_id": task_id, "objective": str(objective),
                         "status": task.get("status", "queued"), **fields})
            task.setdefault("created_at", _now())
            task["updated_at"] = _now()
            # Hooks are process-local by design; the persisted task is resumable.
            if resume_hook:
                self._resume_hooks[task_id] = resume_hook
            data["background_tasks"][task_id] = task
            persisted = {k: v for k, v in task.items() if not k.startswith("_")}
            data["background_tasks"][task_id] = persisted
            _save(data)
            return dict(persisted)

    def update_background_task(self, task_id: str, status: str, **fields) -> dict | None:
        with _lock:
            data = _load()
            task = data["background_tasks"].get(task_id)
            if not task:
                return None
            task.update(fields, status=status, updated_at=_now())
            _save(data)
            return dict(task)

    def resume_background_tasks(self, resume: Callable[[dict], Any] | None = None) -> list[dict]:
        with _lock:
            rows = [dict(row) for row in _load()["background_tasks"].values()
                    if row.get("status") in ("queued", "running", "paused")]
        for row in rows:
            hook = self._resume_hooks.get(row["task_id"])
            if hook:
                try:
                    hook(row)
                except Exception as exc:
                    self.update_background_task(row["task_id"], "failed", error=str(exc))
        if resume:
            for row in rows:
                try:
                    resume(row)
                except Exception as exc:
                    self.update_background_task(row["task_id"], "failed", error=str(exc))
        return rows

    def classify_event(self, event: dict | str, *, context: str = "") -> dict:
        item = {"message": event} if isinstance(event, str) else dict(event or {})
        text = " ".join(str(item.get(key, "")) for key in
                        ("event_type", "title", "message", "source")).lower()
        urgent = any(word in text for word in ("urgent", "critical", "security", "alarm"))
        important = urgent or any(word in text for word in
                                  ("deadline", "failed", "error", "approval", "reminder"))
        relevance = 0.0
        terms = {word for word in str(context).lower().split() if len(word) > 2}
        if terms:
            relevance = min(1.0, sum(term in text for term in terms) / len(terms))
        elif text:
            relevance = 0.5
        return {
            **item, "category": str(item.get("category") or
            ("alert" if urgent else "productivity" if important else "informational")),
            "relevance": relevance,
            "priority": "URGENT" if urgent else "IMPORTANT" if important else
            str(item.get("priority", "NORMAL")).upper(),
        }

    def decide_event(self, event: dict | str, *, context: str = "") -> dict:
        classified = self.classify_event(event, context=context)
        availability = self.snapshot()["availability"]["status"]
        autonomy = self.snapshot()["autonomy"]
        priority = classified["priority"]
        if priority == "URGENT":
            decision = "notify" if availability != "do_not_disturb" else "queue"
        elif autonomy == "manual":
            decision = "queue"
        elif autonomy == "autonomous" and classified["relevance"] >= 0.25:
            decision = "act"
        elif classified["relevance"] >= 0.25 or priority == "IMPORTANT":
            decision = "notify" if availability == "available" else "queue"
        else:
            decision = "ignore"
        return {**classified, "decision": decision, "availability": availability,
                "autonomy": autonomy}

    def diagnostics(self) -> dict:
        state = self.snapshot()
        return {
            "ok": bool(state.get("version") and state.get("components")),
            "state_path": str(self.path), "writable": os.access(self.path.parent, os.W_OK),
            "availability": state["availability"], "autonomy": state["autonomy"],
            "component_counts": {key: len(value) for key, value in state["components"].items()},
            "background_tasks": len(state["background_tasks"]),
            "goals": len(state["goals"]),
            "verified_experiences": len(state.get("experiences", [])),
        }

    def recover(self, *, health: dict | None = None) -> dict:
        with _lock:
            data = _load()
            data["recovery"] = {"last_start": _now(), "resumed_tasks": [
                key for key, row in data["background_tasks"].items()
                if row.get("status") in ("queued", "running", "paused")
            ]}
            if health is not None:
                data["health"] = health
            _save(data)
            return dict(data["recovery"])


runtime_state = RuntimeState()

# Stable functional API for integrations that do not need to retain the
# facade object (and for the main process entry point).
get_runtime_state = runtime_state.snapshot
classify_event = runtime_state.classify_event
decide_event = runtime_state.decide_event
