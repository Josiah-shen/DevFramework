"""failure_classifier 的单元测试。

每条 DETERMINISTIC_PATTERNS 规则至少覆盖一条正向（应命中）用例和一条反向
（类似但不该命中）用例，外加空输入、多模式共存两条综合用例。
"""

from __future__ import annotations

import pytest

from harness.lib.failure_classifier import (
    DETERMINISTIC_PATTERNS,
    is_deterministic_failure,
)

# ---------- 正向用例：(output, expected_reason) ----------

POSITIVE_CASES: list[tuple[str, str]] = [
    # java-compile-error
    (
        "src/Foo.java:10: error: ';' expected\njavac: error: compilation failed",
        "java-compile-error",
    ),
    # py-syntax-error
    (
        '  File "foo.py", line 3\n    def bar(:\n            ^\nSyntaxError: invalid syntax',
        "py-syntax-error",
    ),
    # py-module-missing
    (
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'foo'",
        "py-module-missing",
    ),
    # ts-compile-error
    (
        "src/index.ts(5,10): error TS2304: Cannot find name 'foo'.",
        "ts-compile-error",
    ),
    # npm-missing
    (
        "npm ERR! code E404\nnpm ERR! 404 Not Found - GET https://registry.npmjs.org/foo",
        "npm-missing",
    ),
    # make-missing-target
    (
        "make: *** No rule to make target 'frobnicate'.  Stop.",
        "make-missing-target",
    ),
]

# ---------- 反向用例：相似但不应命中 ----------

NEGATIVE_CASES: list[str] = [
    # 只是日志里提到 "error"，没有 javac/COMPILATION ERROR/cannot find symbol
    "INFO: build finished with 0 error, 0 warning",
    # 普通文本包含 "Syntax" 但不是 SyntaxError:
    "Docs section: Syntax overview",
    # 提到 ModuleNotFound 但不是抛错行
    "This test verifies ModuleNotFound behavior is graceful",
    # TS 字样但没有 error TS\d+: 结构
    "info: TS compiler started",
    # npm 报错但不是 E404 / missing script
    "npm ERR! network timeout at: https://registry.npmjs.org/foo",
    # 讨论 make 的文档，没有 "No rule to make target"
    "make: entering directory '/tmp/build'",
]


@pytest.mark.parametrize("output,expected", POSITIVE_CASES)
def test_positive_cases_hit(output: str, expected: str) -> None:
    assert is_deterministic_failure(output) == expected


@pytest.mark.parametrize("output", NEGATIVE_CASES)
def test_negative_cases_miss(output: str) -> None:
    assert is_deterministic_failure(output) is None


def test_empty_string_returns_none() -> None:
    assert is_deterministic_failure("") is None


def test_multiple_patterns_returns_first_in_list_order() -> None:
    """当输入同时包含多个规则的触发词时，按 DETERMINISTIC_PATTERNS 列表顺序返回第一个命中。

    列表顺序为：java → py-syntax → py-module → ts → npm → make。
    构造同时含 py-module-missing 与 ts-compile-error 的输出，预期返回 py-module-missing。
    """
    output = (
        "ModuleNotFoundError: No module named 'foo'\n"
        "src/index.ts(1,1): error TS2307: Cannot find module 'bar'."
    )
    assert is_deterministic_failure(output) == "py-module-missing"


def test_patterns_list_is_non_empty() -> None:
    """冒烟测试：避免空列表退化为 is_deterministic_failure 永远返回 None。"""
    assert len(DETERMINISTIC_PATTERNS) >= 6
