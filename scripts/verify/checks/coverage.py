#!/usr/bin/env python3
"""自动化覆盖漂移检查：Vue 路由与 Controller 端点是否进入 verify 覆盖网。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
RULE_ID = "testing/verify-coverage-drift"
ROUTE_MAP_PATH = ROOT / "tests" / "e2e" / "route_map.json"
API_CONFIG_PATH = ROOT / "scripts" / "verify" / "api_config.json"
E2E_CONFIG_PATH = ROOT / "scripts" / "verify" / "e2e_config.json"
BACKEND_SRC = ROOT / "src" / "backend" / "src" / "main" / "java"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _normalize_scope(scope) -> set[str] | None:
    if scope is None:
        return None
    return {item.replace("\\", "/").removeprefix("./") for item in scope}


def _load_route_builder():
    return _load_module(
        "build_e2e_route_map_for_coverage",
        ROOT / "harness" / "bin" / "build_e2e_route_map.py",
    )


def _route_targets_for_scope(scope: set[str] | None, vue_routes: dict[str, str]) -> dict[str, str]:
    if scope is None:
        return vue_routes
    if any(
        item == "tests/e2e/route_map.json"
        or item.startswith("tests/e2e/")
        or item == "src/frontend/src/ui/router/index.js"
        for item in scope
    ):
        return vue_routes
    return {
        route: component
        for route, component in vue_routes.items()
        if component in scope
    }


def check_vue_route_coverage(scope=None) -> list[str]:
    builder = _load_route_builder()
    if builder is None:
        return []
    vue_routes = builder.extract_vue_routes_from_router()
    route_map = _load_json(ROUTE_MAP_PATH)
    targets = _route_targets_for_scope(_normalize_scope(scope), vue_routes)
    missing = builder.uncovered_vue_routes(targets, route_map)
    return [
        f"[{RULE_ID}] {route}:0 — Vue 路由未被 tests/e2e/route_map.json 覆盖"
        f"（component={component}）；建议：在对应 e2e spec 的 pytest.mark.routes(...)"
        f" 添加该路由并运行 `python3 harness/bin/build_e2e_route_map.py`"
        for route, component in missing.items()
    ]


def _load_sync_api_config():
    return _load_module(
        "sync_api_config_for_coverage",
        ROOT / "scripts" / "verify" / "sync_api_config.py",
    )


def _api_path(path: str) -> str:
    normalized = "/" + path.strip("/")
    if normalized.startswith("/api/") or normalized == "/api":
        return normalized
    return "/api" + normalized


def _controller_files_for_scope(scope: set[str] | None) -> list[Path]:
    if scope is None:
        return sorted(BACKEND_SRC.rglob("*Controller.java"))
    files: list[Path] = []
    for item in sorted(scope):
        if (
            item.startswith("src/backend/src/main/java/com/xptsqas/api/")
            and item.endswith("Controller.java")
        ):
            path = ROOT / item
            if path.is_file():
                files.append(path)
    return files


def controller_endpoints(scope=None) -> dict[tuple[str, str], str]:
    sync = _load_sync_api_config()
    if sync is None:
        return {}
    endpoints: dict[tuple[str, str], str] = {}
    for file in _controller_files_for_scope(_normalize_scope(scope)):
        for endpoint in sync.scan(file):
            method = endpoint.get("method", "GET")
            path = _api_path(endpoint.get("path", ""))
            endpoints[(method, path)] = str(file.relative_to(ROOT)).replace("\\", "/")
    return dict(sorted(endpoints.items()))


def api_config_endpoints() -> set[tuple[str, str]]:
    config = _load_json(API_CONFIG_PATH)
    endpoints: set[tuple[str, str]] = set()
    for endpoint in config.get("endpoints") or []:
        url = endpoint.get("url", "")
        method = endpoint.get("method", "GET")
        path = urlparse(url).path
        if path:
            endpoints.add((method, path))
    return endpoints


def e2e_config_endpoints() -> set[tuple[str, str]]:
    config = _load_json(E2E_CONFIG_PATH)
    endpoints: set[tuple[str, str]] = set()
    for scenario in config.get("scenarios") or []:
        for step in scenario.get("steps") or []:
            path = step.get("path", "")
            method = step.get("method", "GET")
            if path:
                endpoints.add((method, path))
    return endpoints


def check_endpoint_coverage(scope=None) -> list[str]:
    endpoints = controller_endpoints(scope)
    if not endpoints:
        return []
    api_endpoints = api_config_endpoints()
    e2e_endpoints = e2e_config_endpoints()
    messages: list[str] = []
    for endpoint, source in endpoints.items():
        method, path = endpoint
        if endpoint not in api_endpoints:
            messages.append(
                f"[{RULE_ID}] {source}:0 — Controller 端点 {method} {path}"
                f" 未进入 scripts/verify/api_config.json；建议运行同步脚本或补配置"
            )
        if endpoint not in e2e_endpoints:
            messages.append(
                f"[{RULE_ID}] {source}:0 — Controller 端点 {method} {path}"
                f" 未进入 scripts/verify/e2e_config.json 业务路径；建议补充对应场景步骤"
            )
    return messages


def check(scope=None) -> list[str]:
    return check_vue_route_coverage(scope) + check_endpoint_coverage(scope)