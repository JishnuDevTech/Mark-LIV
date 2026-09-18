"""JARVIS task, schedule, and inbox operations."""
from __future__ import annotations

import json
from core import productivity_store
from core import event_engine


def productivity(parameters: dict, player=None, **_kwargs) -> str:
    params = parameters or {}
    operation = str(params.get("operation", "list_tasks")).strip().lower()
    try:
        if operation == "create_task":
            item = productivity_store.create_task(
                params.get("title", ""),
                due_at=params.get("due_at", ""),
                priority=params.get("priority", "normal"),
                category=params.get("category", ""),
                project_id=params.get("project_id", ""),
                source=params.get("source", "jarvis"),
                recurrence=params.get("recurrence", ""),
                notes=params.get("notes", ""),
            )
            result = f"Task created: {item['title']} ({item['id']})."
            event_engine.publish(
                "TASK_CREATED", result, source="productivity", importance="NORMAL",
                task_id=item["id"], dedupe_key=f"task-created:{item['id']}",
            )
        elif operation == "list_tasks":
            result = json.dumps(productivity_store.list_tasks(params.get("status", "")), indent=2)
        elif operation == "update_task":
            updates = {key: params[key] for key in
                       ("status", "due_at", "priority", "notes", "recurrence")
                       if params.get(key) is not None}
            item = productivity_store.update_task(params.get("task_id", ""), **updates)
            result = json.dumps(item, indent=2) if item else "Task not found."
        elif operation in ("complete_task", "archive_task"):
            fn = productivity_store.complete_task if operation == "complete_task" else productivity_store.archive_task
            item = fn(params.get("task_id", ""))
            result = f"Task {operation.replace('_', ' ')}: {item['title']}." if item else "Task not found."
            if item and operation == "complete_task":
                event_engine.publish(
                    "TASK_COMPLETED", result, source="productivity", importance="IMPORTANT",
                    task_id=item["id"], dedupe_key=f"task-completed:{item['id']}",
                )
        elif operation == "create_event":
            item = productivity_store.create_event(
                params.get("title", ""), start_at=params.get("start_at", ""),
                end_at=params.get("end_at", ""), location=params.get("location", ""),
                source=params.get("source", "jarvis"),
            )
            result = f"Schedule event created: {item['title']} ({item['id']})."
        elif operation == "list_events":
            result = json.dumps(productivity_store.list_events(params.get("day", "")), indent=2)
        elif operation == "create_inbox_item":
            item = productivity_store.create_inbox_item(
                params.get("subject", ""), sender=params.get("sender", ""),
                category=params.get("category", "informational"),
                urgency=params.get("urgency", "normal"), source=params.get("source", "plugin"),
                external_id=params.get("external_id", ""),
            )
            result = f"Inbox item recorded: {item['subject']} ({item['id']})."
        elif operation == "list_inbox":
            result = json.dumps(productivity_store.list_inbox(
                params.get("category", ""), params.get("urgency", "")), indent=2)
        else:
            return f"Unknown productivity operation: {operation}"
    except Exception as exc:
        result = f"Productivity operation failed: {exc}"
    if player:
        player.write_log(f"[Productivity] {operation}")
    return result


TOOL = {
    "name": "productivity",
    "description": "Manage JARVIS-owned temporary tasks, deadlines, recurring work, schedule events, and inbox records. Do not save these as personal memory.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "description": "create_task | list_tasks | update_task | complete_task | archive_task | create_event | list_events | create_inbox_item | list_inbox"},
            "title": {"type": "STRING", "description": "Task or event title"},
            "task_id": {"type": "STRING", "description": "Existing task ID"},
            "due_at": {"type": "STRING", "description": "Task deadline in ISO format"},
            "start_at": {"type": "STRING", "description": "Event start in ISO format"},
            "end_at": {"type": "STRING", "description": "Event end in ISO format"},
            "day": {"type": "STRING", "description": "Day prefix for event listing, YYYY-MM-DD"},
            "status": {"type": "STRING", "description": "open | in_progress | completed | archived"},
            "priority": {"type": "STRING", "description": "low | normal | high | urgent"},
            "category": {"type": "STRING", "description": "Task category or inbox category"},
            "project_id": {"type": "STRING", "description": "Optional project association"},
            "recurrence": {"type": "STRING", "description": "Optional recurrence rule"},
            "notes": {"type": "STRING", "description": "Task notes"},
            "subject": {"type": "STRING", "description": "Inbox subject"},
            "sender": {"type": "STRING", "description": "Inbox sender"},
            "urgency": {"type": "STRING", "description": "normal | important | urgent"},
            "source": {"type": "STRING", "description": "jarvis or external plugin name"},
            "external_id": {"type": "STRING", "description": "Provider item ID"},
            "location": {"type": "STRING", "description": "Event location"},
        },
        "required": ["operation"],
    },
    "handler": productivity,
}
