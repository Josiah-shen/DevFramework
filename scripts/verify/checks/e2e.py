#!/usr/bin/env python3
"""业务路径端到端验证 — 读取 e2e_config.json，验证关键业务路径。"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).parent.parent / "e2e_config.json"
LOGGER = logging.getLogger(__name__)
TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
FULL_TEMPLATE_RE = re.compile(r"^\$\{([^}]+)\}$")
MISSING = object()


def _domains_from_scope(scope) -> set[str]:
    if scope is None:
        return set()
    mapping = {
        "basicdata": "basic-data",
        "application": "application",
        "pv": "pv",
        "sink": "sink",
        "model": "model",
        "analysis": "analysis",
        "statistics": "statistics",
        "dashboard": "dashboard",
        "factor": "factor",
        "file": "file",
    }
    domains: set[str] = set()
    for item in scope:
        parts = item.replace("\\", "/").split("/")
        for part in parts:
            key = part.lower()
            if key in mapping:
                domains.add(mapping[key])
        if item.startswith("src/database/") or "mapper/" in item:
            domains.update({"basic-data", "statistics", "analysis", "pv", "sink"})
    return domains


def _step_domain(path: str) -> str:
    normalized = path.strip("/")
    if normalized.startswith("api/"):
        normalized = normalized[4:]
    return normalized.split("/", 1)[0] if normalized else ""


def _filter_scenarios(scenarios: list[dict], scope) -> tuple[list[dict], list[str]]:
    domains = _domains_from_scope(scope)
    if scope is None:
        return scenarios, []
    if not domains:
        return [], []
    filtered: list[dict] = []
    for scenario in scenarios:
        steps = [step for step in scenario.get("steps", []) if _step_domain(step.get("path", "")) in domains]
        if steps:
            copy = dict(scenario)
            copy["steps"] = steps
            filtered.append(copy)
    return filtered, sorted(domains)


def _ctx_value(ctx: dict, name: str) -> Any:
    if name not in ctx:
        raise KeyError(f"未定义变量 {name}")
    return ctx[name]


def _resolve_template(value, ctx: dict) -> Any:
    if isinstance(value, str):
        full_match = FULL_TEMPLATE_RE.fullmatch(value)
        if full_match:
            return _ctx_value(ctx, full_match.group(1))

        def replace(match: re.Match) -> str:
            return str(_ctx_value(ctx, match.group(1)))

        return TEMPLATE_RE.sub(replace, value)
    if isinstance(value, list):
        return [_resolve_template(item, ctx) for item in value]
    if isinstance(value, dict):
        return {
            _resolve_template(key, ctx) if isinstance(key, str) else key: _resolve_template(
                val, ctx
            )
            for key, val in value.items()
        }
    return value


def _value_from_path(value, path: str):
    if isinstance(value, dict) and path in value:
        return value[path]
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return MISSING
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def _extract_to_ctx(response_body_dict, extract_spec, ctx) -> None:
    for ctx_key, path in extract_spec.items():
        value = _value_from_path(response_body_dict, path)
        if value is MISSING:
            LOGGER.warning("e2e_extract_missing_path target=%s path=%s", ctx_key, path)
            continue
        ctx[ctx_key] = value


def _step_desc(step: dict) -> str:
    return step.get("description", step.get("path", "未命名步骤"))


def _decode_body(body_bytes: bytes):
    if not body_bytes:
        return {}
    return json.loads(body_bytes.decode())


def _header_value(headers, key: str):
    if hasattr(headers, "get"):
        value = headers.get(key)
        if value is not None:
            return value
    if hasattr(headers, "items"):
        for header_key, value in headers.items():
            if str(header_key).lower() == key.lower():
                return value
    return None


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _run_step(base_url: str, step: dict, ctx: dict | None = None) -> "str | None":
    ctx = ctx if ctx is not None else {}
    desc = _step_desc(step)
    method = step.get("method", "GET").upper()
    path = _resolve_template(step["path"], ctx)
    url = base_url.rstrip("/") + str(path)
    expected_status = step.get("expect_status", 200)

    data = None
    headers = {}
    if "body" in step:
        data = json.dumps(_resolve_template(step["body"], ctx)).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method in ("POST", "PUT", "PATCH"):
        data = b"{}"
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            actual = resp.status
            body_bytes = resp.read()
            response_headers = getattr(resp, "headers", {})
    except urllib.error.HTTPError as e:
        actual = e.code
        body_bytes = e.read()
        response_headers = e.headers or {}

    if actual != expected_status:
        return f"{desc}: 期望 HTTP {expected_status}，实际 {actual}"

    body = None
    if "expect_body_contains" in step:
        body = _decode_body(body_bytes)
        expected_body = _resolve_template(step["expect_body_contains"], ctx)
        for key, val in expected_body.items():
            actual_value = _value_from_path(body, str(key))
            if actual_value is MISSING:
                return f"{desc}: 响应体 {key!r} 期望 {val!r}，实际 None"
            if actual_value != val:
                return f"{desc}: 响应体 {key!r} 期望 {val!r}，实际 {actual_value!r}"

    if "expect_headers_contains" in step:
        for key, val in step["expect_headers_contains"].items():
            actual_header = _header_value(response_headers, key)
            if actual_header is None or str(val) not in str(actual_header):
                return f"{desc}: 响应头 {key!r} 期望包含 {val!r}，实际 {actual_header!r}"

    if "extract" in step:
        if body is None:
            body = _decode_body(body_bytes)
        _extract_to_ctx(body, step["extract"], ctx)

    return None


def _run_primary_steps(
    name: str,
    base_url: str,
    steps: list[dict],
    ctx: dict,
    failures: list[str],
    stop_on_failure: bool,
) -> bool:
    ok = True
    for step in steps:
        try:
            err = _run_step(base_url, step, ctx)
        except (ConnectionRefusedError, OSError):
            raise
        except Exception as e:
            failures.append(f"[{name}] {_step_desc(step)}: {_exception_message(e)}")
            ok = False
            if stop_on_failure:
                return False
            continue
        if err:
            failures.append(f"[{name}] {err}")
            ok = False
            if stop_on_failure:
                return False
    return ok


def _run_teardown_steps(
    name: str, base_url: str, steps: list[dict], ctx: dict, failures: list[str]
) -> None:
    for step in steps:
        try:
            err = _run_step(base_url, step, ctx)
        except Exception as e:
            failures.append(f"[{name}] {_step_desc(step)}: {_exception_message(e)}")
            continue
        if err:
            failures.append(f"[{name}] {err}")


def _run_scenario(scenario: dict, base_url: str, failures: list[str]) -> None:
    name = scenario.get("name", "未命名场景")
    ctx: dict = {}
    try:
        setup_ok = _run_primary_steps(
            name, base_url, scenario.get("setup_steps", []), ctx, failures, True
        )
        if setup_ok:
            _run_primary_steps(
                name, base_url, scenario.get("steps", []), ctx, failures, False
            )
    finally:
        _run_teardown_steps(name, base_url, scenario.get("teardown_steps", []), ctx, failures)


def check(scope=None) -> tuple[bool, list[str]]:
    if not CONFIG_PATH.exists():
        return False, ["❌ e2e_config.json 不存在，请触发 e2e-updater agent 生成"]

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    scenarios = config.get("scenarios", [])
    base_url = config.get("base_url", "http://localhost:8088")
    scenarios, domains = _filter_scenarios(scenarios, scope)

    if not scenarios:
        if domains:
            return True, [f"⚠️  scope domains={','.join(domains)} 未匹配到业务场景，跳过业务路径"]
        if scope is not None:
            return True, ["⚠️  当前 scope 未推导出业务域，跳过业务路径"]
        return False, ["❌ 无端到端场景，请检查 e2e_config.json 或重新触发 e2e-updater"]

    failures: list[str] = []
    messages: list[str] = []
    if domains:
        step_count = sum(len(s.get("steps", [])) for s in scenarios)
        messages.append(f"ℹ️  业务路径 scope domains={','.join(domains)}，检查 {step_count} 个步骤")

    for scenario in scenarios:
        try:
            _run_scenario(scenario, base_url, failures)
        except (ConnectionRefusedError, OSError):
            return False, [f"❌ 服务未启动（{base_url}），无法验证业务路径"]

    return len(failures) == 0, messages + failures


if __name__ == "__main__":
    ok, msgs = check()
    for m in msgs:
        sys.stdout.write(f"  {m}\n")
    if not ok:
        sys.stdout.write("❌ 业务路径验证失败\n")
        sys.exit(1)
    sys.stdout.write("✅ 业务路径验证通过\n")
    sys.exit(0)