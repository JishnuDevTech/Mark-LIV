"""Specialist agent contracts and lifecycle management.

Specialists are deliberately thin adapters over the existing capability
router, context store, event engine, plugin manager, and mobile manager.  This
module does not introduce another tool, task, permission, or memory store.
"""
from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from core import context_manager, event_engine
from core.agent_memory import AgentMemory
from core.shared_context import build_handoff, result as structured_result


DEFAULT_AGENT_VOICES = {
    # These are deliberately separate from JARVIS's configured voice.
    "FRIDAY": {"engine": "edgetts", "voice": "en-US-AriaNeural", "speed": 1.02},
    "ULTRON": {"engine": "edgetts", "voice": "en-US-GuyNeural", "speed": 0.96},
    "MESSENGER": {"engine": "edgetts", "voice": "en-GB-RyanNeural", "speed": 1.0},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SpecialistAgent:
    """The shared contract implemented by every specialist."""

    name: str
    description: str
    capabilities: tuple[str, ...]
    router: Any
    context: Any = context_manager
    events: Any = event_engine
    mobile: Any = None
    provider_fallback: Callable[..., Any] | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    memory: AgentMemory = field(init=False)

    def __post_init__(self):
        self.memory = AgentMemory(self.name)

    def status(self) -> dict:
        native = set(self.metadata.get("native_capabilities", []))
        if "plugins" in self.capabilities:
            plugin_available = any(
                item.get("source") == "plugin" and item.get("available")
                for item in self.router.capabilities.snapshot()
            )
            if plugin_available:
                native.add("plugins")
        available = [
            name for name in self.capabilities
            if self.router.capabilities.has(name) or name in native
        ]
        voice = dict(DEFAULT_AGENT_VOICES.get(self.name, DEFAULT_AGENT_VOICES["MESSENGER"]))
        try:
            from core.runtime_state import runtime_state
            persisted = runtime_state.preferences().get("agent_voices", {}).get(self.name, {})
            if isinstance(persisted, dict):
                voice.update(persisted)
        except Exception:
            pass
        voice.update(self.metadata.get("voice_config") or {})
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "healthy": bool(self.enabled),
            "available": bool(available),
            "capabilities": list(self.capabilities),
            "available_capabilities": available,
            "missing_capabilities": [name for name in self.capabilities if name not in available],
            "metadata": dict(self.metadata),
            "memory": self.memory.snapshot(),
            "voice": self.metadata.get("voice", self.name.lower()),
            "voice_config": voice,
        }

    def health(self) -> dict:
        status = self.status()
        status["health"] = "ok" if status["healthy"] else "disabled"
        return status

    def context_handoff(self, task_id: str = "", project_id: str = "") -> dict:
        state = self.context.get_state(project_id or None)
        resolved_project = project_id or state.get("active_project", "")
        return build_handoff(
            agent_id=self.name,
            task_id=task_id,
            project_id=resolved_project,
            task=state.get("active_task", {}),
            project=state.get("projects", {}).get(resolved_project, {}),
            relevant_memory=self.memory.retrieve(project_id=resolved_project),
        )

    async def execute(self, task: dict, *, project_id: str = "",
                      task_id: str = "", **router_context) -> dict:
        if not self.enabled:
            return structured_result(agent=self.name, task=task.get("objective", ""),
                                    status="failed", errors=["Specialist is disabled."])
        tool = str(task.get("tool") or "").strip()
        parameters = dict(task.get("parameters") or {})
        if not tool:
            return structured_result(agent=self.name, task=task.get("objective", ""),
                                    status="failed", errors=["A specialist tool is required."])
        if not self.router.capabilities.has(tool):
            if self.name == "MESSENGER" and tool in {"event_summary", "recent_events"}:
                limit = max(1, min(int(parameters.get("limit", 10)), 50))
                return structured_result(
                    agent=self.name, task=task.get("objective", ""),
                    status="completed", summary="Returned recent events.",
                    actions_taken=[tool], verification=["event store read completed"],
                    result={"events": self.events.recent(
                        limit, str(parameters.get("minimum", "LOW"))
                    )}, tool=tool,
                )
            if self.provider_fallback:
                fallback = self.provider_fallback(self, task, **router_context)
                if inspect.isawaitable(fallback):
                    fallback = await fallback
                if fallback is not None:
                    return fallback
            return structured_result(agent=self.name, task=task.get("objective", ""),
                                    status="failed",
                                    errors=[f"Capability '{tool}' is unavailable."])
        result = await self.router.execute(tool, parameters, **router_context)
        if project_id:
            self.context.record_event(project_id, "specialist_result", str(result)[:2000],
                                      agent=self.name, task_id=task_id, tool=tool)
        ok = result.get("ok", True) is not False
        return structured_result(
            agent=self.name, task=task.get("objective", ""), status="completed" if ok else "failed",
            summary=str(result.get("message") or result.get("result") or "")[:2000],
            actions_taken=[tool], errors=[] if ok else [str(result.get("error", "Tool failed"))],
            verification=["tool returned success"] if ok else [],
            tool=tool, result=result,
        )


