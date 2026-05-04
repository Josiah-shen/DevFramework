#!/usr/bin/env python3
"""接口存活检查 — 读取 api_config.json，验证 HTTP endpoint 可达且返回预期状态码。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

CONFIG_PATH = Path(__file__).parent.parent / "api_config.json"


def _domains_from_scope(scope) -> set[str]:
    if scope is None:
        return set()
    domains: set[str] = set()
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
    for item in scope:
        parts = item.replace("\\", "/").split("/")
        for part in parts:
            key = part.lower()
            if key in mapping:
                domains.add(mapping[key])
        if item.startswith("src/database/") or "mapper/" in item:
            domains.update({"basic-data", "statistics", "analysis", "pv", "sink"})
    return domains


def _endpoint_domain(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if path.startswith("api/"):
        path = path[4:]
    return path.split("/", 1)[0] if path else ""


def _filter_endpoints(endpoints: list[dict], scope) -> tuple[list[dict], list[str]]:
    domains = _domains_from_scope(scope)
    if scope is None:
        return endpoints, []
    if not domains:
        return [], []
    filtered = [ep for ep in endpoints if _endpoint_domain(ep.get("url", "")) in domains]
    return filtered, sorted(domains)


def check(scope=None) -> tuple[bool, list[str]]:
    if not CONFIG_PATH.exists():
        return True, []

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    endpoints = config.get("endpoints", [])
    endpoints, domains = _filter_endpoints(endpoints, scope)

    if not endpoints:
        if domains:
            return True, [f"⚠️  scope domains={','.join(domains)} 未匹配到接口，跳过接口检查"]
        if scope is not None:
            return True, ["⚠️  当前 scope 未推导出接口域，跳过接口检查"]
        return True, ["⚠️  api_config.json 中 endpoints 为空，跳过接口检查"]

    failures: list[str] = []
    base = endpoints[0]["url"].rsplit("/", 1)[0] if endpoints else ""
    messages: list[str] = []
    if domains:
        messages.append(f"ℹ️  接口 scope domains={','.join(domains)}，检查 {len(endpoints)} 个端点")
    for ep in endpoints:
        url = ep["url"]
        method = ep.get("method", "GET")
        expected = ep.get("status", 200)
        try:
            data = None
            headers = {}
            if "body" in ep:
                data = json.dumps(ep["body"]).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif method in ("POST", "PUT", "PATCH"):
                data = b"{}"
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != expected:
                    failures.append(f"{method} {url}: 期望 HTTP {expected}，实际 {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code != expected:
                failures.append(f"{method} {url}: 期望 HTTP {expected}，实际 {e.code}")
        except (ConnectionRefusedError, OSError):
            return False, [f"❌ 服务未启动（{base}），无法验证接口存活"]
        except Exception as e:
            failures.append(f"{method} {url}: 连接失败 — {e}")

    return len(failures) == 0, messages + failures


if __name__ == "__main__":
    ok, msgs = check()
    for m in msgs:
        print(f"  {m}")
    if not ok:
        print("❌ 接口存活检查失败")
        sys.exit(1)
    print("✅ 接口存活检查通过")
    sys.exit(0)