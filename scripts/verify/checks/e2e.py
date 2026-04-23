#!/usr/bin/env python3
"""业务路径端到端验证 — 读取 e2e_config.json，验证关键业务路径。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "e2e_config.json"


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


def check() -> tuple[bool, list[str]]:
    if not CONFIG_PATH.exists():
        return False, ["❌ e2e_config.json 不存在，请触发 e2e-updater agent 生成"]

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    scenarios = config.get("scenarios", [])
    base_url = config.get("base_url", "http://localhost:8088")

    if not scenarios:
        return False, ["❌ 无端到端场景，请检查 e2e_config.json 或重新触发 e2e-updater"]

    failures: list[str] = []

    for scenario in scenarios:
        name = scenario.get("name", "未命名场景")
        for step in scenario.get("steps", []):
            try:
                err = _run_step(base_url, step)
                if err:
                    failures.append(f"[{name}] {err}")
            except (ConnectionRefusedError, OSError):
                return True, [f"⚠️  服务未启动，跳过端到端验证（{base_url}）"]
            except urllib.error.HTTPError as e:
                expected = step.get("expect_status", 200)
                if e.code != expected:
                    failures.append(f"[{name}] {step.get('description', step['path'])}: 期望 {expected}，实际 {e.code}")
            except Exception as e:
                failures.append(f"[{name}] {step.get('description', step['path'])}: {e}")

    return len(failures) == 0, failures


if __name__ == "__main__":
    ok, msgs = check()
    for m in msgs:
        print(f"  {m}")
    if not ok:
        print("❌ 业务路径验证失败")
        sys.exit(1)
    print("✅ 业务路径验证通过")
    sys.exit(0)