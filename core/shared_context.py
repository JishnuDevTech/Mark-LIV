"""Explicit, bounded context bus controlled by JARVIS."""
from __future__ import annotations

from typing import Any


def build_handoff(
    *,
    agent_id: str,
    task_id: str = "",
    project_id: str = "",
    task: dict | None = None,
    project: dict | None = None,
    relevant_memory: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    other_agent_results: list[dict] | None = None,
    constraints: list[str] | None = None,
    user_instructions: str = "",
) -> dict[str, Any]:
    """Return only the fields JARVIS deliberately provides to one agent."""
    return {
        "agent_id": str(agent_id),
        "task_id": str(task_id),
        "project_id": str(project_id),
        "task": dict(task or {}),
        "project": dict(project or {}),
        "relevant_memory": list(relevant_memory or [])[:20],
        "tool_results": list(tool_results or [])[-20:],
        "other_agent_results": list(other_agent_results or [])[-10:],
        "constraints": [str(item) for item in (constraints or [])][:20],
        "user_instructions": str(user_instructions or "")[:12000],
    }


def result(
    *,
    agent: str,
    task: str,
    status: str,
    summary: str = "",
    files_changed: list[str] | None = None,
    actions_taken: list[str] | None = None,
    errors: list[str] | None = None,
    verification: list[str] | None = None,
    recommendations: list[str] | None = None,
    **extra,
) -> dict:
    payload = {
        "agent": str(agent),
        "task": str(task),
        "status": str(status),
        "summary": str(summary),
        "files_changed": list(files_changed or []),
        "actions_taken": list(actions_taken or []),
        "errors": list(errors or []),
        "verification": list(verification or []),
        "recommendations": list(recommendations or []),
        **extra,
    }
    payload.setdefault("ok", payload["status"] not in {"failed", "error"})
    return payload
