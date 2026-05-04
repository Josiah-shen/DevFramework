#!/usr/bin/env python3
"""扫描 tests/e2e/test_*.py 模块级 pytestmark 中的 pytest.mark.routes(...)，
全量重写 tests/e2e/route_map.json 反向索引。

由 hook 在 e2e spec 写入后自动调用，也可手动 `python3 harness/bin/build_e2e_route_map.py`。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
E2E_DIR = ROOT / "tests" / "e2e"
OUT = E2E_DIR / "route_map.json"
ROUTER_PATH = ROOT / "src" / "frontend" / "src" / "ui" / "router" / "index.js"


def _is_routes_call(call: ast.Call) -> bool:
    """识别 pytest.mark.routes(...) 调用。"""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "routes":
        return False
    parent = func.value
    return isinstance(parent, ast.Attribute) and parent.attr == "mark"


def extract_routes_from_module(path: Path) -> list[str]:
    """ast 解析模块级 pytestmark，抽 routes 字符串字面量。

    支持：
      pytestmark = pytest.mark.routes("/a", "/b")
      pytestmark = [pytest.mark.e2e, pytest.mark.routes(...)]
    spec 语法错或 IO 失败时返回空 list（不抛）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []

    routes: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        candidates: list[ast.expr] = (
            list(node.value.elts) if isinstance(node.value, ast.List) else [node.value]
        )
        for call in candidates:
            if isinstance(call, ast.Call) and _is_routes_call(call):
                for arg in call.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        routes.append(arg.value)
    seen: set[str] = set()
    out: list[str] = []
    for r in routes:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def routes_to_keywords(routes: list[str]) -> list[str]:
    """从 route 字符串抽业务关键字：按 / 切段、剥 # 前缀、跳过 :param、转小写。

    /screen/* 路由的子段加 ``screen-`` 前缀，避免与后端域关键字冲突。
    """
    keywords: set[str] = set()
    for r in routes:
        cleaned = r.lstrip("#").strip("/")
        segments = [s.lower() for s in cleaned.split("/") if s and not s.startswith(":")]
        is_screen = segments and segments[0] == "screen"
        for seg in segments:
            if is_screen and seg != "screen":
                keywords.add(f"screen-{seg}")
            else:
                keywords.add(seg)
    return sorted(keywords)


def _repo_path_for_component(component: str) -> str:
    base = ROOT / "src" / "frontend" / "src" / "ui" / "router"
    return str((base / component).resolve().relative_to(ROOT)).replace("\\", "/")


def _route_for_component(path: str, component: str) -> str | None:
    child = path.strip("/")
    if component.startswith("../views/admin/"):
        return f"/admin/{child}" if child else "/admin"
    if component.startswith("../views/screen/"):
        return f"/screen/{child}" if child else "/screen"
    if component == "../views/Home.vue" and path == "/":
        return "/"
    return None


def extract_vue_routes_from_router(path: Path = ROUTER_PATH) -> dict[str, str]:
    """提取当前静态 Vue router 中的可访问页面路由，返回 route -> component repo path。"""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"path:\s*['\"]([^'\"]+)['\"][^{}\n]*component:\s*\(\)\s*=>\s*import\(['\"]([^'\"]+)['\"]\)"
    )
    routes: dict[str, str] = {}
    for match in pattern.finditer(text):
        raw_path, component = match.groups()
        if raw_path.startswith("/:"):
            continue
        route = _route_for_component(raw_path, component)
        if route:
            routes[route] = _repo_path_for_component(component)
    return dict(sorted(routes.items()))


def covered_routes(route_map: dict[str, Any]) -> set[str]:
    routes: set[str] = set()
    for info in route_map.values():
        for route in info.get("routes") or []:
            routes.add(route)
    return routes


def uncovered_vue_routes(
    vue_routes: dict[str, str],
    route_map: dict[str, Any],
) -> dict[str, str]:
    covered = covered_routes(route_map)
    return {route: component for route, component in vue_routes.items() if route not in covered}


def main() -> int:
    if not E2E_DIR.is_dir():
        print(f"[route-map] {E2E_DIR} 不存在，跳过")
        return 0
    result: dict[str, dict[str, list[str]]] = {}
    for spec in sorted(E2E_DIR.glob("test_*.py")):
        routes = extract_routes_from_module(spec)
        result[spec.name] = {
            "routes": routes,
            "keywords": routes_to_keywords(routes),
        }
    OUT.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = len(result)
    with_routes = sum(1 for v in result.values() if v["routes"])
    print(
        f"[route-map] 写入 {OUT.relative_to(ROOT)}（{total} specs，{with_routes} 含 routes）"
    )
    missing = uncovered_vue_routes(extract_vue_routes_from_router(), result)
    if missing:
        print(f"[route-map] WARN {len(missing)} 个 Vue 路由未被 e2e spec 标记覆盖：")
        for route, component in missing.items():
            print(f"  - {route} ({component})")
    return 0


if __name__ == "__main__":
    sys.exit(main())