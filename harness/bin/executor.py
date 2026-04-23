#!/usr/bin/env python3
"""harness-executor engine: task lifecycle gatekeeper.

Workflow: detect → load → plan → approve → execute → verify → complete.

Subcommands:
    check                         environment self-check (exit 2 on failure)
    status <slug>                 inspect plan status
    init <slug> "desc" [--simple] create plan skeleton
    plan <slug>                   fill sectional skeleton into plan body
    approve <slug>                status: drafted → approved
    verify <slug>                 run scripts/validate.py; append record
    complete <slug>               status → completed; sync checkpoint

The engine itself does not invoke claude; Claude sessions read stdout and
decide the next step (e.g. calling ExitPlanMode, delegating to Agents).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from state import (  # noqa: E402
    Frontmatter,
    append_section,
    atomic_write,
    checkpoint_path,
    find_project_root,
    plan_path,
    read_plan,
    write_plan,
)

log = logging.getLogger("harness.executor")

VALID_STATUSES = ("drafted", "approved", "in_progress", "completed", "aborted")
CLAUDE_REQUIRED_SECTIONS = ("## 快速链接", "## 构建命令", "## 分层规则")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


@dataclass
class CheckResult:
    ok: bool
    missing_sections: list[str]
    missing_files: list[str]
    claude_md_path: str


def cmd_check(root: Path) -> int:
    claude_md = root / "CLAUDE.md"
    missing_files: list[str] = []
    missing_sections: list[str] = []

    if not claude_md.is_file():
        missing_files.append(str(claude_md.relative_to(root)))
    else:
        text = claude_md.read_text(encoding="utf-8")
        for section in CLAUDE_REQUIRED_SECTIONS:
            if section not in text:
                missing_sections.append(section)

    result = CheckResult(
        ok=not missing_files and not missing_sections,
        missing_sections=missing_sections,
        missing_files=missing_files,
        claude_md_path=str(claude_md.relative_to(root)) if claude_md.is_file() else "<missing>",
    )

    payload = {
        "ok": result.ok,
        "claude_md": result.claude_md_path,
        "missing_files": result.missing_files,
        "missing_sections": result.missing_sections,
        "next_step": (
            "proceed"
            if result.ok
            else "run `python harness/bin/creator.py audit` to scope the gap, then decide on build/fix"
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


def validate_slug(slug: str) -> None:
    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"invalid slug '{slug}': use lowercase letters, digits, and hyphens only")


def cmd_status(root: Path, slug: str) -> int:
    validate_slug(slug)
    path = plan_path(root, slug)
    if not path.is_file():
        payload = {"slug": slug, "exists": False, "path": str(path.relative_to(root))}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fm = read_plan(path)
    payload = {
        "slug": slug,
        "exists": True,
        "path": str(path.relative_to(root)),
        "status": fm.data.get("status", "unknown"),
        "task": fm.data.get("task", ""),
        "created": fm.data.get("created", ""),
        "approved_at": fm.data.get("approved_at"),
        "verify_runs_count": len(fm.data.get("verify_runs", []) or []),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


PLAN_SKELETON_BODY = """
## 目标
{goal_placeholder}

## 影响范围
- 受影响文件：{scope_placeholder}
- 受影响层级：Layer {layer_placeholder}
- 是否结构性变更：{structural_placeholder}

## 分阶段步骤
1. {steps_placeholder}

## 验证方式
- 机械验证：make verify
- 人工验证：{manual_verify_placeholder}

## 回退策略
{rollback_placeholder}

## 批准门闸
由 Claude 主调 ExitPlanMode；用户批准后 Claude 调 `python harness/bin/executor.py approve {slug}`。

## 验证记录
<!-- 引擎每次 verify 命令 append 一条 -->

