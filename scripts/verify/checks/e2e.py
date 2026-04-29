#!/usr/bin/env python3
"""业务路径端到端验证 — 读取 e2e_config.json，验证关键业务路径。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "e2e_config.json"


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


def _run_step(base_url: str, step: dict) -> "str | None":
    desc = step.get("description", step["path"])
    method = step.get("method", "GET")
    url = base_url.rstrip("/") + step["path"]
    expected_status = step.get("expect_status", 200)

    data = None
    headers = {}
    if "body" in step:
        data = json.dumps(step["body"]).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method in ("POST", "PUT", "PATCH"):
        data = b"{}"
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        actual = resp.status
        body_bytes = resp.read()

    if actual != expected_status:
        return f"{desc}: 期望 HTTP {expected_status}，实际 {actual}"

    if "expect_body_contains" in step:
        body = json.loads(body_bytes.decode())
        for key, val in step["expect_body_contains"].items():
            if body.get(key) != val:
                return f"{desc}: 响应体 {key!r} 期望 {val!r}，实际 {body.get(key)!r}"

    return None


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
        name = scenario.get("name", "未命名场景")
        for step in scenario.get("steps", []):
            try:
                err = _run_step(base_url, step)
                if err:
                    failures.append(f"[{name}] {err}")
            except (ConnectionRefusedError, OSError):
                return False, [f"❌ 服务未启动（{base_url}），无法验证业务路径"]
            except urllib.error.HTTPError as e:
                expected = step.get("expect_status", 200)
                if e.code != expected:
                    failures.append(f"[{name}] {step.get('description', step['path'])}: 期望 {expected}，实际 {e.code}")
            except Exception as e:
                failures.append(f"[{name}] {step.get('description', step['path'])}: {e}")

    return len(failures) == 0, messages + failures


if __name__ == "__main__":
    ok, msgs = check()
    for m in msgs:
        print(f"  {m}")
    if not ok:
        print("❌ 业务路径验证失败")
        sys.exit(1)
    print("✅ 业务路径验证通过")
    sys.exit(0)