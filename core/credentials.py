"""OS-backed plugin credential storage; secrets never enter JARVIS memory."""
from __future__ import annotations

import getpass
import platform
import subprocess

_SERVICE = "JARVIS"


def set_secret(plugin: str, key: str, value: str) -> bool:
    value = str(value or "")
    try:
        import keyring
        keyring.set_password(f"{_SERVICE}:{plugin}", key, value)
        return True
    except Exception:
        pass
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "add-generic-password", "-a", getpass.getuser(),
                 "-s", f"{_SERVICE}:{plugin}:{key}", "-w", value, "-U"],
                capture_output=True, text=True, check=False,
            )
            return result.returncode == 0
        except Exception:
            pass
    return False


def get_secret(plugin: str, key: str) -> str:
    try:
        import keyring
        return keyring.get_password(f"{_SERVICE}:{plugin}", key) or ""
    except Exception:
        pass
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-a", getpass.getuser(),
                 "-s", f"{_SERVICE}:{plugin}:{key}", "-w"],
                capture_output=True, text=True, check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            pass
    return ""


def delete_secret(plugin: str, key: str) -> bool:
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "delete-generic-password", "-a", getpass.getuser(),
                 "-s", f"{_SERVICE}:{plugin}:{key}"],
                capture_output=True, text=True, check=False,
            )
            return result.returncode == 0
        except Exception:
            return False
    return False
