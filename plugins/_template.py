"""
Drop-in JARVIS plugin template.

Copy this file, rename it (no leading underscore), fill in PLUGIN and run().
No other file needs to change — JARVIS discovers this automatically at startup.
"""

PLUGIN = {
    "name": "my_plugin",                     # snake_case, unique, ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
    "provider": "example",
    "version": "1.0.0",
    "permissions": [],                         # granted separately by the user
    "description": (
        "One or two sentences Gemini uses to decide when to call this tool. "
        "Be explicit about trigger phrases and, if it could be confused with "
        "another tool, say which tool NOT to use instead (see game_updater's "
        "description in main.py for the pattern)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "example_arg": {"type": "STRING", "description": "What this argument means"},
        },
        "required": [],   # omit or leave empty for a zero-argument tool
    },
}

# Optional multi-tool contract. Existing one-tool plugins need not define this.
# Each handler receives the normal parameters dict and may declare player and
# session_memory as optional keyword arguments.
PLUGIN_VERSION = "1.0.0"
PLUGIN_TOOLS = [
    # {
    #     "name": "search",
    #     "description": "Search the provider",
    #     "parameters": {"type": "OBJECT", "properties": {}},
    #     "permissions": ["READ"],
    #     "destructive": False,
    #     "handler": search,
    # },
]


def status() -> dict:
    """Return connection state without exposing credentials."""
    return {"connected": False, "error": "Not configured"}


def connect() -> bool:
    """Authenticate using credentials retrieved by the plugin itself."""
    return False


def disconnect() -> None:
    return None

def run(parameters: dict, player=None, session_memory=None) -> str:
    """
    parameters: dict of the args Gemini extracted, matching PLUGIN['parameters'].
    player: the JarvisUI instance — use player.write_log(f"JARVIS: ...") to log,
            same as actions/*.py. May be None.
    session_memory: reserved, usually None today (core tools mostly pass None too).
    Return a short natural-language string — this is spoken back to the user.
    Never raise: catch your own errors and return a spoken error string instead
    (the loader also catches exceptions as a second safety net, but don't rely on it).
    """
    example_arg = parameters.get("example_arg", "")
    try:
        result_text = f"Did the thing with {example_arg}."
    except Exception as e:
        return f"Sir, my_plugin failed: {e}"
    if player:
        try:
            player.write_log(f"JARVIS: {result_text}")
        except Exception:
            pass
    return result_text
