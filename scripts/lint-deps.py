#!/usr/bin/env python3
"""层级依赖检查 — 确保各层只能向下依赖，Layer 4 模块间不得互相引用。"""

import re
import sys
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).parent.parent

_JAVA_GROUP = "com.xptsqas"
_BACKEND_JAVA_PREFIX = "src/backend/src/main/java/com/xptsqas/"
_FRONTEND_SRC_PREFIX = "src/frontend/src/"

# (目录前缀, 层号) — 越早匹配越优先，子目录必须在父目录前面
LAYER_PREFIXES: list[tuple[str, int]] = [
    ("types",              0),
    ("utils",              1),
    ("config",             2),
    ("core/services/impl", 3),
    ("core/services",      3),
    ("core/repository",    3),
    ("core",               3),
    ("api",                4),
    ("cli",                4),
    ("ui",                 4),
]

LAYER4_MODULES = {"api", "cli", "ui"}

_LAYER_NAMES = r"types|utils|config|core(?:/services)?|api|cli|ui"
IMPORT_RE = re.compile(
    rf"""(?:
        import\s+{re.escape(_JAVA_GROUP)}\.({_LAYER_NAMES})\.   # Java 项目内部包 import
        |
        from\s+['"](?:@/|(?:\.\.?/)+)({_LAYER_NAMES})[/'"]  # JS/TS/Vue path import
    )""",
    re.VERBOSE | re.MULTILINE,
)

SKIP_DIRS = {"node_modules", "dist", "build", "target", ".git", "harness", "scripts"}
EXTENSIONS = {".java", ".ts", ".js", ".vue"}


def layer_of(rel_path: str) -> Optional[Tuple[int, str]]:
    """(层号, 顶层目录名)，匹配不到返回 None。"""
    norm = rel_path
    for pfx in (_BACKEND_JAVA_PREFIX, _FRONTEND_SRC_PREFIX):
        if rel_path.startswith(pfx):
            norm = rel_path[len(pfx):]
            break
    for prefix, num in LAYER_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return num, norm.split("/")[0]
    return None


def check_file(path: Path) -> list:
    rel = path.relative_to(ROOT).as_posix()
    src = layer_of(rel)
    if src is None:
        return []

    src_layer, src_module = src

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    violations: list[str] = []
    for m in IMPORT_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").split("/")[0]  # 'core/services' → 'core'
        dst = layer_of(raw + "/x")
        if dst is None:
            continue
        dst_layer, dst_module = dst

        if dst_layer > src_layer:
            violations.append(
                f"{rel}\n"
                f"  规则: 低层禁止依赖高层（Layer {src_layer} → Layer {dst_layer}）\n"
                f"  问题: {src_module}/ 属于 Layer {src_layer}，不得引用属于 Layer {dst_layer} 的 {dst_module}/，"
                f"否则底层模块会被上层细节污染，破坏单向依赖结构\n"
                f"  修复: 将此逻辑上移到 Layer {dst_layer} 或更高层，"
                f"或通过 Layer {src_layer} 的接口（interface）抽象后由高层注入\n"
            )
        elif src_layer == 4 and dst_layer == 4 and src_module != dst_module:
            violations.append(
                f"{rel}\n"
                f"  规则: Layer 4 各模块（api / cli / ui）不得互相引用\n"
                f"  问题: {src_module}/ 引用了同级模块 {dst_module}/，"
                f"导致接口层之间产生横向耦合，任一模块变更都可能影响其他接口层\n"
                f"  修复: 将共用逻辑下沉到 core/services/（Layer 3）或 utils/（Layer 1），"
                f"再由 {src_module}/ 和 {dst_module}/ 各自引用\n"
            )

    return violations


def main() -> int:
    violations: list[str] = []

    for ext in EXTENSIONS:
        for path in ROOT.rglob(f"*{ext}"):
            if any(p in SKIP_DIRS for p in path.parts):
                continue
            violations.extend(check_file(path))

    if violations:
        print("❌ 层级依赖违规：")
        for v in violations:
            print(f"  {v}")
        return 1

    print("✅ 层级依赖检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())