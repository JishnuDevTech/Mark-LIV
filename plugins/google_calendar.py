"""Google Calendar integration through the Calendar v3 REST API."""
from __future__ import annotations

import requests
from memory.config_manager import get_plugin_setting

PLUGIN_VERSION = "1.0.0"
PLUGIN = {
    "name": "google_calendar",
    "provider": "Google Calendar",
    "version": PLUGIN_VERSION,
    "description": "View and manage Google Calendar events and availability.",
    "permissions": ["READ_CALENDAR", "CREATE_EVENT", "MODIFY_EVENT", "DELETE_EVENT"],
    "parameters": {"type": "OBJECT", "properties": {}},
}
PLUGIN_SETTINGS = {
    "namespace": "google_calendar",
    "title": "GOOGLE CALENDAR",
    "fields": [{"key": "access_token", "label": "OAuth access token", "type": "password", "placeholder": "Stored securely in Keychain/keyring"}],
    "action": {"label": "CONNECT / TEST", "run": lambda values: test_connection()},
}


def test_connection():
    result = status()
    return bool(result.get("connected")), result.get("account") or result.get("error", "Connection failed")


def _token():
    return str(get_plugin_setting("google_calendar", "access_token", "") or "").strip()


def _request(method, path, **kwargs):
    if not _token():
        raise RuntimeError("Google Calendar is not configured. Add an OAuth access token in Plugin Settings.")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = "Bearer " + _token()
    headers["Authorization"] = f"Bearer {_token()}"
    headers["Authorization"] = "Bearer " + _token()
    response = requests.request(method, f"https://www.googleapis.com/calendar/v3/{path}", headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def status():
    if not _token():
        return {"connected": False, "error": "OAuth access token not configured"}
    try:
        _request("GET", "users/me/calendarList", params={"maxResults": 1})
        return {"connected": True}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


def connect():
    return bool(status().get("connected"))


def disconnect():
    return None


def _list_events(params):
    calendar_id = params.get("calendar_id", "primary")
    query = {"timeMin": params.get("time_min", ""), "timeMax": params.get("time_max", ""), "singleEvents": "true", "orderBy": "startTime", "maxResults": min(int(params.get("limit", 50)), 250)}
    query = {key: value for key, value in query.items() if value not in ("", None)}
    return _request("GET", f"calendars/{calendar_id}/events", params=query)


def _create_event(params):
    body = {"summary": params.get("title", ""), "description": params.get("description", ""), "location": params.get("location", ""), "start": {"dateTime": params.get("start_at", ""), "timeZone": params.get("timezone", "UTC")}, "end": {"dateTime": params.get("end_at", ""), "timeZone": params.get("timezone", "UTC")}}
    return _request("POST", f"calendars/{params.get('calendar_id', 'primary')}/events", json=body)


def _update_event(params):
    body = {key: params[key] for key in ("summary", "description", "location") if params.get(key) is not None}
    return _request("PATCH", f"calendars/{params.get('calendar_id', 'primary')}/events/{params.get('event_id', '')}", json=body)


def _delete_event(params):
    return _request("DELETE", f"calendars/{params.get('calendar_id', 'primary')}/events/{params.get('event_id', '')}")


def _freebusy(params):
    return _request("POST", "freeBusy", json={"timeMin": params.get("time_min", ""), "timeMax": params.get("time_max", ""), "items": [{"id": params.get("calendar_id", "primary")} ]})


PLUGIN_TOOLS = [
    {"name": "list_events", "description": "List Google Calendar events in a time range.", "parameters": {"type": "OBJECT", "properties": {"time_min": {"type": "STRING"}, "time_max": {"type": "STRING"}, "calendar_id": {"type": "STRING"}, "limit": {"type": "INTEGER"}}}, "permissions": ["READ_CALENDAR"], "handler": _list_events},
    {"name": "find_available_time", "description": "Check Google Calendar free/busy information.", "parameters": {"type": "OBJECT", "properties": {"time_min": {"type": "STRING"}, "time_max": {"type": "STRING"}, "calendar_id": {"type": "STRING"}}, "required": ["time_min", "time_max"]}, "permissions": ["READ_CALENDAR"], "handler": _freebusy},
    {"name": "create_event", "description": "Create a Google Calendar event.", "parameters": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "start_at": {"type": "STRING"}, "end_at": {"type": "STRING"}, "description": {"type": "STRING"}, "location": {"type": "STRING"}, "timezone": {"type": "STRING"}}, "required": ["title", "start_at", "end_at"]}, "permissions": ["CREATE_EVENT"], "destructive": False, "handler": _create_event},
    {"name": "update_event", "description": "Update a Google Calendar event.", "parameters": {"type": "OBJECT", "properties": {"event_id": {"type": "STRING"}, "summary": {"type": "STRING"}, "description": {"type": "STRING"}, "location": {"type": "STRING"}}, "required": ["event_id"]}, "permissions": ["MODIFY_EVENT"], "destructive": False, "handler": _update_event},
    {"name": "cancel_event", "description": "Cancel a Google Calendar event.", "parameters": {"type": "OBJECT", "properties": {"event_id": {"type": "STRING"}}, "required": ["event_id"]}, "permissions": ["DELETE_EVENT"], "destructive": True, "handler": _delete_event},
]

def run(parameters, **_kwargs):
    return _list_events(parameters)
