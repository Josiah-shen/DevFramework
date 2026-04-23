#!/usr/bin/env python3
"""代码规范检查 — 文件行数 ≤500、文件名 kebab-case、禁止 console.log/print() 等调试输出。

分级：
- error（默认，影响 exit code）: console.log / print( / console.warn/error/debug
- warning（两阶段发布中，不影响 exit code）: System.out.println、System.err.println、
  .printStackTrace(

升级记录：
- 前端已完成 logger 迁移（src/frontend/src/utils/logger.js），所有 console.warn/error
  已替换为 logger.xxx。因此将 console.(warn|error|debug) 从 warning 升级为 error，
  阻塞后续回潮。Java 侧（System.out/err.println、printStackTrace）后端尚未清理，
  保持 warning 级别。

白名单：
- src/frontend/src/utils/logger.js 是日志基础设施的底层实现，允许调用 console
  是设计选择（需要按 level 动态分发）。显式加白名单是防御性措施：即使未来有人
  把 console[level] 重构成 console.error 直调，也不应被误报。白名单只针对
  logger.js 这一个文件，不扩大适用范围。

命令行：
  python3 style.py       # warning 仅提示，不影响 exit code
  python3 style.py -W    # 把 warning 也升级为 error
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent

SOURCE_DIRS = {"types", "utils", "config", "core", "api", "cli", "ui"}
_BACKEND_JAVA_ROOT = "src/backend/src/main/java/com/xptsqas/"
_FRONTEND_SRC_ROOT = "src/frontend/src/"
EXTENSIONS = {".java", ".ts", ".js", ".vue", ".py"}
MAX_LINES = 500
EXEMPT_STEMS = {"__init__", "index"}

# error 级别：已稳定落地的规则（含前端 logger 迁移完成后升级的 console.warn/error/debug）
FORBIDDEN_ERROR_RE = re.compile(
    r"\bconsole\.log\b"
    r"|\bprint\s*\("
    r"|\bconsole\.(warn|error|debug)\b"
)
# warning 级别：两阶段发布中，先浮现既有违规，稳定后再升级为 error
FORBIDDEN_WARNING_RE = re.compile(
    r"\bSystem\.out\.println\b"
    r"|\bSystem\.err\.println\b"
    r"|\.printStackTrace\s*\("
)
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 日志基础设施白名单：该文件是 logger 的底层实现，允许调用 console。
# 只针对这一个文件，不扩大适用范围。
_FORBIDDEN_PRINT_ALLOWLIST = frozenset({"src/frontend/src/utils/logger.js"})

_COMMENT_MARKER = {
    ".py": "#",
    ".java": "//", ".ts": "//", ".js": "//", ".vue": "//",
}


def _code_part(line: str, ext: str) -> str:
    marker = _COMMENT_MARKER.get(ext, "")
    if marker:
        idx = line.find(marker)
        if idx != -1:
            return line[:idx]
    return line


def _is_source_file(rel: str) -> bool:
    parts = rel.split("/")
    return (
        bool(parts and parts[0] in SOURCE_DIRS)
        or rel.startswith(_BACKEND_JAVA_ROOT)
        or rel.startswith(_FRONTEND_SRC_ROOT)
    )


def _in_scope(rel: str, scope: set[str] | None) -> bool:
    """判断仓库相对路径 rel 是否落在 scope 声明范围内。

    - scope 为 None：全仓视为 "in scope"，保持旧行为。
    - 条目以 "/" 结尾：按目录前缀匹配。
    - 条目无 "/" 结尾：
        * 若仓库中对应一个目录，亦按目录前缀匹配；
        * 否则按精确文件路径匹配。
    匹配只做路径层面，不解析 glob。
    """
    if scope is None:
        return True
    if rel in scope:
        return True
    for entry in scope:
        if entry.endswith("/") and rel.startswith(entry):
            return True
        # 目录条目（无尾斜杠但实际是目录）：按前缀匹配
        if not entry.endswith("/"):
            candidate = (ROOT / entry)
            if candidate.is_dir() and rel.startswith(entry + "/"):
                return True
    return False


def check(strict: bool = False, scope: set[str] | None = None):
    """返回 (是否通过, 消息列表[, 范围外遗留])。

    参数：
    - strict=False（默认）：warning 级违规不影响 ok 标志
    - strict=True：warning 级违规也视为失败
    - scope=None：保持旧行为，全仓违规均纳入 msgs 并影响 ok；**返回 2 元组**（向后兼容）
    - scope 非 None：
        * 范围内违规进入 msgs，影响 ok；
        * 范围外违规进入 debt，不影响 ok；
        * **返回 3 元组** (ok, msgs, debt)。

    检查逻辑本身（行数、命名、禁用调用）不变，仅按 scope 分流归属。
    """
    errors: list[str] = []
    warnings: list[str] = []
    debt_errors: list[str] = []
    debt_warnings: list[str] = []

    for ext in EXTENSIONS:
        for path in ROOT.rglob(f"*{ext}"):
            rel = path.relative_to(ROOT).as_posix()
            if not _is_source_file(rel):
                continue

            in_scope = _in_scope(rel, scope)
            err_bucket = errors if in_scope else debt_errors
            warn_bucket = warnings if in_scope else debt_warnings

            stem = path.stem

            if ext not in {".java", ".vue"} and stem not in EXEMPT_STEMS and not KEBAB_RE.match(stem):
                err_bucket.append(f"{rel}: 文件名非 kebab-case（{stem}）")

            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            if len(lines) > MAX_LINES:
                err_bucket.append(f"{rel}: 超过 {MAX_LINES} 行（实际 {len(lines)} 行）")

            # 日志基础设施文件（如 logger.js）允许调用 console，跳过 FORBIDDEN 检查
            skip_forbidden = rel in _FORBIDDEN_PRINT_ALLOWLIST

            for i, line in enumerate(lines, 1):
                code = _code_part(line, ext)
                if skip_forbidden:
                    continue
                if FORBIDDEN_ERROR_RE.search(code):
                    err_bucket.append(
                        f"[style/no-debug-print] {rel}:{i} — 禁止 console.log / print() / "
                        f"console.warn/error/debug，建议：改用结构化日志（logger）"
                    )
                elif FORBIDDEN_WARNING_RE.search(code):
                    warn_bucket.append(
                        f"[style/no-debug-print][warning] {rel}:{i} — 禁止 "
                        f"System.out/err.println、printStackTrace()，建议：改用结构化日志（logger）"
                    )

    msgs = errors + warnings
    debt = debt_errors + debt_warnings
    if strict:
        ok = len(msgs) == 0
    else:
        ok = len(errors) == 0

    if scope is None:
        # 向后兼容：未传 scope 时保持 2 元组返回。
        return ok, msgs
    return ok, msgs, debt


if __name__ == "__main__":
    strict = "-W" in sys.argv[1:]
    ok, msgs = check(strict=strict)
    if msgs:
        label = "❌ 代码规范检查失败：" if not ok else "⚠️  代码规范检查有警告（不阻塞）："
        print(label)
        for m in msgs:
            print(f"  {m}")
    if ok:
        print("✅ 代码规范检查通过")
        sys.exit(0)
    sys.exit(1)
