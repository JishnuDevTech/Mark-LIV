"""Temporary, adaptive screen understanding state for computer interaction.

This is deliberately not Personal Memory. It keeps only the latest in-memory
screen state and expires observations when the observer is stopped.
"""
from __future__ import annotations

import asyncio
import hashlib
import platform
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScreenState:
    timestamp: float
    image_size: tuple[int, int]
    logical_size: tuple[int, int]
    application: str = ""
    window: str = ""
    elements: list[dict[str, Any]] = field(default_factory=list)
    relevant_text: list[str] = field(default_factory=list)
    current_focus: str = ""
    changed: bool = True
    signature: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "image_size": list(self.image_size),
            "logical_size": list(self.logical_size),
            "application": self.application,
            "window": self.window,
            "visible_elements": self.elements,
            "relevant_text": self.relevant_text,
            "current_focus": self.current_focus,
            "changed": self.changed,
        }


class ScreenPerception:
    def __init__(self, logger=print):
        self._logger = logger
        self._state: ScreenState | None = None
        self._enabled = False
        self._task: asyncio.Task | None = None
        self._interval = 2.0
        self._active = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _accessibility_snapshot(self) -> dict[str, Any]:
        if platform.system() != "Darwin":
            return {}
        script = '''
tell application "System Events"
  set frontApp to first application process whose frontmost is true
  set appName to name of frontApp
  set windowName to ""
  set elementLines to {}
  try
    set windowName to name of front window of frontApp
    repeat with itemRef in (entire contents of front window of frontApp)
      try
        set itemRole to role of itemRef
        set itemName to ""
        try
          set itemName to name of itemRef
        end try
        if itemName is not "" then set end of elementLines to itemRole & "||" & itemName
      end try
    end repeat
  end try
  set AppleScript's text item delimiters to "##"
  return appName & "||" & windowName & "##" & (elementLines as text)
end tell
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                raw_app, _, raw_elements = result.stdout.strip().partition("##")
                app_name, _, window = raw_app.partition("||")
                elements = []
                for line in raw_elements.split("##"):
                    role, sep, label = line.partition("||")
                    if sep and label:
                        elements.append({
                            "type": role,
                            "label": label,
                            "state": "visible",
                        })
                return {"application": app_name, "window": window, "elements": elements[:200]}
        except Exception as exc:
            self._logger(f"[Screen] Accessibility metadata unavailable: {exc}")
        return {}

    def observe(self, force: bool = False) -> dict[str, Any]:
        from actions.computer_control import _observe_screen

        observation = _observe_screen()
        digest = hashlib.sha256(observation.signature).hexdigest()
        previous = self._state
        changed = force or previous is None or previous.signature != digest
        metadata = self._accessibility_snapshot()
        self._state = ScreenState(
            timestamp=time.time(),
            image_size=observation.image_size,
            logical_size=observation.logical_size,
            application=metadata.get("application", ""),
            window=metadata.get("window", ""),
            elements=metadata.get("elements", []),
            current_focus=metadata.get("window", ""),
            changed=changed,
            signature=digest,
        )
        return self._state.public()

    def current(self) -> dict[str, Any]:
        if self._state is None:
            return self.observe(force=True)
        return self._state.public()

    async def _run(self) -> None:
        while self._enabled:
            try:
                state = await asyncio.to_thread(self.observe)
                # Poll more often while an interaction is active.
                self._interval = 0.5 if self._active else (2.0 if state["changed"] else 5.0)
            except Exception as exc:
                self._logger(f"[Screen] Observation failed: {exc}")
                self._interval = min(self._interval * 2, 10.0)
            await asyncio.sleep(self._interval)

    def start(self, active: bool = False) -> dict[str, Any]:
        self._enabled = True
        self._active = active
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return {"ok": True, "enabled": True, "state": self.current()}

    def stop(self) -> dict[str, Any]:
        self._enabled = False
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        return {"ok": True, "enabled": False}

    def set_active(self, active: bool) -> None:
        self._active = active


screen_perception = ScreenPerception()
