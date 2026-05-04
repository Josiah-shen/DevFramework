#!/usr/bin/env python3
"""统一验证管道。

默认 `make validate` 仍走 full，全量保持旧行为；harness 任务验证可传
`--profile standard --scope ...`，按影响范围收敛构建、测试与 HTTP 验证。
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from harness.lib.failure_classifier import is_deterministic_failure  # noqa: E402

MAX_RETRIES = 3
DETERMINISTIC_FAIL_THRESHOLD = 2
PROFILES = {"smoke", "standard", "full"}


def parse_scope(raw_scope: str | None) -> list[str]:
    if not raw_scope:
        return []
    return [p.strip().replace("\\", "/").removeprefix("./") for p in raw_scope.split(",") if p.strip()]


def has_backend(scope: list[str]) -> bool:
    return any(p.startswith("src/backend/") or p.startswith("src/database/") for p in scope)


def has_frontend(scope: list[str]) -> bool:
    return any(p.startswith("src/frontend/") for p in scope)


def has_verify_tooling(scope: list[str]) -> bool:
    return any(
        p == "Makefile"
        or p.startswith("scripts/verify/")
        or p.startswith("harness/")
        or p == "scripts/validate.py"
        for p in scope
    )


def has_python_tests(scope: list[str], folder: str) -> bool:
    return any(p.startswith(f"tests/{folder}/") for p in scope)


# Mapper 文件名 → 业务域精确映射；未在表中的 mapper 回退到全 5 域（兼容性兜底）
_MAPPER_DOMAINS: dict[str, set[str]] = {
    "CarbonEmissionMapper": {"analysis", "statistics"},
    "CarbonPathPlanMapper": {"analysis", "statistics"},
    "CarbonPathDeviceMapper": {"analysis"},
    "CarbonPathPlanDeviceMapper": {"analysis"},
    "CarbonPathPlanStepMapper": {"analysis"},
    "CarbonReportMapper": {"analysis"},
    "CarbonReportVersionMapper": {"analysis"},
    "AccountingModelMapper": {"analysis"},
    "EnergyConsumptionMapper": {"analysis", "statistics"},
    "EmissionFactorMapper": {"basic-data"},
    "OrganizationMapper": {"basic-data"},
    "OrgTypeMapper": {"basic-data"},
    "RegionMapper": {"basic-data"},
    "DataDictMapper": {"basic-data"},
    "EnergyTypeMapper": {"basic-data", "statistics"},
    "PvGenerationMapper": {"pv"},
    "PvProjectMapper": {"pv"},
    "SinkLandMapper": {"sink"},
    "SinkLedgerMapper": {"sink"},
    "WarningEventMapper": {"warning"},
    "WarningRuleMapper": {"warning"},
    "OrgEnergyProfileMapper": {"basic-data", "statistics"},
}


def scope_domains(scope: list[str]) -> set[str]:
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
        # database/ 改动天然跨模块，保留全 5 域
        if item.startswith("src/database/"):
            domains.update({"basic-data", "statistics", "analysis", "pv", "sink"})
        elif "mapper/" in item or "/repository/" in item.replace("\\", "/"):
            # 提取 mapper 文件名（不含扩展名），按精确映射表收敛；未知 mapper 回退到全 5 域
            name = Path(item).stem  # CarbonEmissionMapper.xml → CarbonEmissionMapper
            if name in _MAPPER_DOMAINS:
                domains.update(_MAPPER_DOMAINS[name])
            else:
                domains.update({"basic-data", "statistics", "analysis", "pv", "sink"})
    return domains


# ── E2E scope 化基础设施 ────────────────────────────────────────────────────

E2E_DIR = ROOT / "tests" / "e2e"
ROUTE_MAP_PATH = E2E_DIR / "route_map.json"

# spec 文件名 → 业务关键字（route_map.json 缺失或不全时的兜底；新增 spec 时同步）
_E2E_SPEC_NAME_HINTS: dict[str, list[str]] = {
    "test_screen.py":              ["screen", "screen-dashboard", "screen-statistics",
                                    "screen-analysis", "screen-application"],
    "test_screen_flow.py":         ["screen", "screen-dashboard", "screen-statistics",
                                    "screen-analysis", "screen-application"],
    "test_dashboard.py":           ["screen", "screen-dashboard"],
    "test_carbon_emission_e2e.py": ["carbon-emission", "carbon-path", "accounting-report",
                                    "analysis-report", "multi-dim"],
    "test_basic_data_e2e.py":      ["basic-data", "org-info", "region-mgmt",
                                    "energy-type", "factor-mgmt", "data-dict"],
    "test_basic_data.py":          ["basic-data", "org-info"],
    "test_statistics_e2e.py":      ["statistics", "carbon-emission", "carbon-path",
                                    "accounting-report", "analysis-report",
                                    "multi-dim", "energy-statistics"],
    "test_pv_e2e.py":              ["pv", "pv-ledger", "pv-analysis"],
    "test_warning_e2e.py":         ["warning", "warning-model", "warning-control"],
    "test_sink_e2e.py":            ["sink", "sink-accounting", "sink-analysis"],
}

# 触发"全量降级"的全局副作用文件（基于实际目录扫描，19 项）
#
# 触发后果：scope 命中以下任一路径时，verify 阶段会强制跑全量 e2e（≈200s/轮，
#           可能上百用例），即便 plan 已声明 scope 也无法收敛 e2e 范围。
#
# 维护规则：
#   - 仅当文件**真实影响所有页面导航或全局测试基础设施**时才入列。
#   - 全局副作用：路由表、根布局、HTTP 客户端、全局 utils、conftest、pytest 配置等。
#   - **纯展示文案/单页面 view/admin 子页面不入**——它们只影响局部测试，
#     入列会导致小改动也触发全量 e2e，浪费数十分钟。
#
# 与 R-1 联动：harness/bin/executor.py:cmd_approve 在 plan 的 ## 影响范围 命中
#              本列表时，会向 stderr 输出非阻塞警告（不影响 approve 切档），
#              提醒维护者评估是否拆 commit 或临时降级到 smoke。
_E2E_FULL_FALLBACK_PATHS = (
    "src/frontend/package.json",
    "src/frontend/package-lock.json",
    "src/frontend/index.html",
    "src/frontend/vite.config.js",
    "src/frontend/.eslintrc.cjs",
    "src/frontend/src/main.js",
    "src/frontend/src/App.vue",
    "src/frontend/src/ui/router/",
    "src/frontend/src/ui/layouts/AdminLayout.vue",
    "src/frontend/src/ui/layouts/ScreenLayout.vue",
    "src/frontend/src/core/services/http.js",
    "src/frontend/src/core/services/index.js",
    "src/frontend/src/utils/logger.js",
    "src/frontend/src/utils/format.js",
    "src/frontend/src/utils/url.js",
    "src/frontend/public/",
    "tests/conftest.py",
    "tests/pytest.ini",
    "tests/requirements.txt",
)


def _load_route_map() -> dict:
    """读 tests/e2e/route_map.json；缺失或解析失败返回空 dict。"""
    import json as _json
    if not ROUTE_MAP_PATH.is_file():
        return {}
    try:
        return _json.loads(ROUTE_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _has_e2e_full_fallback(scope: list[str]) -> bool:
    """scope 是否命中触发全量降级的全局副作用路径。"""
    for item in scope:
        norm = item.replace("\\", "/")
        for trigger in _E2E_FULL_FALLBACK_PATHS:
            if norm == trigger or (trigger.endswith("/") and norm.startswith(trigger)):
                return True
    return False


def _e2e_keywords_from_scope(scope: list[str]) -> set[str]:
    """从 scope 路径推业务关键字：views 文件名 kebab-case + scope_domains。"""
    import re as _re
    keywords: set[str] = set()
    for item in scope:
        norm = item.replace("\\", "/")
        if "/views/screen/" in norm:
            keywords.add("screen")
            stem = Path(norm).stem
            kebab = _re.sub(r"(?<!^)(?=[A-Z])", "-", stem).lower()
            keywords.add(kebab)
        elif "/views/admin/" in norm:
            stem = Path(norm).stem
            kebab = _re.sub(r"(?<!^)(?=[A-Z])", "-", stem).lower()
            keywords.add(kebab)
    keywords.update(scope_domains(scope))
    return keywords


def _scope_implies_e2e(scope: list[str]) -> bool:
    """scope 含前端 views 或后端 api 业务文件时即使无 tests/e2e/ 也要触发 e2e。"""
    for p in scope:
        norm = p.replace("\\", "/")
        if norm.startswith("src/frontend/src/ui/views/"):
            return True
        if norm.startswith("src/backend/src/main/java/com/xptsqas/api/"):
            return True
    return False


def e2e_specs_for_scope(scope: list[str]) -> list[str]:
    """根据 scope 推 e2e spec 文件子集。

    返回 ["tests/e2e/test_xxx.py", ...] 子集 或 ["tests/e2e"] 全量降级哨兵。

    优先级：spec 直命中 > 全量降级 > route_map 反查 > 路由片段匹配 >
            文件名启发兜底 > 后端业务域（已并入关键字）。任一空命中走全量降级。
    """
    direct = [p for p in scope if p.startswith("tests/e2e/test_") and p.endswith(".py")]
    full_fallback = _has_e2e_full_fallback(scope)
    if direct and not full_fallback:
        return sorted(set(direct))
    if full_fallback:
        return ["tests/e2e"]

    keywords = _e2e_keywords_from_scope(scope)
    if not keywords:
        return ["tests/e2e"]

    matched: set[str] = set()
    route_map = _load_route_map()
    for spec_name, info in route_map.items():
        spec_kws = set(info.get("keywords") or [])
        spec_routes = info.get("routes") or []
        if spec_kws & keywords:
            matched.add(spec_name)
            continue
        for kw in keywords:
            for r in spec_routes:
                segs = [s for s in r.strip("/").split("/") if s and not s.startswith(":")]
                is_screen = segs and segs[0] == "screen"
                normed = [f"screen-{s}" if is_screen and s != "screen" else s for s in segs]
                if kw in normed:
                    matched.add(spec_name)
                    break
            else:
                continue
            break

    for spec_name, hint_kws in _E2E_SPEC_NAME_HINTS.items():
        if spec_name in matched:
            continue
        if set(hint_kws) & keywords:
            matched.add(spec_name)

    if not matched:
        return ["tests/e2e"]
    return sorted(f"tests/e2e/{name}" for name in matched)


def java_test_names(scope: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in scope:
        path = ROOT / item
        candidates: list[Path] = []
        if item.startswith("src/backend/src/test/") and item.endswith(".java"):
            candidates.append(path)
        elif item.startswith("src/backend/src/main/") and item.endswith(".java"):
            candidates.append(ROOT / "src/backend/src/test/java" / f"{path.stem}Test.java")
        for candidate in candidates:
            if candidate.is_file() and candidate.stem not in seen:
                seen.add(candidate.stem)
                names.append(candidate.stem)
    return names


def _frontend_test_extensions() -> tuple[str, ...]:
    return (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue")


def frontend_files_with_tests(frontend_files: list[str]) -> list[str]:
    """从 src/frontend/ 相对路径列表中筛出"有对应测试文件"的条目。

    判定规则（任一命中即视为有测试）：
    - 改动文件本身就是 `*.test.*` / `*.spec.*`
    - 同目录存在 `<stem>.test.<ext>` / `<stem>.spec.<ext>`
    - 同目录 `__tests__/` 子目录存在 `<stem>.test.<ext>` / `<stem>.spec.<ext>`

    只返回有对应测试的文件，避免 vitest related --run 在无测试时硬失败。
    """
    base = ROOT / "src/frontend"
    matched: list[str] = []
    for rel in frontend_files:
        rel_path = Path(rel)
        stem = rel_path.stem
        # 自身就是测试
        if stem.endswith(".test") or stem.endswith(".spec"):
            matched.append(rel)
            continue
        parent = base / rel_path.parent
        candidates: list[Path] = []
        for kind in ("test", "spec"):
            for ext in _frontend_test_extensions():
                candidates.append(parent / f"{stem}.{kind}{ext}")
                candidates.append(parent / "__tests__" / f"{stem}.{kind}{ext}")
        if any(c.is_file() for c in candidates):
            matched.append(rel)
    return matched


def _scoped_test_steps(scope: list[str]) -> list[tuple[list[str], Optional[list[str]], str]]:
    steps: list[tuple[list[str], Optional[list[str]], str]] = []
    backend = has_backend(scope)
    frontend = has_frontend(scope)

    if backend:
        steps.append((["mvn", "-f", "src/backend/pom.xml", "-q", "-DskipTests", "compile"], None, "backend-compile — 后端编译"))
        tests = java_test_names(scope)
        if tests:
            steps.append((
                ["mvn", "-f", "src/backend/pom.xml", "-q", f"-Dtest={','.join(tests)}", "test"],
                None,
                "backend-tests — 相关后端测试",
            ))

    if frontend:
        steps.append((["npm", "--prefix", "src/frontend", "run", "build", "--silent"], None, "frontend-build — 前端构建"))
        frontend_files = [
            item.removeprefix("src/frontend/")
            for item in scope
            if item.startswith("src/frontend/") and not item.endswith("/")
        ]
        testable = frontend_files_with_tests(frontend_files) if frontend_files else []
        if testable:
            steps.append((
                ["npm", "--prefix", "src/frontend", "run", "test:scope", "--", *testable],
                None,
                "frontend-tests — 前端测试",
            ))
        elif not frontend_files:
            steps.append((
                ["npm", "--prefix", "src/frontend", "run", "test", "--if-present"],
                None,
                "frontend-tests — 前端测试",
            ))

    for folder in ("unit", "integration"):
        if has_python_tests(scope, folder):
            steps.append((
                ["python3", "-m", "pytest", f"tests/{folder}", "-v"],
                None,
                f"python-{folder} — Python {folder} 测试",
            ))

    return steps


def build_steps(profile: str, scope: list[str], slug: str | None = None) -> list[tuple[list[str], Optional[list[str]], str]]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")

    if profile == "full":
        if scope:
            steps: list[tuple[list[str], Optional[list[str]], str]] = [
                (["make", "build"], None, "build     — 代码能否编译"),
                (["make", "lint-arch"], ["make", "fix-arch"], "lint-arch — 架构和质量合规"),
            ]
            steps.extend(_scoped_test_steps(scope))

            if (
                has_python_tests(scope, "e2e")
                or _scope_implies_e2e(scope)
                or _has_e2e_full_fallback(scope)
            ):
                e2e_targets = e2e_specs_for_scope(scope)
                label_suffix = (
                    "（全量降级）" if e2e_targets == ["tests/e2e"]
                    else f"（{len(e2e_targets)} spec）"
                )
                steps.append((
                    ["python3", "-m", "pytest", *e2e_targets, "-v"],
                    None,
                    f"python-e2e-headless — Python e2e 无头{label_suffix}",
                ))
                steps.append((
                    ["python3", "-m", "pytest", *e2e_targets, "-v", "--headed"],
                    None,
                    f"python-e2e-headed — Python e2e 有头{label_suffix}",
                ))

            tooling = has_verify_tooling(scope)
            backend = has_backend(scope)
            frontend = has_frontend(scope)
            if tooling and not (backend or frontend):
                steps.append((["python3", "-m", "pytest", "--noconftest", "harness/tests", "-v"], None, "harness-tests — 验证工具测试"))

            verify_cmd = ["python3", "scripts/verify/run.py", "--scope", ",".join(scope)]
            if slug:
                verify_cmd.extend(["--slug", slug])
            steps.append((verify_cmd, None, "verify-full — scoped verify"))
            return steps
        else:
            e2e_targets = ["tests/e2e"]
            return [
                (["make", "build"], None, "build     — 代码能否编译"),
                (["make", "lint-arch"], ["make", "fix-arch"], "lint-arch — 架构和质量合规"),
                (["make", "test-backend"], None, "backend-tests — 后端单元/集成测试"),
                (["make", "test-frontend"], None, "frontend-tests — 前端单元测试"),
                (["make", "test-python-unit"], None, "python-unit — Python 单元测试"),
                (["make", "test-python-integration"], None, "python-integration — Python 集成测试"),
                (["python3", "-m", "pytest", *e2e_targets, "-v"],
                 None, f"python-e2e-headless — Python e2e 无头（scoped {len(e2e_targets)} spec）"),
                (["python3", "-m", "pytest", *e2e_targets, "-v", "--headed"],
                 None, f"python-e2e-headed — Python e2e 有头（scoped {len(e2e_targets)} spec）"),
                (["make", "verify"], None, "verify    — 端到端功能验证"),
            ]

    if profile == "smoke":
        smoke_scope = scope if scope else ["."]
        verify_cmd = ["python3", "scripts/verify/run.py", "--scope", ",".join(smoke_scope)]
        if slug:
            verify_cmd.extend(["--slug", slug])
        verify_cmd.extend(["--checks", "arch,style"])
        return [(verify_cmd, None, "verify-smoke — 静态检查（arch + style）")]

    if not scope:
        scope = ["."]

    steps: list[tuple[list[str], Optional[list[str]], str]] = []
    scoped_tests = _scoped_test_steps(scope)
    if scoped_tests:
        steps.extend(scoped_tests)
    else:
        steps.append((["make", "lint-arch"], ["make", "fix-arch"], "lint-arch — 架构和质量合规"))

    tooling = has_verify_tooling(scope)
    backend = has_backend(scope)
    frontend = has_frontend(scope)

    if (
        has_python_tests(scope, "e2e")
        or _scope_implies_e2e(scope)
        or _has_e2e_full_fallback(scope)
    ):
        e2e_targets = e2e_specs_for_scope(scope)
        label_suffix = (
            "（全量降级）" if e2e_targets == ["tests/e2e"]
            else f"（{len(e2e_targets)} spec）"
        )
        steps.append((
            ["python3", "-m", "pytest", *e2e_targets, "-v"],
            None,
            f"python-e2e-headless — Python e2e 无头{label_suffix}",
        ))
        steps.append((
            ["python3", "-m", "pytest", *e2e_targets, "-v", "--headed"],
            None,
            f"python-e2e-headed — Python e2e 有头{label_suffix}",
        ))

    if tooling and not (backend or frontend):
        steps.append((["python3", "-m", "pytest", "--noconftest", "harness/tests", "-v"], None, "harness-tests — 验证工具测试"))

    verify_cmd = ["python3", "scripts/verify/run.py", "--scope", ",".join(scope)]
    if slug:
        verify_cmd.extend(["--slug", slug])
    steps.append((verify_cmd, None, f"verify-{profile} — scoped verify"))
    return steps


def run(cmd: List[str], label: str, suppress_output: bool = False) -> tuple[bool, str]:
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if output and not suppress_output:
        print(output)
    return result.returncode == 0, output


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:12]


def run_with_fix(step_cmd: List[str], fix_cmd: Optional[List[str]], label: str) -> bool:
    last_fp: Optional[str] = None
    repeat_count = 0

    for attempt in range(1, MAX_RETRIES + 2):
        suppress = last_fp is not None and repeat_count >= 1
        ok, output = run(
            step_cmd,
            label if attempt == 1 else f"{label}（第 {attempt - 1} 轮重试）",
            suppress_output=suppress,
        )
        if ok:
            return True

        det_reason = is_deterministic_failure(output)
        if det_reason is not None:
            remaining = MAX_RETRIES + 1 - attempt
            print(f"\n[verify] 检测到确定性失败（reason={det_reason}），跳过剩余重试 {remaining}/{MAX_RETRIES}。")
            print(f"\n🛑 [{label}] 确定性失败，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   错误摘要:\n{_indent(output)}")
            return False

        fp = fingerprint(output)
        if fp == last_fp:
            repeat_count += 1
            print(f"\n[retry-skip-candidate] 指纹 {fp} 与上一轮一致（连续 {repeat_count} 轮）")
        else:
            repeat_count = 1
            last_fp = fp

        if repeat_count >= DETERMINISTIC_FAIL_THRESHOLD:
            remaining = MAX_RETRIES + 1 - attempt
            print(f"\n[retry-skip] 检测到确定性失败（指纹 {fp}），跳过剩余重试 {remaining}/{MAX_RETRIES}")
            print(f"\n🛑 [{label}] 确定性失败，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   错误摘要（仅首轮完整保留）:\n{_indent(output)}")
            return False

        if attempt > MAX_RETRIES:
            print(f"\n🛑 [{label}] 已跑满 {MAX_RETRIES} 轮修复仍未通过，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   最终错误摘要:\n{_indent(output)}")
            return False

        if fix_cmd:
            print(f"\n🔧 [{label}] 第 {attempt} 轮修复：{' '.join(fix_cmd)}")
            subprocess.run(fix_cmd, cwd=ROOT)
        else:
            print(f"\n⚠️  [{label}] 无自动修复命令，跳过修复直接重试（若下一轮指纹重复将早退）")

    return False


def _indent(text: str, prefix: str = "     ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _backend_available(host: str = "127.0.0.1", port: int = 8088, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def needs_backend(step_cmd: list[str], profile: str, scope: list[str]) -> bool:
    if profile == "smoke":
        return False
    if step_cmd[:2] == ["make", "verify"]:
        return True
    if step_cmd[:2] == ["python3", "scripts/verify/run.py"]:
        checks = ""
        if "--checks" in step_cmd:
            checks = step_cmd[step_cmd.index("--checks") + 1]
        if checks and "api" not in checks and "e2e" not in checks:
            return False
        return bool(scope_domains(scope))
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一验证管道")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="full")
    parser.add_argument("--scope", default=None, help="逗号分隔的影响范围路径")
    parser.add_argument("--slug", default=None)
    args = parser.parse_args(argv)

    scope = parse_scope(args.scope)
    steps = build_steps(args.profile, scope, args.slug)
    print(f"ℹ️  validate profile={args.profile}, scope={len(scope)}")

    # full profile 也透传 scope：通过环境变量注入到 `make verify` → verify/run.py，
    # 范围外历史违规降级为 warning（与 standard 一致），范围内违规仍硬失败。
    # `make verify` 不接 CLI scope 参数，env 是唯一稳定通道；verify/run.py 已支持 HARNESS_VERIFY_SCOPE。
    # 用户显式跑 `make verify`（无 scope 参数）仍走全仓硬失败，向后兼容。
    import os as _os
    if args.profile == "full" and scope:
        _os.environ["HARNESS_VERIFY_SCOPE"] = ",".join(scope)
        if args.slug:
            _os.environ["HARNESS_SLUG"] = args.slug
        print(f"ℹ️  full profile 透传 scope（{len(scope)} 条）至 make verify，范围外历史违规降级为 ⚠️")

    for step_cmd, fix_cmd, label in steps:
        if needs_backend(step_cmd, args.profile, scope) and not _backend_available():
            print("\n❌ 后端未在 127.0.0.1:8088 监听，无法执行业务路径验证。")
            print("   请先启动后端（参考 docs/DEVELOPMENT.md → 验证前置条件）：")
            print("     cd src/backend && mvn spring-boot:run")
            print("   或使用 smoke 档：python3 scripts/validate.py --profile smoke --scope <paths>")
            return 1
        if not run_with_fix(step_cmd, fix_cmd, label):
            print(f"\n❌ 管道终止于 [{label.split('—')[0].strip()}]")
            return 1

    print("\n✅ 所有验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())