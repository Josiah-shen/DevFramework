#!/usr/bin/env python3
"""check-scope — 防止 worktree 提交范围漂移。

规则 ID：boundary/worktree-scope-drift
阶段：warning（非阻塞）
晋升条件：连续 10 次构建零误报后由 executor-lint-rule 升级为 error 级别。

背景
----
controller-split review 发现 `scripts/verify/api_config.json`、`e2e_config.json`
等与任务无关的文件在 worktree 中被改动并随提交带入主分支；依靠人工对比
`harness/exec-plans/<slug>.md` 的"受影响文件"声明才捕获。本规则把这项人工对比
自动化：

1. 从 `harness/exec-plans/<slug>.md` 的 `## 影响范围` 段提取声明的文件集合。
2. 通过 `git diff --name-only <base>..HEAD` + `git status --porcelain` 采集实际改动。
3. 计算 "实际改动 \\ 声明集合" 差集；非空则输出规则化告警。

warning 阶段永远返回 exit 0，告警写到 stderr；run.py 以 [pre-existing debt] 语义
展示给 Claude，不阻断管道。

用法
----
    python3 scripts/verify/check-scope.py --slug <slug> [--base main]
    HARNESS_SLUG=<slug> python3 scripts/verify/check-scope.py
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
RULE_ID = "boundary/worktree-scope-drift"

_SCOPE_FILE_LINE_RE = re.compile(r"`(src/[^`]+?)`|`([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)`")


def _extract_scope_from_plan(plan_path: Path) -> list[str]:
    """从 exec-plan 的 `## 影响范围` 段提取文件/目录声明。

    返回条目可能是精确路径（`src/.../Foo.java`），也可能是目录前缀（以 `/` 结尾）。
    """
    if not plan_path.is_file():
        return []
    text = plan_path.read_text(encoding="utf-8")
    # 去除 frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and ("影响范围" in line or "受影响文件" in line):
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    section = "\n".join(lines[start:end])

    scope: list[str] = []
    seen: set[str] = set()
    for m in _SCOPE_FILE_LINE_RE.finditer(section):
        path_str = m.group(1) or m.group(2)
        if not path_str:
            continue
        if any(ch in path_str for ch in ("*", "?", "{", "}")):
            idx = path_str.find("*")
            prefix = path_str[:idx].rstrip("/")
            if prefix and prefix not in seen:
                seen.add(prefix)
                scope.append(prefix + "/")
            continue
        if path_str not in seen:
            seen.add(path_str)
            scope.append(path_str)
    return scope


def _collect_actual_changes(base_ref: str) -> tuple[set[str], str | None]:
    """用 git 采集当前分支 vs base 的改动 + 工作区未提交改动并集。

    返回 (paths, error)；git 失败时 error 非 None。
    """
    paths: set[str] = set()
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}..HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
        )
        if diff.returncode == 0:
            for line in diff.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(ROOT), capture_output=True, text=True, check=False,
        )
        if status.returncode == 0:
            for line in status.stdout.splitlines():
                # porcelain 格式： "XY <path>" 或 "XY <orig> -> <path>"（rename）
                if len(line) < 4:
                    continue
                payload = line[3:]
                if " -> " in payload:
                    payload = payload.split(" -> ")[-1]
                paths.add(payload.strip())
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return paths, str(e)
    return paths, None


def _matches_scope(rel: str, scope_items: list[str]) -> bool:
    """与 style.check 保持一致：精确 + 目录前缀匹配。"""
    if rel in scope_items:
        return True
    for entry in scope_items:
        if entry.endswith("/") and rel.startswith(entry):
            return True
        if not entry.endswith("/"):
            candidate = ROOT / entry
            if candidate.is_dir() and rel.startswith(entry + "/"):
                return True
    return False


def check(slug: str, base_ref: str = "main") -> list[str]:
    """核心检查函数：返回越界条目的规则化告警列表（可能为空）。"""
    plan_path = ROOT / "harness" / "exec-plans" / f"{slug}.md"
    if not plan_path.is_file():
        return [
            f"[{RULE_ID}] harness/exec-plans/{slug}.md:0 — "
            f"exec-plan 不存在，无法校验 scope；建议：先 `executor.py init {slug}` 声明范围"
        ]
    scope_items = _extract_scope_from_plan(plan_path)
    if not scope_items:
        return [
            f"[{RULE_ID}] harness/exec-plans/{slug}.md:0 — "
            f"exec-plan 未在 `## 影响范围` 段声明受影响文件；建议：用反引号列出 `src/...` 路径"
        ]
    actual, err = _collect_actual_changes(base_ref)
    if err:
        return [
            f"[{RULE_ID}] <git>:0 — 无法采集实际改动（{err}）；建议：确认 git 可用、base 分支存在"
        ]
    drift = sorted(p for p in actual if not _matches_scope(p, scope_items))
    if not drift:
        return []
    return [
        f"[{RULE_ID}] {p}:0 — 此文件不在 exec-plan {slug} 声明范围内，"
        f"合并前请 `git restore` 或显式加入 `## 影响范围` 声明"
        for p in drift
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=f"check-scope（规则 {RULE_ID}，warning 阶段）")
    parser.add_argument("--slug", default=None, help="exec-plan slug；缺省读 HARNESS_SLUG")
    parser.add_argument("--base", default="main", help="对比基准分支（默认 main）")
    args = parser.parse_args()
    slug = args.slug or os.environ.get("HARNESS_SLUG")
    if not slug:
        # 无 slug 时静默通过（warning 阶段不可阻塞 run.py）
        print(f"ℹ️  [{RULE_ID}] 未提供 --slug/HARNESS_SLUG，跳过 scope 漂移检查", file=sys.stderr)
        return 0
    warnings = check(slug=slug, base_ref=args.base)
    for w in warnings:
        print(w, file=sys.stderr)
    # warning 阶段 exit code 始终为 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
