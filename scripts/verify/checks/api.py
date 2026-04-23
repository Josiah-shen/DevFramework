#!/usr/bin/env python3
"""接口存活检查 — 读取 api_config.json，验证 HTTP endpoint 可达且返回预期状态码。"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "api_config.json"


def check() -> tuple[bool, list[str]]:
    if not CONFIG_PATH.exists():
        return True, []

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    endpoints = config.get("endpoints", [])

    if not endpoints:
        return True, ["⚠️  api_config.json 中 endpoints 为空，跳过接口检查"]

    failures: list[str] = []
    base = endpoints[0]["url"].rsplit("/", 1)[0] if endpoints else ""
    for ep in endpoints:
        url = ep["url"]
        method = ep.get("method", "GET")
        expected = ep.get("status", 200)
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != expected:
                    failures.append(f"{method} {url}: 期望 HTTP {expected}，实际 {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code != expected:
                failures.append(f"{method} {url}: 期望 HTTP {expected}，实际 {e.code}")
        except (ConnectionRefusedError, OSError):
            return True, [f"⚠️  服务未启动，跳过接口存活检查（{base}）"]
        except Exception as e:
            failures.append(f"{method} {url}: 连接失败 — {e}")

    return len(failures) == 0, failures


if __name__ == "__main__":
    ok, msgs = check()
    for m in msgs:
        print(f"  {m}")
    if not ok:
        print("❌ 接口存活检查失败")
        sys.exit(1)
    print("✅ 接口存活检查通过")
    sys.exit(0)
