"""Gmail integration through the Gmail REST API."""
from __future__ import annotations

import base64
import email.message
import requests

from memory.config_manager import get_plugin_setting

PLUGIN_VERSION = "1.0.0"
PLUGIN = {
    "name": "gmail",
    "provider": "Google Gmail",
    "version": PLUGIN_VERSION,
    "description": "Search, read, draft, archive, and manage Gmail messages.",
    "permissions": ["READ_EMAIL", "CREATE_DRAFT", "SEND_EMAIL", "ARCHIVE_EMAIL", "DELETE_EMAIL"],
    "parameters": {"type": "OBJECT", "properties": {}},
}
PLUGIN_SETTINGS = {
    "namespace": "gmail",
    "title": "GMAIL",
    "fields": [
        {"key": "access_token", "label": "OAuth access token", "type": "password",
         "placeholder": "Stored securely in Keychain/keyring"},
    ],
    "action": {"label": "CONNECT / TEST", "run": lambda values: test_connection()},
}


def test_connection():
    result = status()
    return bool(result.get("connected")), result.get("account") or result.get("error", "Connection failed")


def _token() -> str:
    return str(get_plugin_setting("gmail", "access_token", "") or "").strip()


def _request(method: str, path: str, **kwargs):
    token = _token()
    if not token:
        raise RuntimeError("Gmail is not configured. Add an OAuth access token in Plugin Settings.")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Accept", "application/json")
    headers["Authorization"] = "Bearer " + token
    response = requests.request(method, f"https://gmail.googleapis.com/gmail/v1/users/me/{path}", headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def status() -> dict:
    if not _token():
        return {"connected": False, "error": "OAuth access token not configured"}
    try:
        profile = _request("GET", "profile")
        return {"connected": True, "account": profile.get("emailAddress", "")}
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


def connect() -> bool:
    return bool(status().get("connected"))


def disconnect() -> None:
    return None


def _search(params):
    data = _request("GET", "messages", params={"q": params.get("query", ""), "maxResults": min(int(params.get("limit", 10)), 50)})
    messages = []
    for item in data.get("messages", []):
        messages.append(_read({"message_id": item.get("id", ""), "metadata": True})["result"])
    return {"messages": messages, "next_page_token": data.get("nextPageToken", "")}


def _headers(message):
    return {header["name"].lower(): header["value"] for header in message.get("payload", {}).get("headers", [])}


def _read(params):
    message = _request("GET", f"messages/{params.get('message_id', '')}", params={"format": "metadata" if params.get("metadata") else "full"})
    headers = _headers(message)
    return {"result": {"id": message.get("id"), "thread_id": message.get("threadId"), "subject": headers.get("subject", ""), "from": headers.get("from", ""), "to": headers.get("to", ""), "date": headers.get("date", ""), "labels": message.get("labelIds", []), "snippet": message.get("snippet", "")}}


def _draft(params):
    msg = email.message.EmailMessage()
    msg["To"] = params.get("to", "")
    msg["Subject"] = params.get("subject", "")
    msg.set_content(params.get("body", ""))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _request("POST", "drafts", json={"message": {"raw": raw}})


def _send(params):
    return _request("POST", "messages/send", json={"raw": params.get("raw", "")})


def _reply(params):
    message_id = str(params.get("message_id", "")).strip()
    body = str(params.get("body", "")).strip()
    if not message_id or not body:
        raise ValueError("message_id and body are required for a reply")

    original = _request(
        "GET", f"messages/{message_id}", params={"format": "metadata"}
    )
    original_headers = _headers(original)
    recipient = original_headers.get("reply-to") or original_headers.get("from")
    if not recipient:
        raise ValueError("The selected message has no reply recipient")

    subject = original_headers.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message = email.message.EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    if original_headers.get("message-id"):
        message["In-Reply-To"] = original_headers["message-id"]
        message["References"] = original_headers["message-id"]
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return _request("POST", "messages/send", json={"raw": raw})


def _modify(params, add=None, remove=None):
    return _request("POST", f"messages/{params.get('message_id', '')}/modify", json={"addLabelIds": add or [], "removeLabelIds": remove or []})


def _archive(params):
    return _modify(params, remove=["INBOX"])


def _mark_read(params):
    return _modify(params, remove=["UNREAD"])


def _delete(params):
    return _request("DELETE", f"messages/{params.get('message_id', '')}")


PLUGIN_TOOLS = [
    {"name": "search", "description": "Search Gmail messages.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}, "limit": {"type": "INTEGER"}}, "required": ["query"]}, "permissions": ["READ_EMAIL"], "handler": _search},
    {"name": "read", "description": "Read Gmail message metadata and snippet.", "parameters": {"type": "OBJECT", "properties": {"message_id": {"type": "STRING"}}, "required": ["message_id"]}, "permissions": ["READ_EMAIL"], "handler": _read},
    {"name": "create_draft", "description": "Create a Gmail draft without sending it.", "parameters": {"type": "OBJECT", "properties": {"to": {"type": "STRING"}, "subject": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["to", "subject", "body"]}, "permissions": ["CREATE_DRAFT"], "handler": _draft},
    {"name": "send", "description": "Send a prepared Gmail raw message; requires confirmation.", "parameters": {"type": "OBJECT", "properties": {"raw": {"type": "STRING"}}, "required": ["raw"]}, "permissions": ["SEND_EMAIL"], "destructive": True, "handler": _send},
    {"name": "reply", "description": "Reply to a Gmail message; requires confirmation.", "parameters": {"type": "OBJECT", "properties": {"message_id": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["message_id", "body"]}, "permissions": ["SEND_EMAIL"], "destructive": True, "handler": _reply},
    {"name": "archive", "description": "Archive a Gmail message.", "parameters": {"type": "OBJECT", "properties": {"message_id": {"type": "STRING"}}, "required": ["message_id"]}, "permissions": ["ARCHIVE_EMAIL"], "destructive": False, "handler": _archive},
    {"name": "mark_read", "description": "Mark a Gmail message read.", "parameters": {"type": "OBJECT", "properties": {"message_id": {"type": "STRING"}}, "required": ["message_id"]}, "permissions": ["READ_EMAIL"], "handler": _mark_read},
    {"name": "delete", "description": "Permanently delete a Gmail message; requires confirmation.", "parameters": {"type": "OBJECT", "properties": {"message_id": {"type": "STRING"}}, "required": ["message_id"]}, "permissions": ["DELETE_EMAIL"], "destructive": True, "handler": _delete},
]

def run(parameters, **_kwargs):
    return _search(parameters)
