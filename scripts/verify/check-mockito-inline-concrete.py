#!/usr/bin/env python3
"""check-mockito-inline-concrete — 检测 @Mock/@Spy/mock()/spy() 目标为项目内具体类。

规则 ID：testing/mockito-inline-on-concrete-class
阶段：warning（非阻塞，exit code 恒为 0）
晋升条件：连续 10 次构建零误报后由人工升级为 error 级别。

背景
----
JDK 23 + Mockito inline mock-maker（mockito-core < 5.x）无法 mock 项目内具体类。
错误指纹：`Could not modify all classes [..., class java.lang.Object]`
         + `Mockito cannot mock this class`
         + `Java : 23 / JVM vendor: Homebrew`
跨任务复现 4 次（v2 + v3 + v3-purge），critic 报告三条件全满足。

检测算法（宁漏勿报）
--------------------
扫描 src/backend/src/test/**/*.java：
1. 找到 `@Mock` 或 `@Spy` 注解修饰的字段声明，提取字段类型名。
   注解可能在字段声明的同一行或前一行。
2. 找到 `mock(XxxClass.class)` / `spy(XxxClass.class)` 调用，提取类型名。
3. 对每个候选类型名，判断是否为项目内具体类（非接口、非 abstract）：
   - 在 src/backend/src/main/java/ 下搜索对应 .java 文件
   - 读取文件，检查是否含 `interface ` 或 `abstract class ` 声明
   - 如果是具体类（既非 interface 也非 abstract），输出 warning
4. 排除 JDK/三方库类型（类型名不在项目 src/main 下的跳过）

合法例外
--------
- 类型名在 src/main 下找不到对应 .java 文件（三方库/JDK 类型）
- 类型为 interface 或 abstract class
- 泛型参数中的类型（如 `List<Foo>`）不单独检查
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
TEST_ROOT = ROOT / "src" / "backend" / "src" / "test"
MAIN_ROOT = ROOT / "src" / "backend" / "src" / "main" / "java"
RULE_ID = "testing/mockito-inline-on-concrete-class"

# @Mock 或 @Spy 注解（可能带参数如 @Mock(answer = ...)）
_ANNOTATION_RE = re.compile(r"^\s*@(Mock|Spy)\b")

# 字段声明：提取类型名（支持泛型擦除，取最外层类型）
# 匹配形如 `private SomeType fieldName;` 或 `SomeType<Generic> fieldName;`
_FIELD_DECL_RE = re.compile(
    r"^\s*(?:private|protected|public)?\s*"
    r"([A-Z][A-Za-z0-9_]*)"  # 类型名（首字母大写）
    r"(?:<[^>]*>)?"           # 可选泛型参数
    r"\s+\w+\s*;"             # 字段名 + 分号
)

# mock(Xxx.class) / spy(Xxx.class) / Mockito.mock(Xxx.class) / Mockito.spy(Xxx.class)
_MOCK_CALL_RE = re.compile(
    r"(?:\bMockito\.)?\b(?:mock|spy)\(\s*([A-Z][A-Za-z0-9_]*)\.class\s*[,)]"
)

# 缓存：类型名 -> 是否为项目内具体类（True=具体类, False=接口/abstract, None=不在项目内）
_type_cache: dict[str, bool | None] = {}


def _is_concrete_project_class(type_name: str) -> bool | None:
    """判断类型是否为项目内具体类。返回 True/False/None（不在项目内）。"""
    if type_name in _type_cache:
        return _type_cache[type_name]

    if not MAIN_ROOT.is_dir():
        _type_cache[type_name] = None
        return None

    candidates = list(MAIN_ROOT.rglob(f"{type_name}.java"))
    if not candidates:
        _type_cache[type_name] = None
        return None

    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # 检查是否为 interface 或 abstract class
        if re.search(r"\binterface\s+" + re.escape(type_name) + r"\b", content):
            _type_cache[type_name] = False
            return False
        if re.search(r"\babstract\s+class\s+" + re.escape(type_name) + r"\b", content):
            _type_cache[type_name] = False
            return False
        # 确认是 class 声明（排除 enum 等）
        if re.search(r"\bclass\s+" + re.escape(type_name) + r"\b", content):
            _type_cache[type_name] = True
            return True

    # 文件存在但未匹配到 class/interface/abstract（可能是 enum 等），保守跳过
    _type_cache[type_name] = None
    return None

def _iter_test_files() -> list[Path]:
    if not TEST_ROOT.is_dir():
        return []
    return sorted(TEST_ROOT.rglob("*.java"))


def check() -> list[str]:
    """返回违规告警列表（可能为空）。"""
    warnings: list[str] = []
    _type_cache.clear()

    for path in _iter_test_files():
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = raw.splitlines()

        pending_annotation_line: int | None = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 跳过注释行
            if stripped.startswith("//") or stripped.startswith("*"):
                pending_annotation_line = None
                continue

            # 检测 @Mock / @Spy 注解
            if _ANNOTATION_RE.match(line):
                pending_annotation_line = i
                continue

            # 如果上一行是 @Mock/@Spy，当前行应该是字段声明
            if pending_annotation_line is not None:
                m = _FIELD_DECL_RE.match(line)
                if m:
                    type_name = m.group(1)
                    is_concrete = _is_concrete_project_class(type_name)
                    if is_concrete is True:
                        rel = path.relative_to(ROOT).as_posix()
                        warnings.append(
                            f"[{RULE_ID}] {rel}:{i + 1} — "
                            f"@Mock/@Spy 目标 {type_name} 是项目内具体类，"
                            f"JDK 23 + 当前 mockito 版本下 inline mock "
                            f"失败的高风险点，建议改 mock 接口或真实实例"
                            f" + spy 底层依赖"
                        )
                pending_annotation_line = None

            # 检测 mock(Xxx.class) / spy(Xxx.class) 调用
            for m in _MOCK_CALL_RE.finditer(line):
                type_name = m.group(1)
                is_concrete = _is_concrete_project_class(type_name)
                if is_concrete is True:
                    rel = path.relative_to(ROOT).as_posix()
                    warnings.append(
                        f"[{RULE_ID}] {rel}:{i + 1} — "
                        f"mock()/spy() 目标 {type_name} 是项目内具体类，"
                        f"JDK 23 + 当前 mockito 版本下 inline mock "
                        f"失败的高风险点，建议改 mock 接口或真实实例"
                        f" + spy 底层依赖"
                    )

    return warnings


def main() -> int:
    warns = check()
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