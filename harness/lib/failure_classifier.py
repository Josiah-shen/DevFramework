"""确定性失败分类器。

识别"重试无意义"的失败类别（编译错、语法错、模块缺失等），供 validate.py
在观察到失败输出后立即早退，避免浪费剩余重试轮次。

扩展规则：在 DETERMINISTIC_PATTERNS 追加 (pattern, reason) 元组，reason 使用
kebab-case 以便日志聚合。新增规则同时在 harness/tests/test_failure_classifier.py
补一条正向用例和一条反向用例。
"""

from __future__ import annotations

import re
from typing import Optional

# 顺序即优先级：越靠前的规则命中越早返回。仅收录重试不会改变结果的失败类别，
# 网络抖动、资源争用导致的错误不在此列（ADR-1）。
DETERMINISTIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Java/Maven 编译错
    (
        re.compile(
            r"(javac.*error:|\[ERROR\].*cannot find symbol|COMPILATION ERROR)",
            re.IGNORECASE,
        ),
        "java-compile-error",
    ),
    # Python 语法错 / 缩进错
    (
        re.compile(r"(SyntaxError:|IndentationError:)"),
        "py-syntax-error",
    ),
    # Python 模块缺失
    (
        re.compile(r"(ModuleNotFoundError:|ImportError: No module named)"),
        "py-module-missing",
    ),
    # TypeScript 编译错
    (
        re.compile(r"error TS\d+:"),
        "ts-compile-error",
    ),
    # npm/pnpm 找不到脚本或包
    (
        re.compile(r"npm ERR! (code E404|missing script)"),
        "npm-missing",
    ),
    # Make 目标不存在
    (
        re.compile(r"make: \*\*\* No rule to make target"),
        "make-missing-target",
    ),
]


def is_deterministic_failure(output: str) -> Optional[str]:
    """若输出命中确定性失败模式，返回对应 reason（kebab-case），否则返回 None。

    使用 pattern.search 匹配长输出中的任意一行（ADR-3），不要求整段匹配。
    """
    if not output:
        return None
    for pattern, reason in DETERMINISTIC_PATTERNS:
        if pattern.search(output):
            return reason
    return None
