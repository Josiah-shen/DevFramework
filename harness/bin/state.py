"""Harness state file IO: frontmatter read/write, atomic replace, section append.

All paths are resolved relative to the project root (repo containing CLAUDE.md).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from start (cwd by default) until CLAUDE.md is found."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "CLAUDE.md").is_file():
            return candidate
    raise FileNotFoundError("CLAUDE.md not found in any ancestor directory")


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (write tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class Frontmatter:
    data: dict[str, Any]
    body: str

    def render(self) -> str:
        lines = ["---"]
        for k, v in self.data.items():
            lines.append(f"{k}: {_render_yaml_value(v)}")
        lines.append("---")
        lines.append("")
        return "\n".join(lines) + self.body


def _render_yaml_value(v: Any) -> str:
    """Render a Python value as a simple YAML scalar/list.

    Supports: str, int, bool, None, date, list of scalars, empty list.
    Avoids importing PyYAML to keep stdlib-only.
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + ", ".join(_render_yaml_value(x) for x in v) + "]"
    if isinstance(v, str):
        if v == "" or any(c in v for c in ":#\n") or v.strip() != v:
            return json.dumps(v, ensure_ascii=False)
        return v
    return json.dumps(v, ensure_ascii=False)


def parse_frontmatter(text: str) -> Frontmatter:
    """Parse a simple key:value frontmatter block.

    Only supports flat key:value pairs and inline JSON-ish lists. Good enough
    for our controlled templates — we never hand-edit complex structures.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return Frontmatter(data={}, body=text)
    end = text.find("\n---", 4)
    if end == -1:
        return Frontmatter(data={}, body=text)
    header = text[4:end].strip()
    body_start = end + len("\n---")
    if text[body_start:body_start + 1] == "\n":
        body_start += 1
    body = text[body_start:]

    data: dict[str, Any] = {}
    for raw in header.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = _parse_yaml_value(value.strip())
    return Frontmatter(data=data, body=body)


def _parse_yaml_value(raw: str) -> Any:
    if raw == "" or raw == "null" or raw == "~":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return [s.strip().strip('"') for s in inner.split(",")]
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def read_plan(path: Path) -> Frontmatter:
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def write_plan(path: Path, frontmatter: Frontmatter) -> None:
    atomic_write(path, frontmatter.render())


def append_section(path: Path, heading: str, body: str) -> None:
    """Append a block under a heading (``## heading`` ..) to the plan body.

    If the heading exists, append to its section. If not, create the heading
    at end-of-file.
    """
    text = path.read_text(encoding="utf-8")
    marker = f"\n## {heading}\n"
    idx = text.find(marker)
    if idx == -1:
        appended = text.rstrip() + "\n\n## " + heading + "\n" + body.rstrip() + "\n"
    else:
        start = idx + len(marker)
        next_heading = text.find("\n## ", start)
        if next_heading == -1:
            appended = text.rstrip() + "\n" + body.rstrip() + "\n"
        else:
            appended = text[:next_heading].rstrip() + "\n" + body.rstrip() + "\n" + text[next_heading:]
    atomic_write(path, appended)


def plan_path(root: Path, slug: str) -> Path:
    return root / "harness" / "exec-plans" / f"{slug}.md"


def audit_path(root: Path, today: date | None = None) -> Path:
    today = today or date.today()
    return root / "harness" / "memory" / f"audit-{today.isoformat()}.md"


def latest_audit_path(root: Path) -> Path:
    return root / "harness" / "memory" / "latest-audit.md"


def checkpoint_path(root: Path, slug: str) -> Path:
    return root / "harness" / "tasks" / slug / "checkpoint.md"
