#!/usr/bin/env python3
"""PreToolUse hook：阻止 coordinator 直接编辑 src/ 文件，强制走委派链路。

机制：
- enter/leave 子命令维护 .claude/.subagent-stack JSON 栈（带 TTL 自动过期）
- check 子命令在 Edit/Write 时检查栈深度，depth > 0 放行，否则阻断

Hook 配置：
- PreToolUse(Agent):  python3 harness/bin/subagent_gate.py enter
- PostToolUse(Agent): python3 harness/bin/subagent_gate.py leave
- PreToolUse(Edit):   python3 harness/bin/subagent_gate.py check
- PreToolUse(Write):  python3 harness/bin/subagent_gate.py check
"""
import fcntl
import json
import os
import sys
import time

STACK_FILE = os.path.join(".claude", ".subagent-stack")
LOCK_FILE = os.path.join(".claude", ".subagent-lock")
LOG_FILE = os.path.join(".claude", ".subagent-gate.log")
OLD_DEPTH_FILE = os.path.join(".claude", ".subagent-depth")
GUARDED_PREFIXES = ("src/",)
TTL = int(os.environ.get("GATE_TTL", 300))
LOG_MAX_BYTES = 100_000
_FAIL_CODE = 0


def _read_stack():
    try:
        data = json.load(open(STACK_FILE))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return []


def _write_stack(stack):
    os.makedirs(os.path.dirname(STACK_FILE), exist_ok=True)
    if not stack:
        try:
            os.remove(STACK_FILE)
        except FileNotFoundError:
            pass
    else:
        with open(STACK_FILE, "w") as f:
            json.dump(stack, f)


def _purge_expired(stack):
    now = time.time()
    return [e for e in stack if now - e.get("ts", 0) < TTL]


def _locked_op(fn):
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fn()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _to_relative(file_path):
    if os.path.isabs(file_path):
        try:
            return os.path.relpath(file_path, os.getcwd())
        except ValueError:
            return file_path
    return file_path


def _log(cmd, depth, result, file_path="", agent_type=""):
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
        if file_path:
            entry["file"] = file_path
        if agent_type:
            entry["agent_type"] = agent_type
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _migrate_old_file():
    if os.path.exists(OLD_DEPTH_FILE):
        try:
            old_val = open(OLD_DEPTH_FILE).read().strip()
            os.remove(OLD_DEPTH_FILE)
            _log("migrate", old_val, "deleted-old-depth-file")
        except Exception:
            pass


def cmd_enter():
    try:
        payload = json.load(sys.stdin)
        agent_type = payload.get("tool_input", {}).get("subagent_type", "unknown")
    except Exception:
        agent_type = "unknown"

    def op():
        stack = _purge_expired(_read_stack())
        stack.append({"ts": time.time(), "pid": os.getpid()})
        _write_stack(stack)
        return len(stack)

    depth = _locked_op(op)
    _log("enter", depth, "ok", agent_type=agent_type)
    sys.exit(0)


def cmd_leave():
    try:
        payload = json.load(sys.stdin)
        agent_type = payload.get("tool_input", {}).get("subagent_type", "—")
    except Exception:
        agent_type = "—"

    def op():
        stack = _purge_expired(_read_stack())
        if stack:
            stack.pop()
        _write_stack(stack)
        return len(stack)

    depth = _locked_op(op)
    _log("leave", depth, "ok", agent_type=agent_type)
    sys.exit(0)


def cmd_check():
    global _FAIL_CODE
    _FAIL_CODE = 2

    try:
        payload = json.load(sys.stdin)
    except Exception:
        _log("check", "?", "BLOCK:stdin-fail")
        sys.exit(2)

    file_path = payload.get("tool_input", {}).get("file_path", "")
    rel = _to_relative(file_path)

    if not any(rel.startswith(p) for p in GUARDED_PREFIXES):
        sys.exit(0)

    def op():
        stack = _purge_expired(_read_stack())
        _write_stack(stack)
        return len(stack)

    depth = _locked_op(op)

    if depth > 0:
        _log("check", depth, f"ALLOW:{rel}", rel)
        sys.exit(0)

    _log("check", 0, f"BLOCK:{rel}", rel)
    sys.stderr.write(
        "❌ Coordinator 直接编辑 src/ 违规\n\n"
        f"文件：{rel}\n\n"
        "Coordinator 严禁直接调用 Edit/Write 修改 src/ 下的文件。\n"
        "请通过 Agent 工具委派给 codex-implementer 或 executor-code。\n\n"
        "规则来源：.claude/roles/coordinator.md\n"
        "  - 默认 → codex-implementer\n"
        "  - 兜底 → executor-code（需声明兜底原因）\n"
    )
    sys.exit(2)


def main():
    _migrate_old_file()
    if len(sys.argv) < 2:
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "enter":
        cmd_enter()
    elif cmd == "leave":
        cmd_leave()
    elif cmd == "check":
        cmd_check()
    else:
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        sys.exit(_FAIL_CODE)