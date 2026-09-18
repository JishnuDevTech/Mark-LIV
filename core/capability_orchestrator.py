"""Central capability discovery, routing, and task orchestration.

This module composes the existing action, plugin, and mobile registries.  It
does not own or duplicate those registries; it only provides one stable view
for planning and a small execution state machine for multi-step work.
"""
from __future__ import annotations

import inspect
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from core import context_manager
from core.shared_context import build_handoff, result as structured_result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Capability:
    name: str
    source: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    available: bool = True
    scheduling: str | None = None


class DynamicCapabilityRegistry:
    """A single, refreshed view over the application's existing registries."""

    def __init__(self, logger: Callable[[str], None] = print):
        self._capabilities: dict[str, Capability] = {}
        self._sources: dict[str, Any] = {}
        self._logger = logger

    def register(self, name: str, source: str, description: str = "",
                 parameters: dict | None = None, available: bool = True,
                 scheduling: str | None = None, registry: Any = None) -> Capability:
        capability = Capability(
            name=str(name), source=str(source), description=str(description),
            parameters=dict(parameters or {}), available=bool(available),
            scheduling=scheduling,
        )
        self._capabilities[capability.name] = capability
        if registry is not None:
            self._sources[capability.name] = registry
        return capability

    def register_declarations(self, declarations: list[dict], source: str,
                              registry: Any = None) -> None:
        for declaration in declarations or []:
            if isinstance(declaration, dict) and declaration.get("name"):
                self.register(
                    declaration["name"], source,
                    declaration.get("description", ""),
                    declaration.get("parameters", {}),
                    registry=registry,
                )

    def refresh(self, action_registry=None, plugin_manager=None,
                mobile_manager=None) -> "DynamicCapabilityRegistry":
        self._capabilities.clear()
        self._sources.clear()
        if action_registry is not None:
            self.register_declarations(
                action_registry.get_tool_declarations(), "action", action_registry,
            )
        if plugin_manager is not None:
            self.register_declarations(
                plugin_manager.tool_declarations(), "plugin", plugin_manager,
            )
        if mobile_manager is not None:
            self.register_declarations(
                mobile_manager.declarations(), "mobile", mobile_manager,
            )
        return self

    def has(self, name: str) -> bool:
        return str(name) in self._capabilities

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(str(name))

    def snapshot(self) -> list[dict]:
        return [asdict(item) for item in self._capabilities.values()]

    def declarations(self) -> list[dict]:
        return [
            {"name": item.name, "description": item.description,
             "parameters": item.parameters}
            for item in self._capabilities.values() if item.available
        ]


# Short name for callers that do not need to distinguish dynamic discovery
# from the underlying provider registries.
CapabilityRegistry = DynamicCapabilityRegistry


class ToolRouter:
    """Dispatches through the already-existing action/plugin/mobile managers."""

    def __init__(self, capabilities: DynamicCapabilityRegistry,
                 logger: Callable[[str], None] = print):
        self.capabilities = capabilities
        self._logger = logger

    async def execute(self, name: str, parameters: dict | None = None, **context) -> Any:
        capability = self.capabilities.get(name)
        if capability is None:
            return {"ok": False, "error": f"Capability '{name}' is not available."}
        registry = self.capabilities._sources.get(name)
        parameters = parameters or {}
        try:
            if capability.source == "action":
                result = registry.run(name, parameters, context)
                return {"ok": True, "tool": name, "result": result}
            if capability.source == "plugin":
                result = registry.execute(name, parameters, **{
                    key: context[key] for key in ("player", "session_memory")
                    if key in context
                })
                return await result if inspect.isawaitable(result) else result
            if capability.source == "mobile":
                result = registry.execute(name, parameters)
                return await result if inspect.isawaitable(result) else result
            return {"ok": False, "error": f"Unknown capability source: {capability.source}"}
        except Exception as exc:
            self._logger(f"[Router] {name} failed: {exc}")
            return {"ok": False, "tool": name, "error": str(exc)}

    run = execute


