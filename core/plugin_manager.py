"""JARVIS Plugin Manager: lifecycle, permissions, namespaced tools, and status."""
from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass
from typing import Callable

from core.plugin_loader import PluginRecord, PluginRegistry
from memory.config_manager import (
    get_plugin_enabled, get_plugin_permissions, save_plugin_enabled,
    save_plugin_permissions,
)

_READ_ONLY = {"READ", "READ_ONLY", "LIST", "SEARCH", "VIEW"}


@dataclass
class PluginTool:
    name: str
    plugin: str
    description: str
    parameters: dict
    permissions: set[str]
    destructive: bool
    handler: Callable

    @property
    def api_name(self) -> str:
        if self.name == self.plugin:
            return self.plugin
        return f"{self.plugin}__{self.name}".replace("-", "_").replace(".", "__")


class PluginManager:
    """Manager around the existing registry; plugins remain provider-specific."""

    def __init__(self, registry: PluginRegistry, logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self.registry = registry
        self._logger = logger
        self._notify = notify or (lambda _message: None)
        self._tools: dict[str, PluginTool] = {}
        self._discover_tools()

    def _discover_tools(self) -> None:
        for record in self.registry._all_records:
            if not record.valid:
                continue
            module_tools = getattr(record.module, "PLUGIN_TOOLS", None) or []
            if not module_tools:
                module_tools = [{
                    "name": record.name,
                    "description": record.description,
                    "parameters": record.parameters,
                    "permissions": record.permissions,
                    "destructive": False,
                    "handler": record.run,
                }]
            for metadata in module_tools:
                if not isinstance(metadata, dict) or not callable(metadata.get("handler", record.run)):
                    continue
                tool_name = str(metadata.get("name", record.name)).strip()
                if not tool_name:
                    continue
                tool = PluginTool(
                    name=tool_name,
                    plugin=record.name,
                    description=str(metadata.get("description", record.description)),
                    parameters=metadata.get("parameters", record.parameters),
                    permissions={str(p).upper() for p in metadata.get("permissions", record.permissions)},
                    destructive=bool(metadata.get("destructive", False)),
                    handler=metadata.get("handler", record.run),
                )
                self._tools[tool.api_name] = tool

    def tool_declarations(self) -> list[dict]:
        declarations = []
        for api_name, tool in self._tools.items():
            if not get_plugin_enabled(tool.plugin):
                continue
            status = self.status(tool.plugin)
            granted = set(status.get("permissions", []))
            missing = sorted(tool.permissions - granted)
            availability = (
                "connected and authorized"
                if status.get("connected") and not missing
                else "not currently connected"
                if not status.get("connected")
                else f"requires permission: {', '.join(missing)}"
            )
            declarations.append({
                "name": api_name,
                "description": f"[{tool.plugin}; {availability}] {tool.description}",
                "parameters": tool.parameters,
            })
        return declarations

    def capability_snapshot(self) -> list[dict]:
        """Return the same plugin/tool/permission view used for execution."""
        rows = []
        for plugin in self.registry._plugins:
            status = self.status(plugin)
            tools = []
            for tool in self._tools.values():
                if tool.plugin != plugin:
                    continue
                missing = sorted(tool.permissions - set(status.get("permissions", [])))
                tools.append({
                    "name": tool.api_name,
                    "available": bool(
                        status.get("enabled") and status.get("connected") and not missing
                    ),
                    "required_permissions": sorted(tool.permissions),
                    "missing_permissions": missing,
                })
            rows.append({**status, "tool_capabilities": tools})
        return rows

    def has_tool(self, name: str) -> bool:
        return name in self._tools or self.registry.has(name)

    def scheduling(self, name: str):
        tool = self._tools.get(name)
        return self.registry.scheduling(tool.plugin) if tool else self.registry.scheduling(name)

    def grant_permissions(self, plugin: str, permissions: list[str]) -> None:
        save_plugin_permissions(plugin, permissions)

    def lifecycle(self, plugin: str, operation: str) -> dict:
        record = self.registry._plugins.get(plugin)
        if record is None:
            return {"ok": False, "plugin": plugin, "error": "Plugin is not installed."}
        operation = str(operation or "").lower().strip()
        try:
            if operation == "enable":
                save_plugin_enabled(plugin, True)
            elif operation == "disable":
                save_plugin_enabled(plugin, False)
            elif operation in ("connect", "authenticate"):
                fn = getattr(record.module, "connect", None) or getattr(record.module, "authenticate", None)
                if not callable(fn):
                    return {"ok": False, "plugin": plugin, "error": "Plugin has no connection handler."}
                result = fn()
                if result is False:
                    return {"ok": False, "plugin": plugin, "error": "Plugin connection failed."}
            elif operation == "disconnect":
                fn = getattr(record.module, "disconnect", None)
                if callable(fn):
                    fn()
            else:
                return {"ok": False, "plugin": plugin, "error": f"Unknown lifecycle operation: {operation}"}
            return self.status(plugin)
        except Exception as exc:
            self._logger(f"[Plugins] {plugin} {operation} failed: {exc}")
            self._notify(f"Plugin {plugin} could not {operation}.")
            return {"ok": False, "plugin": plugin, "error": str(exc)}

    def status(self, plugin: str) -> dict:
        record = self.registry._plugins.get(plugin)
        if record is None:
            return {"ok": False, "plugin": plugin, "error": "Plugin is not installed."}
        connected = False
        error = record.error
        try:
            fn = getattr(record.module, "status", None)
            value = fn() if callable(fn) else None
            connected = bool(value.get("connected")) if isinstance(value, dict) else bool(value)
            if isinstance(value, dict):
                error = value.get("error", error)
        except Exception as exc:
            error = str(exc)
        return {
            "ok": True,
            "plugin": plugin,
            "provider": record.provider,
            "version": getattr(record.module, "PLUGIN_VERSION", "1.0"),
            "enabled": get_plugin_enabled(plugin),
            "connected": connected,
            "authenticated": connected,
            "permissions": sorted(get_plugin_permissions(plugin)),
            "requested_permissions": list(record.permissions),
            "tools": sorted(tool.api_name for tool in self._tools.values() if tool.plugin == plugin),
            "error": error,
        }

    def list_for_ui(self) -> list[dict]:
        rows = []
        for record in self.registry._all_records:
            row = {
                "name": record.name,
                "description": record.description,
                "file": record.file,
                "valid": record.valid,
                "error": record.error,
                "provider": record.provider,
                "permissions": list(record.permissions),
                "enabled": get_plugin_enabled(record.name) if record.valid else False,
            }
            if record.valid:
                row.update(self.status(record.name))
            rows.append(row)
        return rows

    def settings_schemas(self):
        return self.registry.settings_schemas()

    def execute(self, name: str, parameters: dict, player=None, session_memory=None,
                confirmed: bool = False) -> dict:
        tool = self._tools.get(name)
        if tool is None and self.registry.has(name):
            tool = next((item for item in self._tools.values() if item.plugin == name), None)
        if tool is None:
            return {"ok": False, "error": f"Plugin tool '{name}' is not available."}
        if not get_plugin_enabled(tool.plugin):
            return {"ok": False, "plugin": tool.plugin, "error": "Plugin is disabled."}
        granted = get_plugin_permissions(tool.plugin)
        missing = sorted(tool.permissions - granted)
        if missing:
            return {"ok": False, "plugin": tool.plugin, "error": "Permission required.", "missing_permissions": missing}
        if tool.destructive:
            from core import confirm as confirm_gate

            title = f"{tool.plugin}: {tool.name}"
            detail = "This action changes or sends external data."

            def _run_confirmed() -> str:
                try:
                    signature = inspect.signature(tool.handler)
                    kwargs = {}
                    if "player" in signature.parameters:
                        kwargs["player"] = player
                    if "session_memory" in signature.parameters:
                        kwargs["session_memory"] = session_memory
                    result = tool.handler(parameters, **kwargs)
                    return str(result or "Done.")
                except Exception as exc:
                    raise RuntimeError(str(exc)) from exc

            message = confirm_gate.request(
                f"{tool.plugin}:{name}",
                f"{title} requires confirmation",
                detail,
                _run_confirmed,
            )
            return {
                "ok": False,
                "plugin": tool.plugin,
                "tool": name,
                "confirmation_pending": True,
                "error": message,
            }
        try:
            signature = inspect.signature(tool.handler)
            kwargs = {}
            if "player" in signature.parameters:
                kwargs["player"] = player
            if "session_memory" in signature.parameters:
                kwargs["session_memory"] = session_memory
            result = tool.handler(parameters, **kwargs)
            return {"ok": True, "plugin": tool.plugin, "tool": name, "result": result or "Done."}
        except Exception as exc:
            traceback.print_exc()
            self._notify(f"Plugin {tool.plugin} failed while running {tool.name}.")
            return {"ok": False, "plugin": tool.plugin, "tool": name, "error": str(exc)}
