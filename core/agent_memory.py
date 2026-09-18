"""Strictly namespaced operational memory for specialist agents.

This store is intentionally separate from JARVIS personal memory.  An agent
can only read and write its own namespace; JARVIS may explicitly copy selected
facts into a handoff context.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_PATH = BASE_DIR / "memory" / "agent_memory.json"
_lock = threading.RLock()
_MAX_ENTRIES = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, list[dict]]:
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = MEMORY_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(MEMORY_PATH)


class AgentMemory:
    """Scoped memory handle. It cannot retrieve another agent's namespace."""

    def __init__(self, agent_id: str):
        self.agent_id = str(agent_id or "").strip().lower()
        if not self.agent_id:
            raise ValueError("agent_id is required")

    def remember(
        self,
        content: Any,
        *,
        memory_type: str = "operational",
        importance: float = 0.5,
        confidence: float = 1.0,
        source: str = "agent",
        project_id: str = "",
        task_id: str = "",
    ) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "agent_id": self.agent_id,
            "memory_type": str(memory_type),
            "content": content,
            "importance": max(0.0, min(1.0, float(importance))),
            "confidence": max(0.0, min(1.0, float(confidence))),
            "created_at": _now(),
            "updated_at": _now(),
            "source": str(source),
            "project_id": str(project_id or ""),
            "task_id": str(task_id or ""),
        }
        with _lock:
            data = _load()
            rows = list(data.get(self.agent_id, []))
            rows.append(entry)
            data[self.agent_id] = rows[-_MAX_ENTRIES:]
            _save(data)
        return dict(entry)

    def retrieve(self, query: str = "", *, project_id: str = "",
                 task_id: str = "", limit: int = 20) -> list[dict]:
        query_terms = {part.lower() for part in str(query).split() if part.strip()}
        with _lock:
            rows = list(_load().get(self.agent_id, []))
        result = []
        for row in reversed(rows):
            if project_id and row.get("project_id") not in ("", project_id):
                continue
            if task_id and row.get("task_id") not in ("", task_id):
                continue
            haystack = json.dumps(row.get("content", ""), ensure_ascii=False).lower()
            if query_terms and not any(term in haystack for term in query_terms):
                continue
            result.append(dict(row))
            if len(result) >= max(1, min(int(limit), 100)):
                break
        return result

    def snapshot(self) -> dict:
        with _lock:
            return {"agent_id": self.agent_id, "entries": len(_load().get(self.agent_id, []))}
