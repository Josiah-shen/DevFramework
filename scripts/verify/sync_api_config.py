#!/usr/bin/env python3
"""扫描 Controller 文件，重新生成 scripts/verify/api_config.json。"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BACKEND_SRC = REPO_ROOT / "src/backend/src/main/java"
APP_YML = REPO_ROOT / "src/backend/src/main/resources/application.yml"
CONFIG_PATH = Path(__file__).parent / "api_config.json"

METHOD_MAP = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}


def get_port() -> str:
    if APP_YML.exists():
        m = re.search(r"port:\s*(\d+)", APP_YML.read_text())
        if m:
            return m.group(1)
    return "8080"


def first_quoted(s: str) -> str:
    m = re.search(r'"([^"]+)"', s)
    return m.group(1) if m else ""


def scan(file: Path) -> list[dict]:
    text = file.read_text(encoding="utf-8")
    prefix = ""
    cm = re.search(r"@RequestMapping\(([^)]+)\)", text)
    if cm:
        prefix = first_quoted(cm.group(1)).rstrip("/")

    results = []
    for ann, method in METHOD_MAP.items():
        for m in re.finditer(rf"@{ann}\(([^)]*)\)", text):
            path = first_quoted(m.group(1))
            if path:
                results.append({"method": method, "path": f"{prefix}{path}"})
    return results


def actuator_endpoints(port: str) -> list[dict]:
    if not APP_YML.exists():
        return []
    text = APP_YML.read_text()
    eps = []
    if "health" in text:
        eps.append({"url": f"http://localhost:{port}/actuator/health", "method": "GET", "status": 200})
    if "info" in text:
        eps.append({"url": f"http://localhost:{port}/actuator/info", "method": "GET", "status": 200})
    return eps


def main() -> None:
    port = get_port()
    base = f"http://localhost:{port}"

    endpoints: list[dict] = []
    for f in BACKEND_SRC.rglob("*Controller.java"):
        for ep in scan(f):
            endpoints.append({"url": f"{base}{ep['path']}", "method": ep["method"], "status": 200})

    endpoints += actuator_endpoints(port)

    seen: set[tuple] = set()
    unique = []
    for ep in endpoints:
        key = (ep["url"], ep["method"])
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    CONFIG_PATH.write_text(
        json.dumps({"endpoints": unique}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"✅ api_config.json 已更新：{len(unique)} 个端点")


if __name__ == "__main__":
    main()
