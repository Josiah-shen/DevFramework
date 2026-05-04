#!/usr/bin/env python3
"""PreToolUse hook：拦截 coordinator 直接委派 executor-code 的违规调用。

规则（coordinator.md:181-188）：
- 默认 → codex-implementer
- 兜底 → executor-code，prompt 必须显式声明：
    "兜底原因 X：<理由>"  其中 X ∈ {a, b, c}
  或设置 CODEX_DISABLE=1 全局回退。

收紧策略（避免误放行）：
- 不再识别 ".claude/"、"harness/"、"小改"、"<30 行"、"codex 不可用" 等模糊关键词
- 必须显式声明 "兜底原因" 字样，否则一律阻断
"""
import json
import os
import re
import sys
import time

FALLBACK_KEYWORDS = [
    r"兜底原因",
    r"兜底：",
    r"兜底:",
    r"CODEX_DISABLE",
]

TRACE_DIR = os.path.join("harness", "trace", "codex")
LOG_FILE = os.path.join(".claude", ".subagent-gate.log")
LOG_MAX_BYTES = 100_000


def _log(cmd, depth, result, agent_type=""):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        try:
            if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
                with open(LOG_FILE, "r") as f:
                    lines = f.readlines()
                with open(LOG_FILE, "w") as f:
                    f.writelines(lines[len(lines) // 2:])
        except FileNotFoundError:
            pass
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cmd": cmd,
            "pid": os.getpid(),
            "depth": depth,
            "result": result,
        }
        if agent_type:
            entry["agent_type"] = agent_type
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _log("route", "-", "route:SKIP:stdin-fail", "")
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    agent_type = tool_input.get("subagent_type", "")
    if agent_type != "executor-code":
        _log("route", "-", f"route:SKIP:{agent_type}", agent_type)
        sys.exit(0)

    prompt = tool_input.get("prompt", "")
    if any(re.search(kw, prompt) for kw in FALLBACK_KEYWORDS):
        _log("route", "-", "route:ALLOW:fallback-keyword", "executor-code")
        sys.exit(0)

    _log("route", "-", "route:BLOCK:no-fallback", "executor-code")
    sys.stderr.write(
        "❌ 编码委派路由违规\n\n"
        "本次调用了 executor-code，但 prompt 未显式声明兜底原因。\n\n"
        "Coordinator 规则（.claude/roles/coordinator.md:181-188）：\n"
        "  - 默认 → codex-implementer\n"
        "  - 兜底 → executor-code，需在 prompt 中显式写明：\n"
        "      \"兜底原因 X：<理由>\"  其中 X ∈ {a, b, c}：\n"
        "      a. codex-implementer 已报告\"阻塞：codex 不可用 / 超时 / 错误\"\n"
        "      b. 任务涉及 .claude/、harness/ 自身\n"
        "      c. 单文件 < 30 行的小改\n\n"
        "或设置 CODEX_DISABLE=1 全局回退。\n\n"
        "请改派 codex-implementer，或补全 \"兜底原因 X：...\" 声明。\n"
    )
    sys.exit(2)


def post_check():
    """PostToolUse hook：codex-implementer 完成后检查是否产生了 trace 文件。"""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    agent_type = payload.get("tool_input", {}).get("subagent_type", "")
    if agent_type != "codex-implementer":
        sys.exit(0)

    today = time.strftime("%Y%m%d")
    try:
        traces = [f for f in os.listdir(TRACE_DIR) if f.startswith(today)]
    except FileNotFoundError:
        traces = []

    if not traces:
        _log("post", "-", "WARN:no-codex-trace", "codex-implementer")
        sys.stderr.write(
            "⚠️ codex-implementer 完成但未产生 codex trace 文件\n"
            f"检查目录：{TRACE_DIR}（今日前缀 {today}）\n"
            "可能原因：codex.sh 未被调用，或调用失败未生成日志。\n"
        )
    sys.exit(0)

if __name__ == "__main__":
    try:
        if "--post" in sys.argv:
            post_check()
        else:
            main()
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)