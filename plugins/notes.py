"""Local JARVIS workspace notes, separate from personal memory."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
NOTES_PATH = BASE_DIR / "memory" / "workspace_notes.json"
PLUGIN_VERSION = "1.0.0"
PLUGIN = {
    "name": "notes",
    "provider": "JARVIS local workspace",
    "version": PLUGIN_VERSION,
    "description": "Create, search, read, update, and archive workspace notes without storing them as personal memory.",
    "permissions": ["READ_NOTES", "CREATE_NOTES", "EDIT_NOTES", "DELETE_NOTES"],
    "parameters": {"type": "OBJECT", "properties": {}},
}


def _load():
    try:
        data = json.loads(NOTES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = NOTES_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, NOTES_PATH)


def status():
    return {"connected": True, "storage": str(NOTES_PATH)}


def connect():
    return True


def disconnect():
    return None


def _create(params):
    data = _load()
    note_id = uuid.uuid4().hex[:12]
    data[note_id] = {"id": note_id, "title": params.get("title", "Untitled"), "content": params.get("content", ""), "category": params.get("category", ""), "archived": False}
    _save(data)
    return data[note_id]


def _search(params):
    query = str(params.get("query", "")).lower()
    return [note for note in _load().values() if not note.get("archived") and query in (note.get("title", "") + " " + note.get("content", "")).lower()]


def _read(params):
    return _load().get(params.get("note_id", ""), {"error": "Note not found"})


def _update(params):
    data = _load()
    note = data.get(params.get("note_id", ""))
    if not note:
        return {"error": "Note not found"}
    for key in ("title", "content", "category"):
        if params.get(key) is not None:
            note[key] = params[key]
    _save(data)
    return note


def _archive(params):
    data = _load()
    note = data.get(params.get("note_id", ""))
    if not note:
        return {"error": "Note not found"}
    note["archived"] = True
    _save(data)
    return note


PLUGIN_TOOLS = [
    {"name": "search", "description": "Search local JARVIS workspace notes.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}, "permissions": ["READ_NOTES"], "handler": _search},
    {"name": "read", "description": "Read a workspace note.", "parameters": {"type": "OBJECT", "properties": {"note_id": {"type": "STRING"}}, "required": ["note_id"]}, "permissions": ["READ_NOTES"], "handler": _read},
    {"name": "create", "description": "Create a local workspace note.", "parameters": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "content": {"type": "STRING"}, "category": {"type": "STRING"}}, "required": ["title", "content"]}, "permissions": ["CREATE_NOTES"], "destructive": False, "handler": _create},
    {"name": "update", "description": "Update a local workspace note.", "parameters": {"type": "OBJECT", "properties": {"note_id": {"type": "STRING"}, "title": {"type": "STRING"}, "content": {"type": "STRING"}, "category": {"type": "STRING"}}, "required": ["note_id"]}, "permissions": ["EDIT_NOTES"], "destructive": False, "handler": _update},
    {"name": "archive", "description": "Archive a local workspace note.", "parameters": {"type": "OBJECT", "properties": {"note_id": {"type": "STRING"}}, "required": ["note_id"]}, "permissions": ["DELETE_NOTES"], "destructive": False, "handler": _archive},
]

def run(parameters, **_kwargs):
    return _search(parameters)
