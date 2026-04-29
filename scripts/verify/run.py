#!/usr/bin/env python3
"""verify 调度器：顺序运行各检查，汇总结果。

--scope 语义
------------
- 未传 --scope：保持全仓扫描 + ❌ 硬失败的历史行为（向后兼容）。
- 传 --scope <path>[,<path>]...（可重复）或 --scope-file <file>：
    * 对 scope 内文件的违规：❌ 硬失败（exit 1）。
    * 对 scope 外文件的违规：输出到独立 "[pre-existing debt]" 段落，降级为 ⚠️，
      不计入失败，**不阻断管道**。
    * 目的：让 "任务 verify" 只对任务范围内改动负责，历史遗留违规只做提示。

路径匹配规则
------------
- scope 中的条目按仓库相对 POSIX 路径匹配（如 `src/backend/src/main/java/.../Foo.java`）。
- 目录前缀支持：若 scope 条目以 "/" 结尾或对应一个目录，视为"该目录下所有文件"均在 scope 内。
- 规则仅对 "代码规范"（style）检查生效；其他检查（架构/接口/业务路径）不受 scope 影响。
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from checks import arch, style, api, e2e  # noqa: E402


def _load_check_scope():
    """按文件名加载 check-scope.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_scope", Path(__file__).parent / "check-scope.py"
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_closeable():
    """按文件名加载 check-closeable-try-with-resources.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_closeable",
        Path(__file__).parent / "check-closeable-try-with-resources.py",
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_spring_self_invocation():
    """按文件名加载 check-spring-self-invocation.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_spring_self_invocation",
        Path(__file__).parent / "check-spring-self-invocation.py",
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_doc_sync():
    """按文件名加载 check-doc-sync.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_doc_sync",
        Path(__file__).parent / "check-doc-sync.py",
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_exec_plan_paths():
    """按文件名加载 check-exec-plan-paths.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_exec_plan_paths",
        Path(__file__).parent / "check-exec-plan-paths.py",
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_mockito_inline():
    """按文件名加载 check-mockito-inline-concrete.py（连字符名不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_mockito_inline",
        Path(__file__).parent / "check-mockito-inline-concrete.py",
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# key, label, fn, supports_scope
CHECKS = [
    ("arch", "架构合规", arch.check, False),
    ("style", "代码规范", style.check, True),
    ("api", "接口存活", api.check, True),
    ("e2e", "业务路径", e2e.check, True),
]


def _parse_scope(args_scope, scope_file):
    """将 CLI 的 scope 参数规范化为仓库相对 POSIX 路径集合；None 表示未指定。"""
    if not args_scope and not scope_file:
        return None
    items: list[str] = []
    if args_scope:
        for entry in args_scope:
            # 支持逗号分隔
            items.extend([p.strip() for p in entry.split(",") if p.strip()])
    if scope_file:
        text = Path(scope_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                items.append(line)
    # 统一为 POSIX 风格（去掉前导 ./，保留结尾 /，便于目录前缀匹配）
    normalized: set[str] = set()
    for p in items:
        p = p.replace("\\", "/")
        if p.startswith("./"):
            p = p[2:]
        normalized.add(p)
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="verify 调度器")
    parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="任务允许硬失败的文件集合（可多次指定或逗号分隔）；范围外历史违规降级为 ⚠️。",
    )
    parser.add_argument(
        "--scope-file",
        default=None,
        help="从文件读取 scope 列表（每行一个路径，# 开头为注释）。",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help="（可选）关联 exec-plan slug，仅用于日志输出，不影响行为。",
    )
    parser.add_argument(
        "--checks",
        default=None,
        help="逗号分隔的检查项：arch,style,api,e2e。默认全部。",
    )
    args = parser.parse_args()

    # 环境变量回退：executor.py verify 通过 env 注入（因为中间有 make 间接调用）。
    # CLI 显式参数优先级高于环境变量。
    env_scope = os.environ.get("HARNESS_VERIFY_SCOPE")
    if not args.scope and not args.scope_file and env_scope:
        args.scope = [env_scope]
    env_slug = os.environ.get("HARNESS_SLUG")
    if not args.slug and env_slug:
        args.slug = env_slug

    scope = _parse_scope(args.scope, args.scope_file)
    selected_checks = None
    if args.checks:
        selected_checks = {item.strip() for item in args.checks.split(",") if item.strip()}
    if scope is not None:
        slug_hint = f"（slug={args.slug}）" if args.slug else ""
        print(f"ℹ️  verify 运行于 --scope 模式{slug_hint}：范围内 {len(scope)} 条路径硬失败，范围外降级为 ⚠️")

    # 规则 boundary/worktree-scope-drift（warning 阶段，不阻塞）
    if args.slug:
        mod = _load_check_scope()
        if mod is not None:
            warns = mod.check(slug=args.slug)
            if warns:
                print(f"⚠️  [{mod.RULE_ID}] {len(warns)} 条越界改动（warning，不阻塞）：")
                for w in warns:
                    print(f"     {w}")

    # 规则 performance/closeable-try-with-resources（warning 阶段，不阻塞）
    closeable_mod = _load_check_closeable()
    if closeable_mod is not None:
        closeable_warns = closeable_mod.check()
        if closeable_warns:
            print(
                f"⚠️  [{closeable_mod.RULE_ID}] {len(closeable_warns)} 条资源未 try-with-resources（warning，不阻塞）："
            )
            for w in closeable_warns:
                print(f"     {w}")

    # 规则 logic/spring-self-invocation-transactional（warning 阶段，不阻塞）
    self_inv_mod = _load_check_spring_self_invocation()
    if self_inv_mod is not None:
        self_inv_warns = self_inv_mod.check()
        if self_inv_warns:
            print(
                f"⚠️  [{self_inv_mod.RULE_ID}] {len(self_inv_warns)} 条 self-invocation 代理失效（warning，不阻塞）："
            )
            for w in self_inv_warns:
                print(f"     {w}")

    # 规则 process/requirement-doc-sync（warning 阶段，不阻塞）
    doc_sync_mod = _load_check_doc_sync()
    if doc_sync_mod is not None:
        doc_sync_warns = doc_sync_mod.check(scope=scope, slug=args.slug)
        if doc_sync_warns:
            print(f"⚠️  [doc-sync] {len(doc_sync_warns)} 条文档同步告警（warning，不阻塞）：")
            for w in doc_sync_warns:
                print(f"     {w}")

    # 规则 process/exec-plan-path-existence（warning 阶段，不阻塞）
    exec_plan_paths_mod = _load_check_exec_plan_paths()
    if exec_plan_paths_mod is not None:
        exec_plan_warns = exec_plan_paths_mod.check(slug=args.slug)
        if exec_plan_warns:
            print(
                f"⚠️  [{exec_plan_paths_mod.RULE_ID}] {len(exec_plan_warns)} 条路径引用告警（warning，不阻塞）："
            )
            for w in exec_plan_warns:
                print(f"     {w}")

    # 规则 testing/mockito-inline-on-concrete-class（warning 阶段，不阻塞）
    mockito_mod = _load_check_mockito_inline()
    if mockito_mod is not None:
        mockito_warns = mockito_mod.check()
        if mockito_warns:
            print(
                f"⚠️  [{mockito_mod.RULE_ID}] {len(mockito_warns)} 条具体类 inline mock（warning，不阻塞）："
            )
            for w in mockito_warns:
                print(f"     {w}")

    all_ok = True
    for key, label, fn, supports_scope in CHECKS:
        if selected_checks is not None and key not in selected_checks:
            continue
        if supports_scope and scope is not None:
            result = fn(scope=scope)
            if len(result) == 3:
                ok, msgs, debt = result
            else:
                ok, msgs = result
                debt = []
        else:
            # 无 scope 调用：所有 check 返回 2 元组 (ok, msgs)，debt 为空
            ok, msgs = fn()
            debt = []
        icon = "✅" if ok else "❌"
        print(f"{icon} {label}")
        for m in msgs:
            print(f"   {m}")
        if debt:
            print(f"   [pre-existing debt] {len(debt)} 条范围外历史违规（不阻断）：")
            for m in debt:
                print(f"     ⚠️  {m}")
        if not ok:
            all_ok = False

    print()
    if not all_ok:
        print("❌ verify 未全部通过")
        return 1
    print("✅ 所有 verify 检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())