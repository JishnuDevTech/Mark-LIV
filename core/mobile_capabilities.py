"""JARVIS-owned registry for capabilities exposed by authenticated phones."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MobileDevice:
    device_id: str
    connected: bool = False
    capabilities: set[str] = field(default_factory=set)
    permissions: set[str] = field(default_factory=set)
    telemetry: dict = field(default_factory=dict)


class MobileCapabilityManager:
    """Registers paired devices without granting unrestricted phone access."""

    def __init__(self, logger=print):
        self._devices: dict[str, MobileDevice] = {}
        self._dashboard = None
        self._logger = logger

    def attach_dashboard(self, dashboard) -> None:
        self._dashboard = dashboard

    def register(self, device_id: str, connected: bool = True,
                 permissions: set[str] | None = None) -> None:
        if not device_id:
            return
        device = self._devices.setdefault(device_id, MobileDevice(device_id))
        device.connected = connected
        if not device.capabilities:
            device.capabilities = {
                "status", "events", "commands", "notifications", "voice",
                "battery", "connection",
            }
        if not device.permissions:
            device.permissions = {"MOBILE_STATUS"}
        if permissions:
            device.permissions.update(str(p).upper() for p in permissions)

    def set_connected(self, device_id: str, connected: bool) -> None:
        self.register(device_id, connected)

    def connected_devices(self) -> list[dict]:
        return [self.describe(d) for d in self._devices.values() if d.connected]

    def describe(self, device: MobileDevice) -> dict:
        return {
            "device_id": device.device_id,
            "connected": device.connected,
            "capabilities": sorted(device.capabilities),
            "permissions": sorted(device.permissions),
            "telemetry": dict(device.telemetry),
        }

    def declarations(self) -> list[dict]:
        return [
            {
                "name": "get_mobile_status",
                "description": "Read the connected Android device and its registered capabilities.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "send_mobile_notification",
                "description": "Send a notification to the connected phone. Requires explicit mobile notification permission.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"title": {"type": "STRING"}, "message": {"type": "STRING"}},
                    "required": ["message"],
                },
            },
            {
                "name": "get_battery_status",
                "description": "Read the latest battery telemetry reported by the connected Android device.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "get_connection_status",
                "description": "Read the authenticated Android connection status and latest telemetry.",
                "parameters": {"type": "OBJECT", "properties": {}},
            },
            {
                "name": "send_mobile_message",
                "description": "Send a message to the connected Android companion.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"message": {"type": "STRING"}},
                    "required": ["message"],
                },
            },
        ]

    def update_telemetry(self, device_id: str, telemetry: dict) -> None:
        self.register(device_id, True)
        device = self._devices[device_id]
        device.telemetry.update({
            str(k): v for k, v in telemetry.items()
            if isinstance(k, str) and isinstance(v, (str, int, float, bool))
        })
        if telemetry.get("notification_permission") is True:
            device.permissions.add("MOBILE_NOTIFY")
        if telemetry.get("voice_permission") is True:
            device.permissions.add("MOBILE_VOICE")
        if telemetry.get("command_permission") is True:
            device.permissions.add("MOBILE_COMMAND")

    async def execute(self, name: str, args: dict) -> dict:
        devices = [d for d in self._devices.values() if d.connected]
        if not devices:
            return {"ok": False, "error": "No connected mobile device is registered."}
        device = devices[0]
        if name in {"get_mobile_status", "mobile_status"}:
            return {"ok": True, "device": self.describe(device)}
        if name == "get_battery_status":
            return {"ok": True, "device_id": device.device_id,
                    "battery": device.telemetry.get("battery")}
        if name == "get_connection_status":
            return {"ok": True, "device_id": device.device_id,
                    "connected": device.connected,
                    "transport": device.telemetry.get("transport", "websocket"),
                    "last_telemetry": device.telemetry}
        if name in {"send_mobile_notification", "mobile_notify"}:
            if "MOBILE_NOTIFY" not in device.permissions:
                return {"ok": False, "error": "Mobile notification permission is not granted."}
            message = str(args.get("message", "")).strip()
            if not message:
                return {"ok": False, "error": "Notification message is required."}
            if self._dashboard is None:
                return {"ok": False, "error": "Mobile communication is unavailable."}
            request_id = uuid.uuid4().hex
            payload = {
                "type": "mobile_notification",
                "request_id": request_id,
                "title": str(args.get("title", "JARVIS")).strip() or "JARVIS",
                "message": message,
            }
            sent, result = await self._dashboard.request_device_action(
                device.device_id, payload, request_id, timeout=8.0,
            )
            if not sent:
                return {"ok": False, "device_id": device.device_id,
                        "error": "Android companion did not acknowledge the notification."}
            return {"ok": bool(result.get("ok", False)), "device_id": device.device_id,
                    "delivered": bool(result.get("ok", False)),
                    "error": result.get("error")}
        if name == "send_mobile_message":
            if "MOBILE_COMMAND" not in device.permissions:
                return {"ok": False, "error": "Mobile command permission is not granted."}
            message = str(args.get("message", "")).strip()
            if not message:
                return {"ok": False, "error": "Message is required."}
            request_id = uuid.uuid4().hex
            sent, result = await self._dashboard.request_device_action(
                device.device_id,
                {"type": "mobile_message", "request_id": request_id, "message": message},
                request_id, timeout=8.0,
            )
            return {"ok": bool(sent and result.get("ok")), "device_id": device.device_id,
                    "error": result.get("error") if sent else "Android companion did not acknowledge the message."}
        return {"ok": False, "error": f"Unknown mobile tool: {name}"}

    def snapshot(self) -> dict:
        return {"connected_devices": self.connected_devices()}
