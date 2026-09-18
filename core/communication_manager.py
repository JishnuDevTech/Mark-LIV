"""Delivery policy between JARVIS Core events and registered endpoints."""
from __future__ import annotations

from core import event_engine
import asyncio


class CommunicationManager:
    def __init__(self, logger=print):
        self._dashboard = None
        self._logger = logger
        event_engine.set_listener(self._on_event)

    def attach_dashboard(self, dashboard) -> None:
        self._dashboard = dashboard

    def _on_event(self, event: dict) -> None:
        if not self._dashboard or event.get("importance") == "LOW":
            return
        try:
            asyncio.get_running_loop().create_task(self.publish(event))
        except RuntimeError:
            pass

    async def publish(self, event: dict) -> None:
        """Persist first, then deliver only attention-worthy events."""
        if not self._dashboard or event.get("importance") == "LOW":
            return
        try:
            await self._dashboard.broadcast({"type": "event", "event": event})
        except Exception as exc:
            self._logger(f"[Communication] delivery deferred: {exc}")

    def record(self, event_type: str, message: str, **fields) -> dict:
        return event_engine.publish(event_type, message, **fields)

    def pending(self, device_id: str, limit: int = 100) -> list[dict]:
        return event_engine.pending_for_device(device_id, limit)

    def acknowledge(self, event_ids: list[str], device_id: str) -> None:
        event_engine.mark_delivered(event_ids, device_id)
