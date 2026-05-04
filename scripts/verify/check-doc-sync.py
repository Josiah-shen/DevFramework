#!/usr/bin/env python3
"""check-doc-sync — 需求相关改动必须同步业务设计文档。

规则 ID：process/requirement-doc-sync
规则 ID：process/doc-cascade-sync
阶段：warning（非阻塞，exit code 恒为 0）
晋升条件：连续若干轮任务验证确认误报可控后，可在 run.py 中升级为 error。

背景
----
PRD/BDD/RID/BP 模板要求模块新增、拆分、合并、删除时立即更新文档，但验证链路
只检查构建、架构、测试和接口，导致需求新增/修改/删除容易只落到源码而漏文档。

检测策略（保守提示，不做语义判断）
--------------------------------
1. 收集任务 scope 与当前 git 改动。
2. 若命中需求相关源码路径（API、Service、Mapper、数据库、前端页面/服务），视为可能
   影响产品需求或跨层契约。
3. 若同时命中 `docs/design-docs/PRD|BDD|RID|BP`，通过。
4. 若 exec-plan 显式写了文档豁免原因，也通过。
5. 否则输出 warning，提示补文档或补豁免说明。

级联同步策略
------------
1. PRD/BP 变更：应同步 RID，或在 exec-plan 写明下游文档无需变更原因。
2. BDD 变更：应同步 RID，或写明计算/口径未影响实现契约的原因。
3. RID 变更：应填写“变更影响清单”，或写明下游无需同步原因。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
RULE_ID = "process/requirement-doc-sync"
CASCADE_RULE_ID = "process/doc-cascade-sync"

DOC_PREFIXES = (
    "docs/design-docs/PRD/",
    "docs/design-docs/BDD/",
    "docs/design-docs/RID/",
    "docs/design-docs/BP/",
)

REQUIREMENT_PREFIXES = (
    "src/backend/src/main/java/com/xptsqas/api/",
    "src/backend/src/main/java/com/xptsqas/core/services/",
    "src/backend/src/main/resources/mapper/",
    "src/database/",
    "src/frontend/src/core/services/",
    "src/frontend/src/ui/views/",
)

EXEMPTION_RE = re.compile(
    r"(文档无需变更|无需更新文档|无需同步文档)\s*[：:]\s*(?P<reason>.+)"
    r"|(?:docs-sync|doc-sync)\s*:\s*exempt\s*[：:]\s*(?P<en_reason>.+)",
    re.IGNORECASE,
)
CASCADE_EXEMPTION_RE = re.compile(
    r"(下游文档无需变更|无需更新下游文档|无需同步下游文档)\s*[：:]\s*(?P<reason>.+)"
    r"|(?:doc-cascade|cascade-docs)\s*:\s*exempt\s*[：:]\s*(?P<en_reason>.+)",
    re.IGNORECASE,
)
IMPACT_LIST_RE = re.compile(r"变更影响清单")
IMPACT_ROW_RE = re.compile(r"^\|\s*(?![-: ]+\|)(?!变更项\s*\|)(?!\{).+\|.+\|.+\|", re.MULTILINE)


def parse_scope(raw_scope: str | None) -> set[str]:
    if not raw_scope:
        return set()
    items: set[str] = set()
    for chunk in raw_scope.split(","):
        item = normalize_path(chunk)
        if item:
            items.add(item)
    return items


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def _collect_actual_changes(root: Path = ROOT, base_ref: str = "HEAD") -> set[str]:
    paths: set[str] = set()
    diff = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode == 0:
        for line in diff.stdout.splitlines():
            line = normalize_path(line)
            if line:
                paths.add(line)

    status = subprocess.run(
        ["git", "status", "--porcelain", "-z"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode == 0:
        entries = [entry for entry in status.stdout.split("\0") if entry]
        i = 0
        while i < len(entries):
            entry = entries[i]
            status_code = entry[:2]
            payload = entry[3:] if len(entry) > 3 else ""
            if "R" in status_code or "C" in status_code:
                # porcelain -z stores rename/copy as "XY new\0old\0"; keep the new path.
                i += 1
            path = normalize_path(payload)
            if path:
                paths.add(path)
            i += 1
    return paths


def _is_under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def is_requirement_path(path: str) -> bool:
    if not _is_under(path, REQUIREMENT_PREFIXES):
        return False
    if "/src/test/" in path or path.endswith("Test.java"):
        return False
    return True


def is_design_doc_path(path: str) -> bool:
    if not _is_under(path, DOC_PREFIXES):
        return False
    name = Path(path).name
    return not name.startswith("_模板_")


def design_doc_kind(path: str) -> str | None:
    for kind in ("PRD", "BDD", "RID", "BP"):
        if path.startswith(f"docs/design-docs/{kind}/") and is_design_doc_path(path):
            return kind
    return None


def _plan_path(root: Path, slug: str | None) -> Path | None:
    if not slug:
        return None
    return root / "harness" / "exec-plans" / f"{slug}.md"


def plan_has_exemption(root: Path = ROOT, slug: str | None = None) -> bool:
    path = _plan_path(root, slug)
    if path is None or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "必须" in line or "应" in line:
            continue
        match = EXEMPTION_RE.search(line)
        if match is None:
            continue
        reason = (match.groupdict().get("reason") or match.groupdict().get("en_reason") or "").strip()
        normalized_reason = reason.strip("`'\"“” ")
        if normalized_reason and "<原因>" not in normalized_reason and "{原因}" not in normalized_reason:
            return True
    return False


def _plan_has_exemption_pattern(root: Path, slug: str | None, pattern: re.Pattern[str]) -> bool:
    path = _plan_path(root, slug)
    if path is None or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "必须" in line or "应" in line:
            continue
        match = pattern.search(line)
        if match is None:
            continue
        reason = (match.groupdict().get("reason") or match.groupdict().get("en_reason") or "").strip()
        normalized_reason = reason.strip("`'\"“” ")
        if normalized_reason and "<原因>" not in normalized_reason and "{原因}" not in normalized_reason:
            return True
    return False


def plan_has_cascade_exemption(root: Path = ROOT, slug: str | None = None) -> bool:
    return _plan_has_exemption_pattern(root=root, slug=slug, pattern=CASCADE_EXEMPTION_RE)


def rid_has_impact_list(path: str, root: Path = ROOT) -> bool:
    doc_path = root / path
    if not doc_path.is_file():
        return False
    try:
        text = doc_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if IMPACT_LIST_RE.search(text) is None:
        return False
    return IMPACT_ROW_RE.search(text) is not None


def check(scope: set[str] | None = None, slug: str | None = None, root: Path = ROOT) -> list[str]:
    """返回文档同步告警列表；warning 阶段不负责 exit code。"""
    paths = set(scope or set())
    paths.update(_collect_actual_changes(root=root))
    paths = {normalize_path(p) for p in paths if normalize_path(p)}

    warnings = check_requirement_doc_sync(paths=paths, slug=slug, root=root)
    warnings.extend(check_doc_cascade_sync(paths=paths, slug=slug, root=root))
    return warnings


def check_requirement_doc_sync(paths: set[str], slug: str | None = None, root: Path = ROOT) -> list[str]:
    requirement_paths = sorted(p for p in paths if is_requirement_path(p))
    if not requirement_paths:
        return []
    if any(is_design_doc_path(p) for p in paths):
        return []
    if plan_has_exemption(root=root, slug=slug):
        return []

    sample = "、".join(requirement_paths[:5])
    if len(requirement_paths) > 5:
        sample += f" 等 {len(requirement_paths)} 个文件"
    plan_hint = (
        f"`harness/exec-plans/{slug}.md` 中写明“文档无需变更：<原因>”"
        if slug
        else "exec-plan 中写明“文档无需变更：<原因>”"
    )
    return [
        f"[{RULE_ID}] docs/design-docs:0 — 检测到需求相关改动（{sample}），"
        f"但未同步 PRD/BDD/RID/BP 文档；请补充 `docs/design-docs/`，或在 {plan_hint}"
    ]


def check_doc_cascade_sync(paths: set[str], slug: str | None = None, root: Path = ROOT) -> list[str]:
    doc_paths_by_kind: dict[str, list[str]] = {"PRD": [], "BDD": [], "RID": [], "BP": []}
    for path in sorted(paths):
        kind = design_doc_kind(path)
        if kind:
            doc_paths_by_kind[kind].append(path)

    if not any(doc_paths_by_kind.values()):
        return []
    if plan_has_cascade_exemption(root=root, slug=slug):
        return []

    warnings: list[str] = []
    rid_changed = bool(doc_paths_by_kind["RID"])
    for upstream_kind in ("PRD", "BP"):
        upstream_paths = doc_paths_by_kind[upstream_kind]
        if upstream_paths and not rid_changed:
            sample = "、".join(upstream_paths[:3])
            warnings.append(
                f"[{CASCADE_RULE_ID}] docs/design-docs/RID:0 — 检测到 {upstream_kind} 文档变更（{sample}），"
                "但未同步 RID 实现文档；请补充对应 RID，或在 exec-plan 写明“下游文档无需变更：<原因>”"
            )

    bdd_paths = doc_paths_by_kind["BDD"]
    if bdd_paths and not rid_changed:
        sample = "、".join(bdd_paths[:3])
        warnings.append(
            f"[{CASCADE_RULE_ID}] docs/design-docs/RID:0 — 检测到 BDD 口径/规则文档变更（{sample}），"
            "但未同步 RID 实现文档；请补充接口/DB/实现契约影响，或写明“下游文档无需变更：<原因>”"
        )

    for rid_path in doc_paths_by_kind["RID"]:
        if not rid_has_impact_list(rid_path, root=root):
            warnings.append(
                f"[{CASCADE_RULE_ID}] {rid_path}:0 — RID 文档已变更，但未填写有效的“变更影响清单”；"
                "请列出影响的 PRD/BDD/BP、测试、接口/DB，或写明“下游文档无需变更：<原因>”"
            )

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=f"check-doc-sync（规则 {RULE_ID}，warning 阶段）")
    parser.add_argument("--scope", default=None, help="逗号分隔的任务影响范围路径")
    parser.add_argument("--slug", default=None, help="exec-plan slug；用于读取文档豁免说明")
    args = parser.parse_args()

    warnings = check(scope=parse_scope(args.scope), slug=args.slug)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if warnings:
        print(f"ℹ️  [{RULE_ID}] 共 {len(warnings)} 条告警（warning 阶段，不阻塞）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())