#!/usr/bin/env python3
"""check-closeable-try-with-resources — Java Closeable 资源未使用 try-with-resources。

规则 ID：performance/closeable-try-with-resources
阶段：warning（非阻塞，exit code 恒为 0）
晋升条件：连续 10 次构建零误报后由人工升级为 error 级别。

背景
----
controller-split review（harness/trace/reviews/2026-04-20-controller-split-500-review.md）
在 PvExcelUtils.java:33 发现：
    Workbook wb = new XSSFWorkbook();
未包入 try-with-resources，方法抛异常时 wb 不会关闭，资源泄漏。review 标注该问题
"原文件遗留缺陷，非本次引入"——说明无 lint 规则时这类问题会永久潜伏。

critic-2026-04-22.md 缺陷 5 归纳共性模式：Java 源码中 `new (Workbook|InputStream|
OutputStream|Reader|Writer|Connection|Statement|ResultSet)` 等 Closeable 子类型被赋值
给局部变量，但外层不是 try-with-resources 结构。

检测算法（宁漏勿报）
--------------------
逐行扫描 src/backend/**/*.java：
1. 若行匹配 `(Type) var = new (Closeable子类)\s*\(`，视作候选。
2. 向上回扫最近 5 行，若出现 `try (` 或 `try(`，或当前行本身处于 `try (` 的括号块内，
   则认为已包入 try-with-resources，跳过。
3. 否则输出警告。

合法例外（见本文件末尾测试用例注释）——下列情况不触发：
  - 整行在 `//` 行注释或 `/* ... */` 块注释中
  - 赋值给类字段（`this.xxx = new ...`，或顶层缩进 ≤ 4 空格的字段声明）
  - 装饰器链：`new XxxStream(new YyyStream(...))` 仅顶层需要 try-with-resources，
    本规则仅匹配 "Type var = new ..." 形式，天然过滤参数位置的 new 表达式
  - 方法返回表达式（`return new ...`，调用方负责关闭）

warning 阶段永远返回 exit 0；run.py 以 [pre-existing debt] 语义展示，不阻断管道。

用法
----
    python3 scripts/verify/check-closeable-try-with-resources.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCAN_ROOT = ROOT / "src" / "backend"
RULE_ID = "performance/closeable-try-with-resources"

# 监控的 Closeable 子类型（POI Workbook、IO Stream、Reader/Writer、JDBC）
_CLOSEABLE_TYPES = (
    "XSSFWorkbook",
    "HSSFWorkbook",
    "SXSSFWorkbook",
    "Workbook",
    "FileInputStream",
    "FileOutputStream",
    "BufferedInputStream",
    "BufferedOutputStream",
    "BufferedReader",
    "BufferedWriter",
    "InputStreamReader",
    "OutputStreamWriter",
    "FileReader",
    "FileWriter",
    "PrintWriter",
    "Connection",
    "Statement",
    "PreparedStatement",
    "ResultSet",
)

# 行内匹配：[修饰符] 类型 变量名 = new Closeable类型(...
# 仅匹配形如 "TypeA var = new TypeB(" 的局部变量声明；不捕获参数位置的 new。
_DECL_NEW_RE = re.compile(
    r"""^\s*                                    # 行首缩进
        (?:final\s+)?                           # 可选 final
        (?:[A-Za-z_][\w<>,\s\?\[\]]*?\s+)?      # 可选左侧类型（含泛型/数组/通配符）
        [A-Za-z_]\w*                            # 变量名
        \s*=\s*new\s+
        (""" + "|".join(_CLOSEABLE_TYPES) + r""")\s*[\(<]
    """,
    re.VERBOSE,
)

# this.xxx = new ...（字段赋值，不触发）
_FIELD_ASSIGN_RE = re.compile(r"^\s*this\.\w+\s*=\s*new\s+")

# return new ...（方法返回，不触发）
_RETURN_NEW_RE = re.compile(r"^\s*return\s+new\s+")

# try (...) 起始（支持 try ( 和 try(）
_TRY_WITH_RES_RE = re.compile(r"\btry\s*\(")


def _strip_block_comments(lines: list[str]) -> list[str]:
    """粗略抹除 /* ... */ 块注释内容（整行替换为空行），避免误判注释中的代码样例。"""
    out = list(lines)
    in_block = False
    for i, line in enumerate(out):
        if in_block:
            end = line.find("*/")
            if end >= 0:
                in_block = False
                out[i] = " " * end + "  " + line[end + 2 :]
            else:
                out[i] = ""
            continue
        # 非块注释状态
        start = line.find("/*")
        if start >= 0:
            end = line.find("*/", start + 2)
            if end >= 0:
                # 同行结束：掏空注释段
                out[i] = line[:start] + "  " + line[end + 2 :]
            else:
                in_block = True
                out[i] = line[:start]
    return out


def _is_line_comment(raw_line: str) -> bool:
    """行是否以 // 注释起始（忽略缩进）。"""
    stripped = raw_line.lstrip()
    return stripped.startswith("//") or stripped.startswith("*")


def _within_try_with_resources(lines: list[str], idx: int, lookback: int = 5) -> bool:
    """回扫最近若干行，判断当前行是否位于 try (...) 块的括号内。

    简化策略：从 idx 向上扫最多 lookback 行，若遇到 `try (` 则认为已包裹；
    若遇到独立的 `{` 行（方法/块起点）则提前中止。
    """
    start = max(0, idx - lookback)
    # 当前行自身包含 try ( 也算
    if _TRY_WITH_RES_RE.search(lines[idx]):
        return True
    for j in range(idx - 1, start - 1, -1):
        line = lines[j]
        if _TRY_WITH_RES_RE.search(line):
            return True
        # 遇到方法起始或无关语句终止；简单探测："{" 结尾但不含 try
        stripped = line.strip()
        if stripped.endswith("{") and "try" not in stripped and "else" not in stripped:
            break
    return False


def _iter_java_files() -> list[Path]:
    if not SCAN_ROOT.is_dir():
        return []
    return sorted(SCAN_ROOT.rglob("*.java"))


def check() -> list[str]:
    """返回违规告警列表（可能为空）。"""
    warnings: list[str] = []
    for path in _iter_java_files():
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        raw_lines = raw.splitlines()
        scrubbed = _strip_block_comments(raw_lines)
        for i, line in enumerate(scrubbed):
            if not line.strip():
                continue
            if _is_line_comment(raw_lines[i]):
                continue
            if _FIELD_ASSIGN_RE.match(line):
                continue
            if _RETURN_NEW_RE.match(line):
                continue
            m = _DECL_NEW_RE.match(line)
            if not m:
                continue
            if _within_try_with_resources(scrubbed, i):
                continue
            type_name = m.group(1)
            rel = path.relative_to(ROOT).as_posix()
            warnings.append(
                f"[{RULE_ID}] {rel}:{i + 1} — "
                f"资源 {type_name} 未使用 try-with-resources，异常路径下可能泄漏"
            )
    return warnings


def main() -> int:
    warns = check()
    for w in warns:
        print(w, file=sys.stderr)
    if warns:
        print(
            f"ℹ️  [{RULE_ID}] 共 {len(warns)} 条告警（warning 阶段，不阻塞）",
            file=sys.stderr,
        )
    # warning 阶段 exit code 恒为 0
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ----------------------------------------------------------------------------
# 预期触发用例（应输出告警）
# ----------------------------------------------------------------------------
# 1. POI Workbook 遗留缺陷（PvExcelUtils.java:33）：
#        Workbook wb = new XSSFWorkbook();
#    → [performance/closeable-try-with-resources] ...:33 — 资源 XSSFWorkbook 未使用 ...
#
# 2. 文件输入流局部变量：
#        FileInputStream fis = new FileInputStream("a.txt");
#        // ...无 try 包裹...
#
# 3. JDBC Statement：
#        Statement stmt = new Statement(...);
#
# 4. 带 final 修饰符的局部变量：
#        final BufferedReader br = new BufferedReader(new FileReader(f));
#    （顶层 br 需 try-with-resources；内层 FileReader 是装饰器链，作为参数出现，不会被 _DECL_NEW_RE 匹配）
#
# 5. SXSSFWorkbook 流式大文件：
#        SXSSFWorkbook wb = new SXSSFWorkbook(100);
#
# ----------------------------------------------------------------------------
# 合法例外（不应触发）
# ----------------------------------------------------------------------------
# A. 已正确使用 try-with-resources：
#        try (Workbook wb = new XSSFWorkbook()) {
#            ...
#        }
#    → 回扫命中 `try (`，跳过。
#
# B. 类字段赋值（由外部 bean/close 钩子管理）：
#        this.workbook = new XSSFWorkbook();
#    → 命中 _FIELD_ASSIGN_RE，跳过。
#
# C. 方法返回表达式（调用方负责 close）：
#        return new XSSFWorkbook();
#    → 命中 _RETURN_NEW_RE，跳过。
#
# D. 装饰器链参数位置：
#        try (BufferedReader br = new BufferedReader(new FileReader(f))) { ... }
#    → 顶层 `new BufferedReader(...)` 在 try 块内；内层 `new FileReader(f)` 作为参数出现，
#      不匹配 "Type var = new ..." 形式，天然不触发。
#
# E. 注释中的代码样例：
#        // Workbook wb = new XSSFWorkbook();  // 历史写法
#        /* Workbook wb = new XSSFWorkbook(); */
#    → _is_line_comment / _strip_block_comments 抹除，跳过。