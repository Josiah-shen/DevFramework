#!/usr/bin/env python3
"""check-spring-self-invocation — Spring bean 类内 self-invocation 代理失效检测。

规则 ID：logic/spring-self-invocation-transactional
阶段：warning（非阻塞，exit code 恒为 0）
晋升条件：连续 10 次构建零误报后由人工升级为 error 级别。

背景
----
Spring @Transactional / @Async / @Cacheable 等注解依赖 AOP 代理拦截方法调用。
当同一类内的方法 A 直接调用标注了这些注解的方法 B 时（self-invocation），调用
不经过代理，注解不生效。v3-purge 任务中 codex-reviewer 一轮漏检此问题。
SonarJava S5808/S5803 有同类规则。

检测算法（宁漏勿报）
--------------------
逐文件扫描 src/backend/src/main/java/**/*.java：
1. 识别 Spring bean 类：文件含 @Component/@Service/@Repository/@Controller/@RestController
2. 收集标注了 @Transactional/@Async/@Cacheable/@CacheEvict/@CachePut 的方法名
3. 对每个未标注上述注解的方法体，扫描是否直接调用了 annotated_methods 中的方法
4. 匹配 this.methodName( 或裸 methodName( 调用（排除 super./otherObj./static 调用）
5. 排除注释中的调用

合法例外（不触发）：
  - 调用方自身也标注了同类注解
  - 注释中的调用
  - static 方法调用
  - 通过其他对象调用（obj.method()）
  - super.method() 调用
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SCAN_ROOT = ROOT / "src" / "backend" / "src" / "main" / "java"
RULE_ID = "logic/spring-self-invocation-transactional"

_BEAN_ANNOTATIONS = {
    "@Component", "@Service", "@Repository", "@Controller", "@RestController",
}

_PROXY_ANNOTATIONS = {
    "Transactional", "Async", "Cacheable", "CacheEvict", "CachePut",
}

_PROXY_ANNOTATION_RE = re.compile(
    r"@(" + "|".join(_PROXY_ANNOTATIONS) + r")\b"
)

_METHOD_DECL_RE = re.compile(
    r"^\s+(?:public|protected|private|static|\s)*\s+"
    r"(?:[\w<>\[\],\s\?]+\s+)"
    r"(\w+)\s*\("
)

_BEAN_ANNOTATION_RE = re.compile(
    r"@(Component|Service|Repository|Controller|RestController)\b"
)


def _is_spring_bean(content: str) -> bool:
    """文件是否包含 Spring bean 类注解。"""
    return bool(_BEAN_ANNOTATION_RE.search(content))


def _strip_comments(lines: list[str]) -> list[str]:
    """粗略抹除行注释和块注释内容，避免误判。"""
    out: list[str] = []
    in_block = False
    for line in lines:
        if in_block:
            end = line.find("*/")
            if end >= 0:
                in_block = False
                out.append(" " * (end + 2) + line[end + 2:])
            else:
                out.append("")
            continue
        start = line.find("/*")
        if start >= 0:
            end = line.find("*/", start + 2)
            if end >= 0:
                out.append(line[:start] + "  " + line[end + 2:])
            else:
                in_block = True
                out.append(line[:start])
            continue
        line_comment = line.find("//")
        if line_comment >= 0:
            out.append(line[:line_comment])
        else:
            out.append(line)
    return out


def _collect_methods(scrubbed_lines: list[str]) -> list[dict]:
    """收集方法声明及其注解、起止行号。

    返回列表，每项：
      {"name": str, "annotations": set[str], "start": int, "end": int}
    start/end 为行索引（0-based），end 为方法体结束的 } 行。
    """
    methods: list[dict] = []
    pending_annotations: set[str] = set()
    i = 0
    n = len(scrubbed_lines)

    while i < n:
        line = scrubbed_lines[i]
        ann_match = _PROXY_ANNOTATION_RE.search(line)
        if ann_match:
            pending_annotations.add(ann_match.group(1))
            i += 1
            continue

        method_match = _METHOD_DECL_RE.match(line)
        if method_match:
            method_name = method_match.group(1)
            if method_name in ("if", "for", "while", "switch", "catch", "return"):
                pending_annotations.clear()
                i += 1
                continue
            # static 方法跳过
            prefix = line[:method_match.start(1)]
            if "static" in prefix:
                pending_annotations.clear()
                i += 1
                continue

            body_start = i
            # 找方法体开始的 {
            brace_count = 0
            found_open = False
            j = i
            while j < n:
                for ch in scrubbed_lines[j]:
                    if ch == "{":
                        brace_count += 1
                        found_open = True
                    elif ch == "}":
                        brace_count -= 1
                if found_open and brace_count == 0:
                    break
                j += 1

            methods.append({
                "name": method_name,
                "annotations": pending_annotations.copy(),
                "start": body_start,
                "end": j,
            })
            pending_annotations.clear()
            i = j + 1
            continue

        # 非注解非方法声明行：重置 pending（避免类注解误挂到方法上）
        stripped = line.strip()
        if stripped and not stripped.startswith("@"):
            pending_annotations.clear()
        i += 1

    return methods

def _find_self_invocations(
    scrubbed_lines: list[str],
    method: dict,
    annotated_names: dict[str, str],
) -> list[tuple[int, str, str, str]]:
    """在方法体内查找对 annotated_names 中方法的 self-invocation。

    返回 [(line_idx, caller_name, target_name, annotation), ...]
    """
    hits: list[tuple[int, str, str, str]] = []
    caller = method["name"]
    start = method["start"] + 1
    end = method["end"]

    for i in range(start, min(end + 1, len(scrubbed_lines))):
        line = scrubbed_lines[i]
        for target_name, annotation in annotated_names.items():
            if target_name == caller:
                continue
            # 匹配 this.methodName( 或裸 methodName(
            patterns = [
                f"this.{target_name}(",
                f" {target_name}(",
                f"\t{target_name}(",
                f"({target_name}(",
                f"!{target_name}(",
                f"={target_name}(",
            ]
            found = False
            for pat in patterns:
                idx = line.find(pat)
                if idx >= 0:
                    if pat.startswith("this."):
                        found = True
                        break
                    # 裸调用：确保前面不是 . 或字母（排除 obj.method() 和 otherMethod()）
                    char_before_pos = idx
                    if char_before_pos > 0:
                        ch = line[char_before_pos - 1]
                        if ch == "." or ch.isalpha() or ch == "_":
                            continue
                    found = True
                    break

            if not found:
                # 行首直接调用
                stripped = line.lstrip()
                if stripped.startswith(f"{target_name}("):
                    found = True
                elif stripped.startswith(f"this.{target_name}("):
                    found = True

            if not found:
                continue

            # 排除 super.method()
            if f"super.{target_name}(" in line:
                continue

            hits.append((i, caller, target_name, annotation))

    return hits


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
        if not _is_spring_bean(raw):
            continue

        raw_lines = raw.splitlines()
        scrubbed = _strip_comments(raw_lines)
        methods = _collect_methods(scrubbed)

        annotated_names: dict[str, str] = {}
        for m in methods:
            if m["annotations"]:
                first_ann = next(iter(m["annotations"]))
                annotated_names[m["name"]] = first_ann

        if not annotated_names:
            continue

        for m in methods:
            if m["annotations"]:
                continue
            hits = _find_self_invocations(scrubbed, m, annotated_names)
            for line_idx, caller, target, annotation in hits:
                rel = path.relative_to(ROOT).as_posix()
                warnings.append(
                    f"[{RULE_ID}] {rel}:{line_idx + 1} — "
                    f"方法 {caller} 直接调用同类 @{annotation} 方法 {target}，"
                    f"Spring 代理不生效（self-invocation），"
                    f"建议抽独立 @Service 或使用 TransactionTemplate"
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
    return 0


if __name__ == "__main__":
    sys.exit(main())