#!/usr/bin/env python3
"""check-exec-plan-paths — exec-plan 中引用的项目路径不存在。

规则 ID：process/exec-plan-path-existence
阶段：warning（非阻塞，exit code 恒为 0）
晋升条件：连续 10 次构建零误报后由人工升级为 error 级别。

背景
----
v3-purge plan 中 PRD 路径写成假的 `v0_3_xxx.md`（实际是 `03_碳分析.md`）；
critic-2026-04-28 C5 同根因。跨任务复现 2 次，三条件全满足。

检测算法（宁漏勿报）
--------------------
扫描 harness/exec-plans/*.md，提取反引号或 Markdown 链接中以
docs/ / src/ / harness/ / scripts/ / tests/ 开头的路径引用，
对每条路径做 Path(ROOT / p).exists() 校验。
不存在时在同级目录下做 fuzzy match 推荐。

合法例外（不触发）：
  - URL（http:// / https://）
  - glob 模式（含 * 或 ?）—— 但对 glob 前缀目录做存在性校验
  - 代码块（``` 围栏）内的路径
  - 不以项目前缀开头的路径

warning 阶段永远返回 exit 0；run.py 以 warning 语义展示，不阻断管道。

用法
----
    python3 scripts/verify/check-exec-plan-paths.py
    python3 scripts/verify/check-exec-plan-paths.py --slug report-version-v3-purge
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLANS_DIR = ROOT / "harness" / "exec-plans"
RULE_ID = "process/exec-plan-path-existence"

# 只校验以这些前缀开头的路径（项目内路径）
_PROJECT_PREFIXES = ("docs/", "src/", "harness/", "scripts/", "tests/")

# 反引号包裹的路径：`some/path/here`
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]+)`")

# Markdown 链接中的路径：[text](some/path/here)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _is_url(path: str) -> bool:
    return path.startswith("http://") or path.startswith("https://")


def _is_glob(path: str) -> bool:
    return "*" in path or "?" in path


def _is_project_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _PROJECT_PREFIXES)


def _is_template_or_command(path: str) -> bool:
    """检测路径是否为模板占位符、shell 命令或非路径内容。"""
    # 含 shell 变量 $(...) 或 ${...} 或 $VAR
    if "$" in path:
        return True
    # 含花括号模板 {slug} 等
    if "{" in path and "}" in path:
        return True
    # 含管道符 | （shorthand 如 PRD|BDD|RID）
    if "|" in path:
        return True
    # 含空格（命令行参数、描述性文本）
    if " " in path:
        return True
    # 含 << （heredoc）
    if "<<" in path:
        return True
    return False


def _strip_trailing_annotations(path: str) -> str:
    """去掉路径后面常见的注释标记，如 `path/to/file.java`（新增）。"""
    # 去掉尾部的中文括号注释
    path = re.sub(r"[（(][^)）]*[)）]$", "", path)
    # 去掉尾部空白
    return path.strip()

def _fuzzy_suggest(path: str) -> str:
    """在同级目录下做 fuzzy match，返回建议文案。"""
    full = ROOT / path
    parent = full.parent
    if not parent.is_dir():
        # 父目录都不存在，尝试向上找最近存在的祖先
        ancestor = parent
        while ancestor != ROOT and not ancestor.is_dir():
            ancestor = ancestor.parent
        if ancestor == ROOT:
            return "请检查路径拼写"
        # 在最近存在的祖先下找相似
        target_name = full.relative_to(ancestor).as_posix()
        candidates = [
            p.relative_to(ancestor).as_posix()
            for p in ancestor.rglob("*")
            if p.is_file()
        ][:200]  # 限制搜索量
        matches = difflib.get_close_matches(target_name, candidates, n=1, cutoff=0.5)
        if matches:
            suggestion_path = (ancestor / matches[0]).relative_to(ROOT).as_posix()
            return f"是否指 `{suggestion_path}`？"
        return "请检查路径拼写"

    # 父目录存在，在同级找相似文件名
    target_name = full.name
    siblings = [p.name for p in parent.iterdir()]
    matches = difflib.get_close_matches(target_name, siblings, n=1, cutoff=0.5)
    if matches:
        suggestion_path = (parent / matches[0]).relative_to(ROOT).as_posix()
        return f"是否指 `{suggestion_path}`？"
    return "请检查路径拼写"


def _extract_paths_from_line(line: str) -> list[str]:
    """从单行提取所有候选路径引用。"""
    paths: list[str] = []
    # 反引号路径
    for m in _BACKTICK_PATH_RE.finditer(line):
        paths.append(m.group(1))
    # Markdown 链接路径
    for m in _MD_LINK_RE.finditer(line):
        paths.append(m.group(1))
    return paths


def _check_glob_prefix(path: str) -> str | None:
    """对 glob 模式，校验其前缀目录是否存在。返回 None 表示通过，否则返回不存在的前缀。"""
    # 找到第一个 glob 字符的位置，取其前面的目录部分
    idx = min(
        (path.index(c) for c in ("*", "?") if c in path),
        default=len(path),
    )
    prefix = path[:idx]
    # 取到最后一个 / 为止
    last_slash = prefix.rfind("/")
    if last_slash <= 0:
        return None  # 前缀太短，不校验
    dir_path = prefix[: last_slash]
    if not (ROOT / dir_path).is_dir():
        return dir_path
    return None

def check(slug: str | None = None) -> list[str]:
    """返回违规告警列表（可能为空）。

    Parameters
    ----------
    slug : str | None
        如果传了 slug，只扫描 harness/exec-plans/{slug}.md；
        如果没传，扫描 harness/exec-plans/*.md 全部。
    """
    if not PLANS_DIR.is_dir():
        return []

    if slug:
        plan_file = PLANS_DIR / f"{slug}.md"
        if not plan_file.is_file():
            return []
        plan_files = [plan_file]
    else:
        plan_files = sorted(PLANS_DIR.glob("*.md"))

    warnings: list[str] = []

    for plan in plan_files:
        try:
            content = plan.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        in_code_block = False

        for line_no, line in enumerate(lines, start=1):
            # 跟踪代码围栏状态
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            candidates = _extract_paths_from_line(line)
            for raw_path in candidates:
                path = _strip_trailing_annotations(raw_path)

                # 过滤 URL
                if _is_url(path):
                    continue

                # 过滤非项目路径
                if not _is_project_path(path):
                    continue

                # 过滤模板、shell 命令等非真实路径
                if _is_template_or_command(path):
                    continue

                rel_plan = plan.relative_to(ROOT).as_posix()

                # glob 模式：只校验前缀目录
                if _is_glob(path):
                    bad_prefix = _check_glob_prefix(path)
                    if bad_prefix:
                        suggestion = _fuzzy_suggest(bad_prefix)
                        warnings.append(
                            f"[{RULE_ID}] {rel_plan}:{line_no} — "
                            f"引用路径 `{path}` 的前缀目录 `{bad_prefix}` 不存在；"
                            f"建议：{suggestion}"
                        )
                    continue

                # 普通路径：直接校验存在性
                if not (ROOT / path).exists():
                    suggestion = _fuzzy_suggest(path)
                    warnings.append(
                        f"[{RULE_ID}] {rel_plan}:{line_no} — "
                        f"引用路径 `{path}` 不存在；建议：{suggestion}"
                    )

    return warnings

def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查 exec-plan 中引用的项目路径是否存在"
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="只扫描指定 slug 的 exec-plan 文件",
    )
    args = parser.parse_args()

    warns = check(slug=args.slug)
    for w in warns:
        print(w, file=sys.stderr)
    if warns:
        print(
            f"[{RULE_ID}] 共 {len(warns)} 条告警（warning 阶段，不阻塞）",
            file=sys.stderr,
        )
    # warning 阶段 exit code 恒为 0
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ----------------------------------------------------------------------------
# 预期触发用例（应输出告警）
# ----------------------------------------------------------------------------
# 1. 反引号路径不存在：
#        `docs/design-docs/PRD/v0_3_xxx.md`
#    → [process/exec-plan-path-existence] ...:{line} — 引用路径 ... 不存在；
#      建议：是否指 `docs/design-docs/PRD/03_碳分析.md`？
#
# 2. Markdown 链接路径不存在：
#        [PRD](docs/design-docs/PRD/nonexistent.md)
#    → 同上
#
# 3. 影响范围段中的路径不存在：
#        - `src/backend/src/main/java/com/xxx/NonExistent.java`
#    → 同上
#
# 4. glob 模式前缀目录不存在：
#        `src/backend/src/main/java/com/xxx/nonexistent/*.java`
#    → 前缀目录不存在告警
#
# ----------------------------------------------------------------------------
# 合法例外（不应触发）
# ----------------------------------------------------------------------------
# A. URL：
#        `https://example.com/docs/foo`
#    → 跳过
#
# B. 非项目路径：
#        `node_modules/foo/bar.js`
#    → 不以项目前缀开头，跳过
#
# C. 代码块内的路径：
#        ```yaml
#        path: docs/nonexistent.md
#        ```
#    → 在围栏内，跳过
#
# D. glob 模式但前缀目录存在：
#        `src/backend/src/main/java/com/xptsqas/**/*.java`
#    → 前缀目录存在，跳过
#
# E. 路径实际存在：
#        `docs/ARCHITECTURE.md`
#    → 存在，跳过