class TaskOrchestrator:
    """Plan, execute, verify, and persist bounded multi-step tasks."""

    STATES = ("planned", "running", "verified", "completed", "failed")

    def __init__(self, router: ToolRouter, logger: Callable[[str], None] = print,
                 verifier: Callable[[dict, Any], bool] | None = None,
                 runtime=None):
        self.router = router
        self._logger = logger
        self._verifier = verifier or self._default_verify
        self._runtime = runtime

    @staticmethod
    def _default_verify(step: dict, result: Any) -> bool:
        if isinstance(result, dict):
            return result.get("ok") is not False
        if isinstance(result, str):
            lowered = result.strip().lower()
            return not (lowered.startswith("tool ") and " failed" in lowered)
        return True

    def plan(self, objective: str, steps: list[dict] | None = None,
             project_id: str = "", task_id: str = "") -> dict:
        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        normalized = []
        for index, step in enumerate(steps or [], 1):
            if not isinstance(step, dict) or not step.get("tool"):
                continue
            normalized.append({
                "id": str(step.get("id") or f"step_{index}"),
                "tool": str(step["tool"]),
                "parameters": dict(step.get("parameters") or {}),
                "status": "planned",
            })
        state = {
            "task_id": task_id, "objective": str(objective or "").strip(),
            "project_id": project_id, "status": "planned", "steps": normalized,
            "created_at": _now(), "updated_at": _now(), "results": [], "errors": [],
        }
        context_manager.start_task(task_id, state["objective"], project_id)
        context_manager.update_task(
            status="planned", unfinished_work=[step["id"] for step in normalized],
            recent_decisions=[json.dumps({"steps": len(normalized)})],
        )
        if project_id:
            context_manager.record_event(project_id, "task_plan", state["objective"],
                                         task_id=task_id, step_count=len(normalized))
        if self._runtime:
            self._runtime.register_background_task(
                state["objective"], task_id=task_id, status="queued",
                project_id=project_id, steps=[step["id"] for step in normalized],
            )
        return state

    async def execute(self, state: dict, **context) -> dict:
        state["status"] = "running"
        state["updated_at"] = _now()
        if self._runtime:
            self._runtime.update_background_task(state["task_id"], "running")
        for step in state.get("steps", []):
            if step.get("status") == "completed":
                continue
            step["status"] = "running"
            context_manager.update_task(status="running", current_step=step["id"])
            result = await self.router.execute(step["tool"], step.get("parameters"), **context)
            state.setdefault("results", []).append({"step": step["id"], "result": result})
            if not self._verifier(step, result):
                step["status"] = "failed"
                state["status"] = "failed"
                state.setdefault("errors", []).append({"step": step["id"], "result": result})
                context_manager.update_task(status="failed", errors=[str(result)])
                if self._runtime:
                    self._runtime.update_background_task(
                        state["task_id"], "failed", error=str(result),
                    )
                return state
            step["status"] = "verified"
            context_manager.update_task(status="verified", current_step=step["id"])
            context_manager.update_task(
                completed_work=[step["id"]], unfinished_work=[
                    item["id"] for item in state["steps"]
                    if item.get("status") not in ("verified", "completed")
                ], intermediate_results=[str(result)[:1200]],
            )
            step["status"] = "completed"
        state["status"] = "completed"
        state["updated_at"] = _now()
        context_manager.update_task(status="completed", current_step="")
        if self._runtime:
            self._runtime.update_background_task(state["task_id"], "completed")
        return state

    def decompose(self, objective: str, tools: list[str] | None = None,
                  project_id: str = "") -> dict:
        """Create a safe plan skeleton; model-specific decomposition is optional."""
        steps = [{"tool": tool, "parameters": {}} for tool in (tools or [])]
        return self.plan(objective, steps, project_id=project_id)