class AgentManager:
    """Dynamic registry, health surface, context handoff, and assignment API."""

    def __init__(self, router, *, context=context_manager, events=event_engine,
                 plugin_manager=None, mobile_manager=None, logger=print,
                 provider_fallback=None):
        self.router = router
        self.context = context
        self.events = events
        self.plugin_manager = plugin_manager
        self.mobile_manager = mobile_manager
        self.logger = logger
        self.provider_fallback = provider_fallback
        self._agents: dict[str, SpecialistAgent] = {}
        self._tasks: dict[str, dict] = {}
        self.discover()

    def register(self, agent: SpecialistAgent) -> SpecialistAgent:
        agent.name = agent.name.upper()
        agent.provider_fallback = agent.provider_fallback or self.provider_fallback
        self._agents[agent.name] = agent
        return agent

    def set_provider_fallback(self, provider_fallback: Callable[..., Any] | None) -> None:
        """Install an optional model-router hook without making it mandatory."""
        self.provider_fallback = provider_fallback
        for agent in self._agents.values():
            agent.provider_fallback = provider_fallback

    def discover(self) -> dict[str, SpecialistAgent]:
        """Register the built-ins against the current shared router."""
        self.register(SpecialistAgent(
            "FRIDAY", "Productivity, plugins, and mobile notifications.",
            ("productivity", "plugins", "reminder", "send_mobile_notification"), self.router,
            context=self.context, events=self.events, mobile=self.mobile_manager,
            metadata={"role": "services and productivity", "voice": "friday"},
        ))
        self.register(SpecialistAgent(
            "ULTRON", "Repository and code analysis, builds, and tests.",
            ("project_access", "file_processor", "code_helper", "dev_agent"),
            self.router, context=self.context, events=self.events,
            metadata={"role": "coding and engineering", "voice": "ultron"},
        ))
        self.register(SpecialistAgent(
            "MESSENGER", "Event summaries and authenticated mobile notifications.",
            ("event_summary", "send_mobile_notification", "send_mobile_message",
             "get_mobile_status"),
            self.router, context=self.context, events=self.events, mobile=self.mobile_manager,
            metadata={"native_capabilities": ["event_summary"],
                      "role": "communication", "voice": "messenger"},
        ))
        return dict(self._agents)

    # Explicit aliases keep the contract convenient for UI and orchestration
    # callers without introducing another execution path.
    discover_agents = discover
    register_agent = register

    def get(self, name: str) -> SpecialistAgent | None:
        return self._agents.get(str(name or "").upper())

    def list(self) -> list[dict]:
        return [agent.status() for agent in self._agents.values()]

    def tasks(self, limit: int = 50) -> list[dict]:
        """Return the live task registry without introducing another store."""
        rows = sorted(self._tasks.values(), key=lambda row: row.get("updated_at", ""), reverse=True)
        return [dict(row) for row in rows[:max(1, min(int(limit), 200))]]

    def communications(self, limit: int = 50) -> list[dict]:
        """Project the existing event stream into an agent communication feed."""
        names = set(self._agents)
        return [
            event for event in self.events.recent(max(1, min(int(limit) * 3, 200)))
            if str(event.get("source", "")).upper() in names
            or str(event.get("event_type", "")).startswith("specialist_")
        ][:max(1, min(int(limit), 200))]

    def set_voice_config(self, agent: str, config: dict) -> dict:
        specialist = self.get(agent)
        if specialist is None:
            raise ValueError(f"Specialist '{agent}' is not registered.")
        allowed = {"engine", "voice", "speed"}
        current = dict(DEFAULT_AGENT_VOICES.get(specialist.name, DEFAULT_AGENT_VOICES["MESSENGER"]))
        current.update({key: value for key, value in dict(config or {}).items() if key in allowed})
        current["speed"] = max(0.5, min(float(current["speed"]), 2.0))
        specialist.metadata["voice_config"] = current
        try:
            from core.runtime_state import runtime_state
            voices = runtime_state.preferences().get("agent_voices", {})
            voices = dict(voices) if isinstance(voices, dict) else {}
            voices[specialist.name] = current
            runtime_state.set_preferences({"agent_voices": voices})
        except Exception:
            # Voice still applies for this process if the optional persistence
            # layer is unavailable.
            pass
        return specialist.status()["voice_config"]

    def status(self, name: str | None = None) -> dict | list[dict]:
        if name:
            agent = self.get(name)
            return agent.status() if agent else {
                "name": str(name).upper(), "healthy": False,
                "error": "Specialist is not registered.",
            }
        return self.list()

    def health(self) -> dict:
        return {"ok": all(row["healthy"] for row in self.list()),
                "agents": [agent.health() for agent in self._agents.values()]}

    def handoff(self, agent: str, task_id: str = "", project_id: str = "") -> dict:
        specialist = self.get(agent)
        if specialist is None:
            return {"ok": False, "error": f"Specialist '{agent}' is not registered."}
        return {"ok": True, "handoff": specialist.context_handoff(task_id, project_id)}

    async def assign(self, agent: str, task: dict | None = None, *,
                     project_id: str = "", task_id: str = "", **router_context) -> dict:
        specialist = self.get(agent)
        if specialist is None:
            return {"ok": False, "error": f"Specialist '{agent}' is not registered."}
        task_id = task_id or f"specialist_{uuid.uuid4().hex[:12]}"
        task = dict(task or {})
        self._tasks[task_id] = {"task_id": task_id, "agent": specialist.name,
                                "status": "running", "created_at": _now(), "task": task}
        self.context.start_task(task_id, str(task.get("objective", "")), project_id)
        self.events.publish(
            "specialist_task_started",
            f"{specialist.name} started specialist task.",
            source=specialist.name,
            task_id=task_id,
            project_id=project_id,
        )
        result = await specialist.execute(task, project_id=project_id, task_id=task_id,
                                          **router_context)
        status = "completed" if result.get("ok") else "failed"
        self._tasks[task_id].update({"status": status, "result": result, "updated_at": _now()})
        self.context.update_task(status=self._tasks[task_id]["status"],
                                 intermediate_results=[str(result)[:1200]])
        self.events.publish(
            "specialist_task_completed" if status == "completed" else "specialist_task_failed",
            f"{specialist.name} {status} specialist task.",
            source=specialist.name,
            importance="NORMAL" if status == "completed" else "IMPORTANT",
            task_id=task_id,
            project_id=project_id,
        )
        if status == "completed" and hasattr(self.context, "record_verified_experience"):
            try:
                from core.runtime_state import runtime_state
                runtime_state.record_verified_experience(
                    operation=f"specialist:{specialist.name}",
                    outcome="completed",
                    method=str(task.get("tool", "")),
                    verified=True,
                    details={"task_id": task_id},
                )
            except Exception as exc:
                self.logger(f"[Agents] Could not record verified experience: {exc}")
        if status == "completed":
            specialist.memory.remember(
                {"summary": str(result.get("summary", ""))[:2000],
                 "tool": str(result.get("tool", ""))},
                memory_type="task_result",
                importance=0.5,
                confidence=1.0,
                source="verified_task",
                project_id=project_id,
                task_id=task_id,
            )
        return {**result, "task_id": task_id}

    assign_task = assign
    execute_task = assign

    def task_status(self, task_id: str) -> dict:
        return self._tasks.get(task_id, {"ok": False, "error": "Specialist task not found."})
