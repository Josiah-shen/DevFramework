#!/usr/bin/env python3
"""harness-creator engine: infrastructure auditor and builder.

Six dimensions: doc, lint, build, layer, agent, harness.
Three tiers: 0-20 bare (build from scratch), 21-70 gapped (targeted fix),
71+ healthy (dry-run + diff list).

Subcommands:
    audit            — score only, write report
    build            — act based on score (dry-run at 71+)
    fix <dimension>  — target a single dimension
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rubric import (  # noqa: E402
    DIMENSIONS,
    AuditResult,
    DimensionScore,
    run_audit,
    score_three_tier,
)
from state import (  # noqa: E402
    atomic_write,
    audit_path,
    find_project_root,
    latest_audit_path,
)

log = logging.getLogger("harness.creator")

REQUIRED_DOCS = ("CLAUDE.md", "docs/ARCHITECTURE.md", "docs/DEVELOPMENT.md", "docs/PRODUCT_SENSE.md")
REQUIRED_LINT_SCRIPTS = (
    "scripts/lint-deps.py",
    "scripts/verify/checks/arch.py",
    "scripts/verify/checks/style.py",
    "scripts/verify/checks/api.py",
    "scripts/verify/checks/e2e.py",
)
REQUIRED_MAKE_TARGETS = ("build", "test", "lint-arch", "verify", "validate")
REQUIRED_AGENTS = (
    "coordinator",
    "executor-research",
    "executor-code",
    "executor-shell",
    "executor-review",
    "executor-lint-rule",
    "verifier",
)
REQUIRED_HARNESS_DIRS = ("harness/memory", "harness/tasks", "harness/trace", "harness/exec-plans", "harness/bin")
BACKEND_JAVA_ROOT = "src/backend/src/main/java/com/xptsqas"
BACKEND_LAYERS = tuple(
    f"{BACKEND_JAVA_ROOT}/{layer}"
    for layer in ("types", "config", "core/services", "core/repository", "api")
)
FRONTEND_LAYERS = ("src/frontend/src/core", "src/frontend/src/ui", "src/frontend/src/utils")


def probe_doc(root: Path) -> DimensionScore:
    missing = [d for d in REQUIRED_DOCS if not (root / d).is_file()]
    soft_defect = False
    evidence: list[str] = []
    if not missing:
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        link_pattern = re.compile(r"\]\(([^)]+\.md)\)")
        broken: list[str] = []
        for link in link_pattern.findall(claude):
            target = (root / link).resolve()
            if not target.is_file():
                broken.append(link)
        if broken:
            soft_defect = True
            evidence.append(f"CLAUDE.md has {len(broken)} broken link(s): {broken}")
        else:
            evidence.append("CLAUDE.md links all resolve")
    gaps = [f"missing doc: {p}" for p in missing]
    return DimensionScore(
        name="doc",
        score=score_three_tier(len(missing), soft_defect),
        evidence=evidence,
        gaps=gaps,
    )


def probe_lint(root: Path) -> DimensionScore:
    missing = [p for p in REQUIRED_LINT_SCRIPTS if not (root / p).is_file()]
    return DimensionScore(
        name="lint",
        score=score_three_tier(len(missing), False),
        evidence=[f"{len(REQUIRED_LINT_SCRIPTS) - len(missing)}/{len(REQUIRED_LINT_SCRIPTS)} lint scripts present"],
        gaps=[f"missing lint script: {p}" for p in missing],
    )


def probe_build(root: Path) -> DimensionScore:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return DimensionScore(
            name="build",
            score=0,
            evidence=["Makefile absent"],
            gaps=["Makefile absent — all build targets missing"],
        )
    text = makefile.read_text(encoding="utf-8")
    target_pattern = re.compile(r"^([A-Za-z0-9_-]+):", re.MULTILINE)
    present_targets = set(target_pattern.findall(text))
    missing = [t for t in REQUIRED_MAKE_TARGETS if t not in present_targets]
    return DimensionScore(
        name="build",
        score=score_three_tier(len(missing), False),
        evidence=[f"Makefile targets found: {sorted(present_targets & set(REQUIRED_MAKE_TARGETS))}"],
        gaps=[f"missing make target: {t}" for t in missing],
    )


def probe_layer(root: Path) -> DimensionScore:
    missing: list[str] = []
    for layer in (*BACKEND_LAYERS, *FRONTEND_LAYERS):
        if not (root / layer).is_dir():
            missing.append(layer)
    return DimensionScore(
        name="layer",
        score=score_three_tier(len(missing), False),
        evidence=[f"{len(BACKEND_LAYERS) + len(FRONTEND_LAYERS) - len(missing)} layer dirs present"],
        gaps=[f"missing layer dir: {p}" for p in missing],
    )


def probe_agent(root: Path) -> DimensionScore:
    agents_dir = root / ".claude" / "agents"
    if not agents_dir.is_dir():
        return DimensionScore(
            name="agent",
            score=0,
            evidence=[".claude/agents/ absent"],
            gaps=[".claude/agents/ directory missing"],
        )
    missing = [a for a in REQUIRED_AGENTS if not (agents_dir / f"{a}.md").is_file()]
    return DimensionScore(
        name="agent",
        score=score_three_tier(len(missing), False),
        evidence=[f"{len(REQUIRED_AGENTS) - len(missing)}/{len(REQUIRED_AGENTS)} required agents present"],
        gaps=[f"missing agent: {a}.md" for a in missing],
    )


def probe_harness(root: Path) -> DimensionScore:
    missing = [d for d in REQUIRED_HARNESS_DIRS if not (root / d).is_dir()]
    soft = False
    evidence: list[str] = []
    if not missing:
        bin_dir = root / "harness" / "bin"
        entrypoints = [bin_dir / "creator.py", bin_dir / "executor.py"]
        if not all(p.is_file() for p in entrypoints):
            soft = True
            evidence.append("harness/bin/ exists but creator.py or executor.py missing")
        else:
            evidence.append("all harness dirs present and bin entrypoints exist")
    return DimensionScore(
        name="harness",
        score=score_three_tier(len(missing), soft),
        evidence=evidence,
        gaps=[f"missing harness dir: {d}" for d in missing],
    )


def build_probes(root: Path) -> dict:
    return {
        "doc": lambda: probe_doc(root),
        "lint": lambda: probe_lint(root),
        "build": lambda: probe_build(root),
        "layer": lambda: probe_layer(root),
        "agent": lambda: probe_agent(root),
        "harness": lambda: probe_harness(root),
    }


def render_report(result: AuditResult, root: Path, today: date) -> str:
    lines = [
        "---",
        f"audit_date: {today.isoformat()}",
        f"score: {result.normalized}",
        f"grade: {result.grade}",
        "---",
        "",
        f"# Harness 基础设施审计报告 — {today.isoformat()}",
        "",
        f"**总评分**：{result.normalized}/100（grade: {result.grade}）",
        "",
        "## 六维分值",
        "",
        "| 维度 | 分值 | 证据 | 缺口 |",
        "|------|-----:|------|------|",
    ]
    for dim in result.scores:
        ev = "；".join(dim.evidence) or "—"
        gp = "；".join(dim.gaps) or "—"
        lines.append(f"| {dim.name} | {dim.score}/20 | {ev} | {gp} |")
    lines.append("")
    lines.append("## 缺口清单")
    lines.append("")
    if result.gaps:
        for g in result.gaps:
            lines.append(f"- {g}")
    else:
        lines.append("（无缺口）")
    lines.append("")
    lines.append("## 档位与建议")
    lines.append("")
    lines.append(f"当前档位：**{result.grade}**")
    if result.grade == "bare":
        lines.append("- 从零搭建：`python harness/bin/creator.py build` 会 copy templates/ 全套骨架。")
    elif result.grade == "gapped":
        lines.append("- 针对性补缺：`python harness/bin/creator.py fix <dimension>`，按上述缺口逐项修复。")
    else:
        lines.append("- `build` 将退化为 dry-run，输出差异清单不实改；真实改动需显式 `fix <dim>`。")
    lines.append("")
    return "\n".join(lines)


def ensure_harness_dirs(root: Path) -> None:
    """Create harness base directories if they were garbage-collected by git (empty dirs are untracked)."""
    for rel in REQUIRED_HARNESS_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)


def cmd_audit(root: Path) -> int:
    ensure_harness_dirs(root)
    probes = build_probes(root)
    result = run_audit(probes)
    today = date.today()
    report = render_report(result, root, today)
    target = audit_path(root, today)
    atomic_write(target, report)
    atomic_write(latest_audit_path(root), report)
    log.info("audit complete: score=%d grade=%s report=%s", result.normalized, result.grade, target)

    summary = {
        "score": result.normalized,
        "grade": result.grade,
        "gaps": result.gaps,
        "report_path": str(target.relative_to(root)),
        "dimensions": [asdict(d) for d in result.scores],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_build(root: Path) -> int:
    probes = build_probes(root)
    result = run_audit(probes)
    log.info("build invoked: score=%d grade=%s", result.normalized, result.grade)

    if result.grade == "healthy":
        summary = {
            "mode": "dry-run",
            "reason": "score >= 71 (healthy grade); no automatic changes",
            "score": result.normalized,
            "diffs": result.gaps or ["(no gaps detected)"],
            "next_step": "run `python harness/bin/creator.py fix <dimension>` to apply changes explicitly",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    summary = {
        "mode": "not-implemented",
        "score": result.normalized,
        "grade": result.grade,
        "note": "MVP only supports dry-run at healthy grade; bare/gapped build flow not implemented yet",
        "gaps": result.gaps,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2


def cmd_fix(root: Path, dimension: str) -> int:
    if dimension not in DIMENSIONS:
        log.error("unknown dimension '%s'; valid: %s", dimension, DIMENSIONS)
        return 2
    summary = {
        "mode": "not-implemented",
        "dimension": dimension,
        "note": "MVP does not implement automated fix; see audit report for manual remediation.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="creator", description="harness-creator engine")
    parser.add_argument("--root", type=Path, default=None, help="project root (auto-detected if omitted)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="score current infrastructure and write report")
    sub.add_parser("build", help="act based on score (dry-run at healthy grade)")
    fix_parser = sub.add_parser("fix", help="target a single dimension")
    fix_parser.add_argument("dimension", choices=DIMENSIONS)

    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else find_project_root()

    if args.command == "audit":
        return cmd_audit(root)
    if args.command == "build":
        return cmd_build(root)
    if args.command == "fix":
        return cmd_fix(root, args.dimension)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
