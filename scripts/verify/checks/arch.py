#!/usr/bin/env python3
"""架构合规检查 — 通过 importlib 复用 lint-deps.py 的层级依赖规则。"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent


def _load_lint_deps():
    spec = importlib.util.spec_from_file_location(
        "lint_deps",
        ROOT / "scripts" / "lint-deps.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check() -> tuple[bool, list[str]]:
    mod = _load_lint_deps()
    violations: list[str] = []
    for ext in mod.EXTENSIONS:
        for path in ROOT.rglob(f"*{ext}"):
            if any(p in mod.SKIP_DIRS for p in path.parts):
                continue
            violations.extend(mod.check_file(path))
    return len(violations) == 0, violations


if __name__ == "__main__":
    ok, msgs = check()
    if msgs:
        print("❌ 架构合规检查失败：")
        for m in msgs:
            print(f"  {m}")
        sys.exit(1)
    print("✅ 架构合规检查通过")
    sys.exit(0)