class BossCoordinator:
    """JARVIS-owned delegation boundary for multi-specialist objectives."""

    def __init__(self, agents, events, runtime=None, logger: Callable[[str], None] = print):
        self.agents = agents
        self.events = events
        # RuntimeState is the existing task store.  Keep accepting an injected
        # facade for tests and integrations, but never create a second store.
        if runtime is None:
            from core.runtime_state import runtime_state
            runtime = runtime_state
        self.runtime = runtime
        self.logger = logger

    @staticmethod
    def _route(objective: str) -> list[str]:
        text = str(objective).lower()
        routes: list[str] = []
        if any(word in text for word in (
            "code", "file", "bug", "build", "test", "repository", "vscode",
            "implement", "debug", "function",
        )):
            routes.append("ULTRON")
        if any(word in text for word in (
            "email", "calendar", "notion", "reminder", "remind", "reminders",
            "schedule", "task",
            "plugin", "productivity",
        )):
            routes.append("FRIDAY")
        if any(word in text for word in (
            "message", "notify", "tell", "send", "communication",
        )):
            routes.append("MESSENGER")
        # An ambiguous objective must not silently become a coding task.
        return routes

    def _select_tool(self, agent_name: str, objective: str,
                     requested: str = "") -> str:
        """Choose one registered capability deterministically for a child."""
        specialist = self.agents.get(agent_name)
        if specialist is None:
            return ""
        available = set(specialist.status().get("available_capabilities", []))
        if requested:
            return requested if requested in available else ""
        if not available:
            return ""
        text = objective.lower()
        # Prefer concrete capabilities whose names occur in the objective, then
        # use stable role-specific ordering rather than registry iteration order.
        preferred = {
            "ULTRON": ("dev_agent", "code_helper", "file_processor", "project_access"),
            "FRIDAY": ("productivity", "plugins", "reminder", "send_mobile_notification"),
            "MESSENGER": ("send_mobile_message", "send_mobile_notification",
                          "event_summary", "get_mobile_status"),
        }.get(agent_name, ())
        for capability in preferred:
            if capability in available and (capability.replace("_", " ") in text
                                            or capability in text):
                return capability
        return next((capability for capability in preferred if capability in available),
                    sorted(available)[0])

    def _persist(self, objective: str, *, task_id: str, status: str, **fields) -> None:
        self.runtime.register_background_task(
            objective, task_id=task_id, status=status, owner="JARVIS", **fields,
        )

    async def delegate(self, objective: str, *, project_id: str = "",
                       user_instructions: str = "", tools: dict[str, str] | None = None,
                       player=None, session_memory=None) -> dict:
        objective = str(objective or "").strip()
        if not objective:
            return structured_result(
                agent="JARVIS", task="", status="failed",
                errors=["A delegation objective is required."],
            )
        parent_id = f"boss_{uuid.uuid4().hex[:12]}"
        routes = self._route(objective)
        if not routes:
            self._persist(objective, task_id=parent_id, status="failed",
                          project_id=project_id, children=[])
            return structured_result(
                agent="JARVIS", task=objective, status="failed",
                errors=["No specialist capability matches this objective."],
                task_id=parent_id, objective=objective, delegated_to=[],
            )
        child_ids = [f"{parent_id}:{name.lower()}" for name in routes]
        self._persist(objective, task_id=parent_id, status="planned",
                      project_id=project_id, children=child_ids)
        context_manager.start_task(parent_id, objective, project_id)
        self.events.publish(
            "jarvis_delegation_started", f"JARVIS delegated: {objective}",
            source="JARVIS", task_id=parent_id, project_id=project_id,
        )
        results = []
        for agent_name, child_id in zip(routes, child_ids):
            self._persist(objective, task_id=child_id, status="planned",
                          project_id=project_id, parent_task_id=parent_id,
                          agent=agent_name)
            specialist = self.agents.get(agent_name)
            if specialist is None:
                results.append(structured_result(
                    agent=agent_name, task=objective, status="failed",
                    errors=["Specialist is not registered."], task_id=child_id,
                ))
                continue
            tool_name = self._select_tool(
                agent_name, objective, (tools or {}).get(agent_name, ""),
            )
            handoff = build_handoff(
                agent_id=agent_name, task_id=child_id, project_id=project_id,
                task={"objective": objective, "parent_task_id": parent_id,
                      "tool": tool_name},
                relevant_memory=specialist.memory.retrieve(project_id=project_id),
                constraints=["JARVIS remains the owner of final decisions."],
                user_instructions=user_instructions,
            )
            if not tool_name:
                result = structured_result(
                    agent=agent_name, task=objective, status="failed",
                    errors=[f"No available capability exists for {agent_name}."],
                    task_id=child_id,
                )
            else:
                result = await self.agents.assign(
                    agent_name,
                    {"tool": tool_name, "parameters": {"shared_context": handoff},
                     "objective": objective},
                    project_id=project_id, task_id=child_id,
                    player=player, session_memory=session_memory,
                )
            results.append(result)
            self.events.publish(
                "agent_message",
                f"JARVIS → {agent_name}: delegation result received",
                source="JARVIS", task_id=parent_id, project_id=project_id,
                importance="NORMAL", target_agent=agent_name,
                communication_type="agent_to_agent",
            )
        failed = [item for item in results if item.get("status") == "failed" or item.get("ok") is False]
        status = "failed" if failed else "completed"
        for result, child_id in zip(results, child_ids):
            self.runtime.update_background_task(child_id, result.get("status", "failed"),
                                                result=result)
        self.runtime.update_background_task(parent_id, status, results=results)
        self.events.publish(
            "jarvis_delegation_completed", f"JARVIS delegation {status}: {objective}",
            source="JARVIS", task_id=parent_id, project_id=project_id,
            importance="IMPORTANT" if failed else "NORMAL",
        )
        return structured_result(
            agent="JARVIS", task=objective, status=status,
            summary=f"{len(results) - len(failed)}/{len(results)} specialist tasks completed.",
            task_id=parent_id, parent_task_id=parent_id, objective=objective,
            delegated_to=routes, child_task_ids=child_ids, results=results,
        )

    # Public names used by Core callers; delegate remains the backwards-
    # compatible entry point used by the existing delegate_objective tool.
    coordinate = delegate
    orchestrate = delegate
