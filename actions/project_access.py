"""Direct, bounded filesystem and project inspection for JARVIS."""
from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

from core import context_manager

_MAX_READ = 30000
_MAX_RESULTS = 80
_MAX_SCAN_FILES = 1200
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".yaml",
    ".yml", ".toml", ".md", ".txt", ".java", ".go", ".rs", ".sql", ".sh",
}


def _base() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve(raw: str = "") -> Path:
    value = str(raw or ".").strip()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _base() / path
    return path.resolve()


def _safe(path: Path) -> bool:
    try:
        return path == _base() or _base() in path.parents or Path.home() in path.parents
    except Exception:
        return False


def _files(root: Path):
    count = 0
    if root.is_file():
        yield root
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for filename in sorted(files):
            path = Path(current) / filename
            count += 1
            if count > _MAX_SCAN_FILES:
                return
            yield path


def _structure(root: Path, max_results: int) -> str:
    lines = [f"Project root: {root}"]
    entries = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        level = len(Path(current).relative_to(root).parts)
        prefix = "  " * level
        for name in sorted(files):
            if entries >= max_results:
                lines.append(f"... truncated after {entries} entries")
                return "\n".join(lines)
            lines.append(f"{prefix}- {name}")
            entries += 1
        for name in dirs:
            if entries >= max_results:
                lines.append(f"... truncated after {entries} entries")
                return "\n".join(lines)
            lines.append(f"{prefix}+ {name}/")
            entries += 1
    return "\n".join(lines)


def _read(path: Path, start: int, end: int) -> str:
    if path.stat().st_size > _MAX_READ * 4:
        return f"File is too large to inspect directly ({path.stat().st_size} bytes)."
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[max(0, start - 1): max(0, end)]
    return "\n".join(f"{i + start}: {line}" for i, line in enumerate(selected))[:_MAX_READ]


def _search(root: Path, query: str, glob: str, max_results: int) -> str:
    pattern = re.compile(query, re.IGNORECASE)
    matches = []
    for path in _files(root):
        if glob and not fnmatch.fnmatch(path.name, glob) and not fnmatch.fnmatch(str(path.relative_to(root)), glob):
            continue
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line):
                    matches.append(f"{path}:{number}: {line.strip()[:240]}")
                    if len(matches) >= max_results:
                        return "\n".join(matches)
        except OSError:
            continue
    return "\n".join(matches) or "No matches found."


def _architecture(root: Path) -> str:
    counts: dict[str, int] = {}
    imports: list[str] = []
    for path in _files(root):
        suffix = path.suffix.lower() or "[no extension]"
        counts[suffix] = counts.get(suffix, 0) + 1
        if path.suffix == ".py":
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith(("import ", "from ")):
                        imports.append(f"{path.name}: {line.strip()}")
            except OSError:
                pass
    summary = [f"Project root: {root}", "File types:"]
    summary.extend(f"  {kind}: {count}" for kind, count in sorted(counts.items()))
    if imports:
        summary.append("Python imports:")
        summary.extend(f"  {line}" for line in imports[:120])
    return "\n".join(summary)


def project_access(parameters: dict, player=None, **_kwargs) -> str:
    params = parameters or {}
    operation = str(params.get("operation", "structure")).strip().lower()
    root = _resolve(params.get("path", "."))
    if not _safe(root):
        return f"Access denied: {root}"
    if not root.exists():
        return f"Path not found: {root}"
    limit = min(max(int(params.get("max_results", 40)), 1), _MAX_RESULTS)
    try:
        if operation == "structure":
            result = _structure(root, limit)
        elif operation == "read":
            target = root if root.is_file() else _resolve(params.get("file", ""))
            if not target.exists() or not target.is_file() or not _safe(target):
                return f"File not found or access denied: {target}"
            result = _read(target, int(params.get("start_line", 1)), int(params.get("end_line", 400)))
        elif operation == "search":
            query = str(params.get("query", "")).strip()
            if not query:
                return "A search query is required."
            result = _search(root, query, str(params.get("glob", "")), limit)
        elif operation == "architecture":
            result = _architecture(root)
        elif operation == "info":
            result = json.dumps({
                "path": str(root), "type": "file" if root.is_file() else "directory",
                "size": root.stat().st_size if root.is_file() else None,
            }, indent=2)
        else:
            return f"Unknown project_access operation: {operation}"
    except Exception as exc:
        result = f"Project access failed: {exc}"

    project_id = root.name if root.is_dir() else root.parent.name
    context_manager.record_event(project_id, "project_inspection", result[:2000], operation=operation)
    context_manager.update_project(
        project_id,
        relevant_files=[str(root)],
        current_task=f"project inspection: {operation}",
        intermediate_results=[result[:1200]],
    )
    if player:
        player.write_log(f"[Project] {operation}: {root}")
    return result


TOOL = {
    "name": "project_access",
    "description": "Inspect project files directly without using the GUI: list structure, read bounded source files, search code, inspect metadata, or summarize architecture.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "description": "structure | read | search | architecture | info"},
            "path": {"type": "STRING", "description": "Project or directory path, relative to JARVIS project or absolute under the home folder"},
            "file": {"type": "STRING", "description": "File path for read when path is the project root"},
            "query": {"type": "STRING", "description": "Regex/text query for search"},
            "glob": {"type": "STRING", "description": "Optional filename glob, e.g. *.py"},
            "start_line": {"type": "INTEGER", "description": "First line for read"},
            "end_line": {"type": "INTEGER", "description": "Last line for read"},
            "max_results": {"type": "INTEGER", "description": "Bounded result count"},
        },
        "required": ["operation"],
    },
    "handler": project_access,
}
