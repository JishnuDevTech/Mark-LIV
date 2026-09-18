"""Tool entry points for temporary current-screen understanding."""
from __future__ import annotations

import json

from core.screen_perception import screen_perception


def screen_perception_action(parameters: dict, **_kwargs) -> str:
    action = str((parameters or {}).get("action", "state")).lower().strip()
    if action in {"state", "observe", "current"}:
        return json.dumps(screen_perception.observe(force=True), ensure_ascii=False)
    if action == "start":
        return json.dumps(screen_perception.start(
            active=bool((parameters or {}).get("active", False))
        ), ensure_ascii=False)
    if action == "stop":
        return json.dumps(screen_perception.stop(), ensure_ascii=False)
    return f"Unknown screen perception action: {action}"


TOOL = {
    "name": "screen_perception",
    "description": (
        "Observe the current screen as temporary structured context. Supports "
        "state/observe, start adaptive observation, and stop observation. "
        "Screen state is not saved as Personal Memory."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "state | observe | start | stop"},
            "active": {"type": "BOOLEAN", "description": "Poll more frequently during active computer work"},
        },
        "required": ["action"],
    },
    "handler": screen_perception_action,
}