## 完成记录
<!-- 引擎在 complete 时写入 -->
"""


def cmd_init(root: Path, slug: str, description: str, simple: bool) -> int:
    validate_slug(slug)
    path = plan_path(root, slug)
    if path.exists():
        log.error("plan already exists: %s", path)
        return 1

    # 若在 worktree 下创建 plan，顺手把前端依赖软链准备好（避免首次 build 撞 vite 缺失）
    _ensure_worktree_dependencies(root)

    if simple:
        checkpoint = checkpoint_path(root, slug)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"---\ntask: {description}\nslug: {slug}\ncreated: {date.today().isoformat()}\n"
            f"simple: true\nstatus: in_progress\n---\n\n"
            "## 备注\n简单任务跳过计划+批准流程。\n"
        )
        atomic_write(checkpoint, content)
        payload = {"mode": "simple", "checkpoint_path": str(checkpoint.relative_to(root))}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    today = date.today()
    body = PLAN_SKELETON_BODY.format(
        slug=slug,
        goal_placeholder="{Claude 填}",
        scope_placeholder="{Claude 填}",
        layer_placeholder="{N}",
        structural_placeholder="{是/否}",
        steps_placeholder="{Claude 填}",
        manual_verify_placeholder="{可选}",
        rollback_placeholder="{Claude 填}",
    )
    fm = Frontmatter(
        data={
            "task": description,
            "slug": slug,
            "created": today.isoformat(),
            "approved_at": None,
            "status": "drafted",
            "verify_runs": [],
        },
        body=body,
    )
    write_plan(path, fm)
    payload = {
        "mode": "planned",
        "plan_path": str(path.relative_to(root)),
        "status": "drafted",
        "next_step": "Claude fills sections via Edit, then calls ExitPlanMode; user approval → approve subcommand",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_plan(root: Path, slug: str) -> int:
    """Regenerate skeleton body if someone wiped it; idempotent hint step."""
    validate_slug(slug)
    path = plan_path(root, slug)
    if not path.is_file():
        log.error("plan not found: %s (run init first)", path)
        return 1
    fm = read_plan(path)
    if "## 目标" in fm.body:
        payload = {"slug": slug, "action": "noop", "reason": "skeleton sections already present"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    fm.body = PLAN_SKELETON_BODY.format(
        slug=slug,
        goal_placeholder="{Claude 填}",
        scope_placeholder="{Claude 填}",
        layer_placeholder="{N}",
        structural_placeholder="{是/否}",
        steps_placeholder="{Claude 填}",
        manual_verify_placeholder="{可选}",
        rollback_placeholder="{Claude 填}",
    )
    write_plan(path, fm)
    payload = {"slug": slug, "action": "skeleton-restored", "plan_path": str(path.relative_to(root))}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_approve(root: Path, slug: str) -> int:
    validate_slug(slug)
    path = plan_path(root, slug)
    if not path.is_file():
        log.error("plan not found: %s", path)
        return 1
    fm = read_plan(path)
    current = fm.data.get("status")
    if current != "drafted":
        log.error("approve requires status=drafted, got %r", current)
        return 1
    fm.data["status"] = "approved"
    fm.data["approved_at"] = datetime.now().isoformat(timespec="seconds")
    write_plan(path, fm)
    payload = {
        "slug": slug,
        "status": "approved",
        "approved_at": fm.data["approved_at"],
        "plan_path": str(path.relative_to(root)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


_SCOPE_FILE_LINE_RE = re.compile(r"`(src/[^`]+?)`|`([a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+)`")
_SCOPE_RANGE_HEADERS = ("## 影响范围", "## 影响范围 / 受影响文件", "受影响文件")


def _extract_scope_from_plan(body: str) -> list[str]:
    """从 exec-plan 正文提取"受影响文件"集合。

    识别规则：定位"## 影响范围"段（或近似标题），直到下一个 "##" 段为止，
    抽取其中形如 `src/...` 或 `path/to/file.ext` 的反引号包裹路径。
    - 忽略尾随的括号说明（如 "(1658 → 删除)"）
    - 忽略通配符条目（如 `.../*.java`）——这些会被 check-scope.py 处理
    - 返回仓库相对 POSIX 路径的去重列表
    """
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and ("影响范围" in line or "受影响文件" in line):
            start = i + 1
            break
    if start is None:
        return []
    # 找段落结束
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
        # 过滤通配符与模糊条目
        if any(ch in path_str for ch in ("*", "?", "{", "}")):
            # 目录通配（如 `api/basicdata/*.java`）：取目录前缀
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


def _ensure_worktree_dependencies(root: Path) -> None:
    """若当前 root 位于 worktree 下，确保前端 node_modules 与后端 target 已软链到主仓。

    critic-2026-04-22 缺陷 3：worktree 默认只复制 git 跟踪文件，`src/frontend/node_modules`
    为空导致 `sh: vite: command not found`。在 verify 入口自动建立 symlink，消除人工干预。

    策略：
    - 仅当 root 的真实路径包含 `.claude/worktrees/` 片段时才动手
    - 从 `.claude/worktrees/<name>/` 向上两级即主仓；检查主仓对应目录存在再建链
    - 若目标已存在（常规目录或已有软链），全部跳过，不破坏用户手工状态
    """
    import os

    try:
        resolved = root.resolve()
    except OSError:
        return
    parts = resolved.parts
    try:
        wt_idx = parts.index(".claude")
    except ValueError:
        return
    if wt_idx + 1 >= len(parts) or parts[wt_idx + 1] != "worktrees":
        return
    # 主仓根 = 去掉 `.claude/worktrees/<name>` 三段
    if wt_idx + 2 >= len(parts):
        return
    main_repo = Path(*parts[:wt_idx])
    if not (main_repo / "CLAUDE.md").is_file():
        # 不像主仓；放弃
        return

    candidates = [
        ("src/frontend/node_modules", "前端依赖"),
        ("src/backend/target", "后端构建产物"),
    ]
    for rel, desc in candidates:
        link_path = root / rel
        source = main_repo / rel
        # 目标已存在（目录 / 软链 / 文件）一律跳过
        if link_path.exists() or link_path.is_symlink():
            continue
        if not source.exists():
            log.warning(
                "[worktree-deps] 主仓未构建 %s（%s），跳过自动软链。"
                "首次 build 前请在主仓执行对应构建命令。",
                rel, desc,
            )
            continue
        try:
            link_path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source, link_path, target_is_directory=True)
            log.info("[worktree-deps] 已软链 %s → %s", rel, source)
        except OSError as exc:
            log.warning("[worktree-deps] 软链 %s 失败：%s", rel, exc)


def cmd_verify(root: Path, slug: str) -> int:
    validate_slug(slug)
    path = plan_path(root, slug)
    if not path.is_file():
        log.error("plan not found: %s", path)
        return 1
    fm = read_plan(path)
    if fm.data.get("status") not in ("approved", "in_progress"):
        log.error("verify requires status in {approved, in_progress}, got %r", fm.data.get("status"))
        return 1

    if fm.data.get("status") == "approved":
        fm.data["status"] = "in_progress"
        write_plan(path, fm)

    # worktree 环境下自动软链前端/后端依赖，消除 `sh: vite: command not found` 类失败
    _ensure_worktree_dependencies(root)

    # 从 exec-plan 提取 scope 文件集合；未声明时回退全仓模式。
    scope_items = _extract_scope_from_plan(fm.body)
    scope_args: list[str] = []
    if scope_items:
        log.info("verify scope: %d entries extracted from plan", len(scope_items))
        scope_args = ["--scope", ",".join(scope_items), "--slug", slug]
    else:
        log.warning(
            "⚠️  plan %s 未声明受影响文件集合，verify 回退为全仓扫描（历史违规将硬失败）。"
            " 建议在 `## 影响范围` 段落用反引号列出 `src/...` 路径。",
            slug,
        )

    validate_script = root / "scripts" / "validate.py"
    if not validate_script.is_file():
        log.error("scripts/validate.py not found at %s", validate_script)
        return 1

    log.info("running %s ...", validate_script)
    # validate.py 调用 make verify，通过环境变量把 scope 透传给 run.py
    env = None
    if scope_args:
        import os
        env = os.environ.copy()
        env["HARNESS_VERIFY_SCOPE"] = ",".join(scope_items)
        env["HARNESS_SLUG"] = slug
    proc = subprocess.run(
        [sys.executable, str(validate_script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
    )
    passed = proc.returncode == 0
    stamp = datetime.now().isoformat(timespec="seconds")
    summary = "PASS" if passed else f"FAIL (exit {proc.returncode})"

    fm = read_plan(path)
    runs = list(fm.data.get("verify_runs") or [])
    runs.append({"timestamp": stamp, "result": summary})
    fm.data["verify_runs"] = runs
    write_plan(path, fm)

    record = (
        f"\n### {stamp} — {summary}\n\n"
        f"```\nstdout (last 40 lines):\n{_tail(proc.stdout, 40)}\n\n"
        f"stderr (last 20 lines):\n{_tail(proc.stderr, 20)}\n```\n"
    )
    append_section(path, "验证记录", record)

    payload = {
        "slug": slug,
        "passed": passed,
        "exit_code": proc.returncode,
        "timestamp": stamp,
        "plan_path": str(path.relative_to(root)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def _tail(text: str, n: int) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])


def cmd_complete(root: Path, slug: str) -> int:
    validate_slug(slug)
    path = plan_path(root, slug)
    if not path.is_file():
        log.error("plan not found: %s", path)
        return 1
    fm = read_plan(path)
    if fm.data.get("status") not in ("in_progress", "approved"):
        log.error("complete requires status in {in_progress, approved}, got %r", fm.data.get("status"))
        return 1

    runs = fm.data.get("verify_runs") or []
    last = runs[-1] if runs else None
    last_passed = bool(last and "PASS" in str(last.get("result", "")))
    if not last_passed:
        log.error("cannot complete: latest verify did not pass (%r)", last)
        return 1

    fm.data["status"] = "completed"
    write_plan(path, fm)

    stamp = datetime.now().isoformat(timespec="seconds")
    completion = (
        f"\n- 完成时间：{stamp}\n"
        f"- 验证记录条数：{len(runs)}\n"
        f"- 最终状态：completed\n"
    )
    append_section(path, "完成记录", completion)

    chk = checkpoint_path(root, slug)
    chk.parent.mkdir(parents=True, exist_ok=True)
    if not chk.is_file():
        chk_content = (
            f"---\ntask: {fm.data.get('task', '')}\nslug: {slug}\n"
            f"created: {fm.data.get('created', date.today().isoformat())}\n"
            f"last_updated: {date.today().isoformat()}\nstatus: completed\n---\n\n"
            "## 已完成阶段\n- [x] 全流程 — 由 harness-executor 驱动完成\n\n"
            "## 已修改文件\n（由任务本身记录，见 exec-plan 正文）\n"
        )
        atomic_write(chk, chk_content)

    task_name = fm.data.get("task", slug)
    commit_msg = f"chore(harness): complete {slug} — {task_name}"
    git_result = subprocess.run(
        ["git", "commit", "--only", str(path), str(chk), "-m", commit_msg],
        cwd=str(root), capture_output=True, text=True,
    )
    auto_committed = git_result.returncode == 0
    if auto_committed:
        log.info("auto-committed harness files: %s", commit_msg)
    else:
        log.warning("git commit skipped or failed: %s", git_result.stderr.strip())

    payload = {
        "slug": slug,
        "status": "completed",
        "plan_path": str(path.relative_to(root)),
        "checkpoint_path": str(chk.relative_to(root)),
        "auto_committed": auto_committed,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="executor", description="harness-executor engine")
    parser.add_argument("--root", type=Path, default=None, help="project root (auto-detected if omitted)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="environment self-check")
    status_p = sub.add_parser("status", help="inspect plan status")
    status_p.add_argument("slug")

    init_p = sub.add_parser("init", help="create plan skeleton")
    init_p.add_argument("slug")
    init_p.add_argument("description")
    init_p.add_argument("--simple", action="store_true", help="skip plan, write only checkpoint")

    plan_p = sub.add_parser("plan", help="regenerate skeleton sections if missing")
    plan_p.add_argument("slug")

    approve_p = sub.add_parser("approve", help="drafted → approved")
    approve_p.add_argument("slug")

    verify_p = sub.add_parser("verify", help="run validate.py, append record")
    verify_p.add_argument("slug")

    complete_p = sub.add_parser("complete", help="mark completed + sync checkpoint")
    complete_p.add_argument("slug")

    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else find_project_root()

    if args.command == "check":
        return cmd_check(root)
    if args.command == "status":
        return cmd_status(root, args.slug)
    if args.command == "init":
        return cmd_init(root, args.slug, args.description, args.simple)
    if args.command == "plan":
        return cmd_plan(root, args.slug)
    if args.command == "approve":
        return cmd_approve(root, args.slug)
    if args.command == "verify":
        return cmd_verify(root, args.slug)
    if args.command == "complete":
        return cmd_complete(root, args.slug)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
