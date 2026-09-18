"""Notion integration through the official Notion REST API."""
from __future__ import annotations

import requests
from memory.config_manager import get_plugin_setting

PLUGIN_VERSION = "1.0.0"
PLUGIN = {
    "name": "notion",
    "provider": "Notion",
    "version": PLUGIN_VERSION,
    "description": "Search, read, create, and update Notion pages.",
    "permissions": ["READ_PAGES", "CREATE_PAGES", "EDIT_PAGES", "DELETE_PAGES"],
    "parameters": {"type": "OBJECT", "properties": {}},
}
PLUGIN_SETTINGS = {
    "namespace": "notion",
    "title": "NOTION",
    "fields": [{"key": "integration_token", "label": "Integration token", "type": "password", "placeholder": "Stored securely in Keychain/keyring"}],
    "action": {"label": "CONNECT / TEST", "run": lambda values: test_connection()},
}


def test_connection():
    result = status()
    return bool(result.get("connected")), result.get("error", "Connected")


def _token():
    return str(get_plugin_setting("notion", "integration_token", "") or "").strip()


def _request(method, path, **kwargs):
    if not _token():
        raise RuntimeError("Notion is not configured. Add an integration token in Plugin Settings.")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = "Bearer " + _token()
    headers.update({"Authorization": f"Bearer {_token()}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"})
    headers["Authorization"] = "Bearer " + _token()
    response = requests.request(method, f"https://api.notion.com/v1/{path}", headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def status():
    if not _token():
        return {"connected": False, "error": "Integration token not configured"}
    try:
        _request("GET", "users/me")
        return {"connected": True}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


def connect():
    return bool(status().get("connected"))


def disconnect():
    return None


def _search(params):
    return _request("POST", "search", json={"query": params.get("query", ""), "page_size": min(int(params.get("limit", 20)), 100)})


def _read(params):
    return _request("GET", f"pages/{params.get('page_id', '')}")


def _create(params):
    parent = params.get("parent_id", "")
    if not parent:
        raise ValueError("parent_id is required for a Notion page")
    return _request("POST", "pages", json={"parent": {"page_id": parent}, "properties": {"title": {"title": [{"text": {"content": params.get("title", "")}}]}}, "children": []})


def _update(params):
    return _request("PATCH", f"pages/{params.get('page_id', '')}", json={"properties": {"title": {"title": [{"text": {"content": params.get("title", "")}}]}}})


def _archive(params):
    return _request("PATCH", f"pages/{params.get('page_id', '')}", json={"archived": True})


PLUGIN_TOOLS = [
    {"name": "search", "description": "Search Notion pages.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}, "limit": {"type": "INTEGER"}}, "required": ["query"]}, "permissions": ["READ_PAGES"], "handler": _search},
    {"name": "read_page", "description": "Read a Notion page.", "parameters": {"type": "OBJECT", "properties": {"page_id": {"type": "STRING"}}, "required": ["page_id"]}, "permissions": ["READ_PAGES"], "handler": _read},
    {"name": "create_page", "description": "Create a Notion page under a parent page.", "parameters": {"type": "OBJECT", "properties": {"parent_id": {"type": "STRING"}, "title": {"type": "STRING"}}, "required": ["parent_id", "title"]}, "permissions": ["CREATE_PAGES"], "destructive": False, "handler": _create},
    {"name": "update_page", "description": "Update a Notion page title.", "parameters": {"type": "OBJECT", "properties": {"page_id": {"type": "STRING"}, "title": {"type": "STRING"}}, "required": ["page_id", "title"]}, "permissions": ["EDIT_PAGES"], "destructive": False, "handler": _update},
    {"name": "archive_page", "description": "Archive a Notion page.", "parameters": {"type": "OBJECT", "properties": {"page_id": {"type": "STRING"}}, "required": ["page_id"]}, "permissions": ["DELETE_PAGES"], "destructive": True, "handler": _archive},
]

def run(parameters, **_kwargs):
    return _search(parameters)
