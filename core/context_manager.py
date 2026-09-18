"""JARVIS-owned persistent context for projects, tasks, and model handoffs."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "memory" / "jarvis_state.json"
_MAX_EVENTS = 80
_MAX_TEXT = 12000
_ACTIVE_MESSAGES = 24
_TASK_EVENTS = 40
_RETRIEVAL_DEFAULT = 12
_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {
        "version": 2,
        "active_project": "",
        "active_task": {},
        "active_conversation": {"messages": [], "summary": ""},
        "projects": {},
        # Kept for compatibility with the first state format.
        "conversation": [],
    }


def _load() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _empty()
            base.update(data)
            if not isinstance(base.get("projects"), dict):
                base["projects"] = {}
            if not isinstance(base.get("conversation"), list):
                base["conversation"] = []
            active = base.get("active_conversation")
            if not isinstance(active, dict):
                active = {"messages": [], "summary": ""}
            if not isinstance(active.get("messages"), list):
                active["messages"] = []
            base["active_conversation"] = active
            if not isinstance(base.get("active_task"), dict):
                base["active_task"] = {}
            return base
    except Exception:
        pass
    return _empty()


def _save(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def _project(project_id: str, data: dict) -> dict:
    projects = data.setdefault("projects", {})
    project = projects.setdefault(project_id, {
        "project_id": project_id,
        "goal": "",
        "technology_stack": [],
        "requirements": [],
        "design_decisions": [],
        "completed_work": [],
        "unfinished_work": [],
        "relevant_files": [],
        "errors": [],
        "pending_changes": [],
        "current_task": "",
        "conversation_context": [],
        "events": [],
        "updated": _now(),
    })
    return project


def _append_unique(items: list, values) -> None:
    if isinstance(values, str):
        values = [values]
    for value in values or []:
        value = str(value).strip()
        if value and value not in items:
            items.append(value)


def start_project(project_id: str, goal: str = "") -> dict:
    project_id = str(project_id or "default").strip() or "default"
    with _lock:
        data = _load()
        project = _project(project_id, data)
        if goal and not project["goal"]:
            project["goal"] = goal.strip()
        project["updated"] = _now()
        data["active_project"] = project_id
        _save(data)
        return dict(project)


def start_task(task_id: str, objective: str, project_id: str = "") -> dict:
    """Start or resume the JARVIS-owned task currently being worked on."""
    with _lock:
        data = _load()
        task = data.setdefault("active_task", {})
        task.update({
            "task_id": str(task_id or "task").strip() or "task",
            "objective": str(objective or "").strip(),
            "project_id": str(project_id or data.get("active_project", "")),
            "status": "active",
            "current_step": task.get("current_step", ""),
            "completed_work": task.get("completed_work", []),
            "unfinished_work": task.get("unfinished_work", []),
            "recent_decisions": task.get("recent_decisions", []),
            "intermediate_results": task.get("intermediate_results", []),
            "errors": task.get("errors", []),
            "updated": _now(),
        })
        if project_id:
            data["active_project"] = project_id
        _save(data)
        return dict(task)


def update_task(**updates) -> dict:
    """Persist bounded working memory without turning it into chat history."""
    with _lock:
        data = _load()
        task = data.setdefault("active_task", {})
        for key in ("task_id", "objective", "project_id", "status", "current_step"):
            if updates.get(key) is not None and str(updates[key]).strip():
                task[key] = str(updates[key]).strip()
        for key in ("completed_work", "unfinished_work", "recent_decisions",
                "intermediate_results", "errors"):
            if key in updates:
                values = task.setdefault(key, [])
                _append_unique(values, updates[key])
                task[key] = values[-_TASK_EVENTS:]
        task["updated"] = _now()
        _save(data)
        return dict(task)


def update_project(project_id: str, **updates) -> dict:
    """Merge model/task facts into one canonical JARVIS project state."""
    project_id = str(project_id or "default").strip() or "default"
    with _lock:
        data = _load()
        project = _project(project_id, data)
        for key in ("technology_stack", "requirements", "design_decisions",
                    "completed_work", "unfinished_work", "relevant_files"):
            if key in updates:
                _append_unique(project.setdefault(key, []), updates[key])
        goal = str(updates.get("goal") or "").strip()
        if goal:
            current_goal = str(project.get("goal") or "").strip()
            if current_goal and current_goal.lower() != goal.lower():
                project.setdefault("pending_changes", []).append({
                    "type": "goal_change",
                    "from": current_goal,
                    "to": goal,
                    "updated": _now(),
                })
                project["pending_changes"] = project["pending_changes"][-10:]
            else:
                project["goal"] = goal
        current_task = str(updates.get("current_task") or "").strip()
        if current_task:
            project["current_task"] = current_task
        if updates.get("errors"):
            _append_unique(project.setdefault("errors", []), updates["errors"])
        if updates.get("conversation_context"):
            _append_unique(project.setdefault("conversation_context", []), updates["conversation_context"])
        project["updated"] = _now()
        data["active_project"] = project_id
        _save(data)
        return dict(project)


def confirm_project_goal(project_id: str, goal: str) -> dict:
    """Apply a pending goal change only after explicit user confirmation."""
    project_id = str(project_id or "default").strip() or "default"
    goal = str(goal or "").strip()
    if not goal:
        return get_state(project_id)
    with _lock:
        data = _load()
        project = _project(project_id, data)
        project["goal"] = goal
        project["pending_changes"] = [
            change for change in project.get("pending_changes", [])
            if change.get("to") != goal
        ]
        project["updated"] = _now()
        data["active_project"] = project_id
        _save(data)
        return dict(project)


def record_event(project_id: str, kind: str, detail: str, **fields) -> None:
    detail = str(detail or "").strip()
    if not detail:
        return
    with _lock:
        data = _load()
        project = _project(project_id, data)
        event = {"time": _now(), "kind": str(kind), "detail": detail[:_MAX_TEXT]}
        event.update({k: v for k, v in fields.items() if v is not None})
        project.setdefault("events", []).append(event)
        project["events"] = project["events"][-_MAX_EVENTS:]
        project["updated"] = event["time"]
        data["active_project"] = project_id
        _save(data)


def record_conversation(text: str, role: str = "user", project_id: str = "") -> None:
    text = str(text or "").strip()
    if not text:
        return
    with _lock:
        data = _load()
        item = {"time": _now(), "role": role, "text": text[:_MAX_TEXT]}
        data.setdefault("conversation", []).append(item)
        data["conversation"] = data["conversation"][-_MAX_EVENTS:]
        active = data.setdefault("active_conversation", {"messages": [], "summary": ""})
        active.setdefault("messages", []).append(item)
        active["messages"] = active["messages"][-_ACTIVE_MESSAGES:]
        if project_id:
            project = _project(project_id, data)
            project.setdefault("conversation_context", []).append(f"{role}: {text[:2000]}")
            project["conversation_context"] = project["conversation_context"][-20:]
            project["updated"] = item["time"]
            data["active_project"] = project_id
        _save(data)


def _terms(text: str) -> set[str]:
    return {word for word in str(text or "").lower().split() if len(word) > 2}


def _relevance(request: str, text: str) -> int:
    words = _terms(request)
    if not words:
        return 1
    haystack = str(text or "").lower()
    return sum(1 for word in words if word in haystack)


def _personal_context(request: str, limit: int) -> list[dict]:
    """Retrieve personal facts through the existing personal-memory manager."""
    try:
        from memory.memory_manager import load_memory
        memory = load_memory()
    except Exception:
        return []
    rows = []
    for category, values in memory.items():
        if not isinstance(values, dict):
            continue
        for key, entry in values.items():
            value = entry.get("value", "") if isinstance(entry, dict) else entry
            score = _relevance(request, f"{category} {key} {value}")
            if score:
                rows.append((score, {"category": category, "key": key, "value": str(value)}))
    rows.sort(key=lambda row: (-row[0], row[1]["key"]))
    return [row[1] for row in rows[:limit]]


def retrieve_context(request: str = "", project_id: str = "", limit: int = _RETRIEVAL_DEFAULT,
                     include_personal: bool | None = None) -> dict:
    """Build task-specific context from the four JARVIS memory tiers.

    Empty or weather-like requests intentionally omit project/task state. A
    project request includes only matching project facts, working memory, and
    the most relevant recent conversation. Personal facts are opt-in for task
    contexts and remain separate from project state.
    """
    request_low = str(request or "").lower()
    project_words = ("project", "build", "website", "code", "app", "file",
                     "architecture", "design", "implement", "continue")
    productivity_words = ("task", "work", "deadline", "today", "schedule",
                          "calendar", "email", "inbox", "remind", "priority")
    task_request = (any(word in request_low for word in project_words)
                    or any(word in request_low for word in productivity_words)
                    or bool(project_id))
    if include_personal is None:
        include_personal = task_request
    with _lock:
        data = _load()
        active_project = project_id or data.get("active_project", "")
        project = data.get("projects", {}).get(active_project, {}) if active_project else {}
        task = data.get("active_task", {}) if task_request else {}
        messages = data.get("active_conversation", {}).get("messages", [])
        relevant_messages = [
            message for message in messages
            if _relevance(request, message.get("text", ""))
        ][-limit:] if request else messages[-limit:]
    package = {
        "active_conversation": {
            "messages": relevant_messages,
            "summary": data.get("active_conversation", {}).get("summary", ""),
        },
        "working_task": task,
    }
    if task_request and project:
        package["project_state"] = project
        package["active_project"] = active_project
    if include_personal:
        package["relevant_personal_memory"] = _personal_context(request, limit)
    if task_request:
        try:
            from core import productivity_store
            request_domains = ("task", "work", "deadline", "today", "schedule",
                               "calendar", "email", "inbox", "remind", "priority")
            if any(word in request_low for word in request_domains):
                package["productivity"] = {
                    "tasks": productivity_store.list_tasks()[:limit],
                    "schedule": productivity_store.list_events()[:limit],
                    "inbox": productivity_store.list_inbox()[:limit],
                }
        except Exception:
            pass
        # Goals are owned by the runtime facade and are included only for
        # work-related requests, keeping ordinary conversation lightweight.
        try:
            from core.runtime_state import runtime_state
            goals = runtime_state.goals("active")
            if goals:
                package["active_goals"] = goals[:limit]
            runtime = runtime_state.snapshot()
            package["runtime"] = {
                "availability": runtime.get("availability", {}),
                "autonomy": runtime.get("autonomy", "suggest"),
                "health": runtime.get("health", {}),
            }
        except Exception:
            pass
    return package


def get_state(project_id: str = "") -> dict:
    with _lock:
        data = _load()
        project_id = project_id or data.get("active_project", "")
        return dict(_project(project_id, data)) if project_id else data


def context_package(project_id: str = "", request: str = "", max_chars: int = 18000) -> str:
    """Return the handoff context every reasoning provider receives."""
    package = retrieve_context(request=request, project_id=project_id, include_personal=True)
    raw = json.dumps(package, indent=2, ensure_ascii=False)
    return raw if len(raw) <= max_chars else raw[:max_chars] + "\n[context truncated by JARVIS]"
