#!/usr/bin/env python3
"""统一验证管道：build → lint-arch → test → verify，失败自动进入修复循环（最多 3 轮）。

重试策略：
- 若有自动修复命令：按 MAX_RETRIES 轮跑修复-重试循环。
- 一次命中"确定性失败"模式（javac 编译错、Python 语法错/模块缺失、tsc 编译错
  等，见 harness/lib/failure_classifier.py）立即 break，无需等第二轮指纹。
- 若连续两轮 stderr 指纹完全一致（环境缺依赖、静态违规等其他确定性失败），
  立即 break 跳过剩余重试，直接进入人工介入分支，避免噪音。
- 同一指纹的第二次及之后的输出只打印指纹引用，不再全量复制错误文本。
"""

import hashlib
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent.parent
# 让 harness/lib 可被 import（validate.py 位于 scripts/，与 harness/ 平级）
sys.path.insert(0, str(ROOT))
from harness.lib.failure_classifier import is_deterministic_failure  # noqa: E402

MAX_RETRIES = 3
# 连续相同指纹触发早退的阈值（critic-2026-04-22 缺陷 2：3 轮全跑是噪音，2 轮即可判定确定性失败）
DETERMINISTIC_FAIL_THRESHOLD = 2


def run(cmd: List[str], label: str, suppress_output: bool = False) -> tuple:
    """运行命令，返回 (成功与否, 合并输出)。suppress_output=True 时不回显（已知重复错误场景）。"""
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if output and not suppress_output:
        print(output)
    return result.returncode == 0, output


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()[:12]


def run_with_fix(step_cmd: List[str], fix_cmd: Optional[List[str]], label: str) -> bool:
    """
    运行一个步骤，失败时执行修复循环。

    提前退出条件（critic-2026-04-22 缺陷 2）：
    - 连续两轮指纹一致 → 判定为确定性失败，跳过剩余重试直接报人工介入。
    - 第二次及以后相同指纹只打印指纹引用，不复制全量 stderr。
    """
    last_fp: Optional[str] = None
    repeat_count = 0

    for attempt in range(1, MAX_RETRIES + 2):  # 1 次初跑 + 最多 3 轮修复
        # 若上一轮已产生指纹且本轮即将重复，第二次起抑制输出
        suppress = last_fp is not None and repeat_count >= 1
        ok, output = run(
            step_cmd,
            label if attempt == 1 else f"{label}（第 {attempt - 1} 轮重试）",
            suppress_output=suppress,
        )
        if ok:
            return True

        # 确定性失败优先早退：编译错、语法错、模块缺失等重试无意义的失败，
        # 一次命中即刻 break，不再等"连续两轮相同指纹"。
        det_reason = is_deterministic_failure(output)
        if det_reason is not None:
            remaining = MAX_RETRIES + 1 - attempt
            print(
                f"\n[verify] 检测到确定性失败（reason={det_reason}），"
                f"跳过剩余重试 {remaining}/{MAX_RETRIES}。"
            )
            print(f"\n🛑 [{label}] 确定性失败，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   错误摘要:\n{_indent(output)}")
            return False

        fp = fingerprint(output)

        if fp == last_fp:
            repeat_count += 1
            print(f"\n[retry-skip-candidate] 指纹 {fp} 与上一轮一致（连续 {repeat_count} 轮）")
        else:
            repeat_count = 1
            last_fp = fp

        # 确定性失败早退：连续两轮相同指纹立即 break
        if repeat_count >= DETERMINISTIC_FAIL_THRESHOLD:
            remaining = MAX_RETRIES + 1 - attempt
            print(
                f"\n[retry-skip] 检测到确定性失败（指纹 {fp}），"
                f"跳过剩余重试 {remaining}/{MAX_RETRIES}"
            )
            print(f"\n🛑 [{label}] 确定性失败，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   错误摘要（仅首轮完整保留）:\n{_indent(output)}")
            return False

        if attempt > MAX_RETRIES:
            print(f"\n🛑 [{label}] 已跑满 {MAX_RETRIES} 轮修复仍未通过，需要人工介入：")
            print(f"   运行命令: {' '.join(step_cmd)}")
            print(f"   最终错误摘要:\n{_indent(output)}")
            return False

        if fix_cmd:
            round_num = attempt  # 第几轮修复
            print(f"\n🔧 [{label}] 第 {round_num} 轮修复：{' '.join(fix_cmd)}")
            subprocess.run(fix_cmd, cwd=ROOT)
        else:
            print(f"\n⚠️  [{label}] 无自动修复命令，跳过修复直接重试（若下一轮指纹重复将早退）")

    return False


def _indent(text: str, prefix: str = "     ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _backend_available(host: str = "127.0.0.1", port: int = 8088, timeout: float = 2.0) -> bool:
    """TCP 探测后端是否在监听。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def main() -> int:
    # (验证命令, 修复命令或 None, 标签)
    steps = [
        (["make", "build"],     None,                 "build     — 代码能否编译"),
        (["make", "lint-arch"], ["make", "fix-arch"],  "lint-arch — 架构和质量合规"),
        (["make", "test"],      None,                 "test      — 单元/集成测试"),
        (["make", "verify"],    None,                 "verify    — 端到端功能验证"),
    ]

    for step_cmd, fix_cmd, label in steps:
        if "verify" in label and not _backend_available():
            print("\n❌ 后端未在 127.0.0.1:8088 监听，无法执行业务路径验证。")
            print("   请先启动后端（参考 docs/DEVELOPMENT.md → 验证前置条件）：")
            print("     cd src/backend && mvn spring-boot:run")
            print("   或仅跑不依赖后端的子集：make build && make lint-arch && make test")
            return 1
        if not run_with_fix(step_cmd, fix_cmd, label):
            print(f"\n❌ 管道终止于 [{label.split('—')[0].strip()}]")
            return 1

    print("\n✅ 所有验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())